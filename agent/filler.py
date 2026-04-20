"""Filler generators for the channels that pipecat's main response stream
can NOT cover natively:

  - **progress**: periodic narration during a SLOW tool call. The LLM is
    blocked waiting for the tool result, so it can't stream
    "still looking..." itself — we have to fabricate it.
  - **backchannel**: brief listener-acks ("mm-hmm", "yeah") fired DURING
    the user's turn. The agent isn't producing a response at all in this
    moment, so again pipecat's stream can't help.

The OPENING preamble before a tool call is no longer in this file —
it's prompt-driven now. See `tool_use_block()` below; it gets appended
to every persona's system prompt and instructs the LLM to emit 2-12
words inline before each tool call. Pipecat's OpenAILLMService streams
those tokens to TTS before running the function call. One LLM, one
source of truth, no race conditions.

Backend-aware rendering still applies — Fish gets `[softly]`/`[hmm]` tags;
Kokoro gets plain text.
"""

from __future__ import annotations

import asyncio
import collections
import logging
import os
from dataclasses import dataclass
from enum import Enum

from openai import AsyncOpenAI

logger = logging.getLogger(__name__)


class Verbosity(str, Enum):
    SILENT = "silent"
    BRIEF = "brief"
    NARRATED = "narrated"
    CHATTY = "chatty"


class Latency(str, Enum):
    FAST = "fast"      # <500ms — no preamble, no progress
    MEDIUM = "medium"  # 0.5-3s — preamble (LLM-emitted), no progress
    SLOW = "slow"      # 3s+ — preamble + periodic progress (this file)


DEFAULT_VERBOSITY = Verbosity(os.environ.get("VERBOSITY", Verbosity.BRIEF.value).lower())


# ---------------------------------------------------------------------------
# Per-backend prompting style — used by both `tool_use_block` (for the
# inline preamble the LLM emits) and `_generate` (for progress + backchannel).
# ---------------------------------------------------------------------------

_FISH_STYLE = """\
You may use Fish Audio S2-Pro inline prosody tags to sound natural.
Preferred tags for thinking-out-loud:
  [softly], [pause], [hmm], [um], [thinking], [whisper]
Use them sparingly — one or two per phrase, at phrase starts or pauses.
Example: "[softly] hmm, [pause] let me check that for you"
"""

_KOKORO_STYLE = """\
Use plain text only. Do NOT use bracketed tags, SSML, or emoji —
they get spoken as literal sounds. Convey hesitation with words alone:
"hmm, let me see" / "one sec, checking" / "okay, hold on".
"""


def _backend_style(tts_backend: str) -> str:
    return _FISH_STYLE if tts_backend == "fish" else _KOKORO_STYLE


# ---------------------------------------------------------------------------
# Verbosity → preamble length / tone in the system prompt
# ---------------------------------------------------------------------------

_PREAMBLE_LENGTH_BY_VERBOSITY: dict[Verbosity, str | None] = {
    Verbosity.SILENT: None,  # no preamble at all
    Verbosity.BRIEF: "2 to 4 words. Casual, low energy. Like 'one sec' or 'let me see'.",
    Verbosity.NARRATED: "4 to 8 words. Warm, natural. May reference the topic abstractly ('let me look that up').",
    Verbosity.CHATTY: "Up to 12 words. Slightly expressive. May add a tiny acknowledgement of the topic.",
}


def tool_use_block(verbosity: Verbosity, tts_backend: str) -> str:
    """Returns the TOOL USE section appended to every persona's system
    prompt. Encodes the inline preamble pattern: speak briefly BEFORE
    every tool call, in the same response. Pipecat streams those tokens
    to TTS before running the tool."""
    if verbosity is Verbosity.SILENT:
        return (
            "## TOOL USE\n"
            "When you call a tool, do NOT say anything before the call. "
            "Make the tool call silently. Speak only the answer once the "
            "tool returns."
        )
    length = _PREAMBLE_LENGTH_BY_VERBOSITY[verbosity]
    style = _backend_style(tts_backend).strip()
    return f"""\
## TOOL USE — speak BEFORE every tool call

Whenever you call a tool, ALWAYS first emit one short preamble line in
the response, THEN call the tool. The preamble is spoken aloud
immediately so the user knows you heard them and are working.

Preamble length / tone: {length}

CRITICAL — what the preamble must NOT do:
  - never include a fact, name, number, date, or detail from the answer.
    The actual answer comes after the tool returns.
  - never restate the user's question.
  - never claim what you found or will find.
The preamble is ONLY about acknowledging that you're checking.

Examples (do NOT copy verbatim — write your own each time):
  "hmm, let me check"
  "one sec"
  "okay, looking that up"
  "alright, give me a moment"

{style}
"""


# ---------------------------------------------------------------------------
# Generator — for progress (during SLOW tool) + backchannel (during user)
# ---------------------------------------------------------------------------

_PROGRESS_STYLE = (
    "Even shorter and softer than a normal acknowledgement — 2 to 5 words. "
    "Feels like an under-breath continuation, not a new announcement. "
    "Never repeat any previous progress line."
)

_BACKCHANNEL_STYLE = (
    "ONE backchannel — a tiny listener acknowledgement that signals 'I'm "
    "still here, keep going' WHILE the user is talking. ONE or two tokens "
    "max. Examples (don't reuse exactly): mm-hmm, yeah, right, mhm, ok, "
    "got it, sure, uh-huh. Quiet, soft, low energy. Never a sentence."
)


@dataclass
class Settings:
    verbosity: Verbosity = DEFAULT_VERBOSITY
    progress_after_secs: float = 3.0
    progress_interval_secs: float = 4.0
    recency_window: int = 6
    max_gen_tokens: int = 30
    temperature: float = 0.9
    timeout_secs: float = 2.5


class _Recent:
    def __init__(self, window: int = 6):
        self._buf: collections.deque[str] = collections.deque(maxlen=window)

    def remember(self, phrase: str) -> None:
        if phrase and phrase.strip():
            self._buf.append(phrase.strip())

    def hint(self) -> str:
        if not self._buf:
            return ""
        return "Recent lines we've already said this session (AVOID repeating):\n" + "\n".join(
            f"  - {p}" for p in self._buf
        )


class FillerGenerator:
    """Generates progress narration + backchannels via the local routing LLM."""

    def __init__(
        self,
        *,
        llm_url: str,
        model: str,
        api_key: str = "not-needed",
        settings: Settings | None = None,
    ):
        self._client = AsyncOpenAI(api_key=api_key, base_url=llm_url)
        self._model = model
        self._settings = settings or Settings()
        self._recent = _Recent(self._settings.recency_window)

    @property
    def settings(self) -> Settings:
        return self._settings

    async def progress(
        self,
        *,
        tool_name: str,
        user_utterance: str | None,
        tts_backend: str,
    ) -> str | None:
        """Periodic 'still working' line for a SLOW in-flight tool."""
        if self._settings.verbosity in (Verbosity.SILENT, Verbosity.BRIEF):
            return None
        phrase = await self._generate(
            kind="progress",
            length_style=_PROGRESS_STYLE,
            tool_name=tool_name,
            user_utterance=user_utterance,
            tts_backend=tts_backend,
        )
        if phrase:
            self._recent.remember(phrase)
        return phrase

    async def backchannel(self, *, tts_backend: str) -> str | None:
        """One brief listener-ack while the user is talking."""
        if self._settings.verbosity is Verbosity.SILENT:
            return None
        phrase = await self._generate(
            kind="backchannel",
            length_style=_BACKCHANNEL_STYLE,
            tool_name="(listening)",
            user_utterance=None,
            tts_backend=tts_backend,
        )
        if not phrase:
            return None
        # Wrap Fish output in [softly] for unobtrusive delivery.
        if tts_backend == "fish" and not phrase.startswith("["):
            phrase = f"[softly] {phrase}"
        self._recent.remember(phrase)
        return phrase

    async def _generate(
        self,
        *,
        kind: str,
        length_style: str,
        tool_name: str,
        user_utterance: str | None,
        tts_backend: str,
    ) -> str | None:
        system = (
            f"You generate ONE '{kind}' line for a voice agent. The line "
            "will be spoken immediately. Output ONLY the line, no quotes, "
            "no markdown, no explanation."
        )
        user_parts = [
            f"Context: {tool_name}",
            f"Length / tone: {length_style}",
            "",
            _backend_style(tts_backend).strip(),
        ]
        if user_utterance:
            user_parts.insert(1, f"User said: {user_utterance.strip()[:200]}")
        recent = self._recent.hint()
        if recent:
            user_parts.append("")
            user_parts.append(recent)
        user_parts.append("")
        user_parts.append(f"Output the {kind} line and nothing else.")
        user = "\n".join(user_parts)

        try:
            r = await asyncio.wait_for(
                self._client.chat.completions.create(
                    model=self._model,
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    max_tokens=self._settings.max_gen_tokens,
                    temperature=self._settings.temperature,
                    extra_body={"chat_template_kwargs": {"enable_thinking": False}},
                ),
                timeout=self._settings.timeout_secs,
            )
        except asyncio.TimeoutError:
            logger.warning(f"[filler:gen] {kind} timeout (> {self._settings.timeout_secs}s)")
            return None
        except Exception as e:
            logger.warning(f"[filler:gen] {kind} failed: {e}")
            return None

        text = (r.choices[0].message.content or "").strip()
        if text and text[0] in "\"'" and text[-1] in "\"'":
            text = text[1:-1].strip()
        if not text:
            return None
        if tts_backend != "fish" and "[" in text:
            import re
            text = re.sub(r"\[[^\]]+\]", "", text).strip(" ,.;:")
        return text or None
