"""Generative, backend-aware filler — the "thinking out loud" track.

Replaces the old hardcoded phrase pools. Every filler line is generated
fresh by a small local LLM, conditioned on:

  - the user's last utterance (what are we checking?)
  - the tool being dispatched (how should I acknowledge?)
  - the TTS backend (tag-style prosody for Fish; plain text for Kokoro)
  - recent fillers we've said this session (to avoid repetition)
  - the requested verbosity level (silent / brief / narrated / chatty)

Why generative: a rotating phrase pool saturates in ~10 turns and starts
sounding like IVR. Generating per-turn keeps the filler novel and, more
importantly, grounds it in the actual user query — see Sesame + OpenAI
Realtime Prompting Guide + ElevenLabs Conversational AI.

Why backend-aware: Fish Audio S2-Pro supports 15k+ inline prosody tags
(`[softly]`, `[pause]`, `[hmm]`, `[thinking]`). Using them turns a filler
from "announcement" into "actual hesitation." Kokoro has no equivalent —
bracketed tags get spoken as literal `[softly]` sounds, which is worse
than plain text. So we render differently per backend.

Latency tiers control when filler fires at all:

  FAST   — tools that typically return in <500ms (calculator, datetime).
           Emit nothing. Filler would arrive AFTER the answer.
  MEDIUM — 0.5-3s tools (web_search, deep_research). One opening line.
  SLOW   — 3s+ tools (slow_research, long A2A calls). Opening + periodic
           progress frames.
"""

from __future__ import annotations

import asyncio
import collections
import logging
import os
from dataclasses import dataclass, field
from enum import Enum

from openai import AsyncOpenAI

logger = logging.getLogger(__name__)


class Verbosity(str, Enum):
    SILENT = "silent"
    BRIEF = "brief"
    NARRATED = "narrated"
    CHATTY = "chatty"


class Latency(str, Enum):
    FAST = "fast"      # <500ms — no filler
    MEDIUM = "medium"  # 0.5-3s — opening only
    SLOW = "slow"      # 3s+ — opening + progress


DEFAULT_VERBOSITY = Verbosity(os.environ.get("VERBOSITY", Verbosity.BRIEF.value).lower())


# ---------------------------------------------------------------------------
# Per-backend prompting — Fish gets tags; Kokoro gets plain.
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


# ---------------------------------------------------------------------------
# Verbosity → length + tone shaping
# ---------------------------------------------------------------------------

_VERBOSITY_STYLE = {
    Verbosity.SILENT: None,  # no filler fires at all
    Verbosity.BRIEF: (
        "Extremely short — 2 to 4 words. Conversational. Low energy, "
        "like you're half-thinking. Never announce."
    ),
    Verbosity.NARRATED: (
        "Short — 3 to 8 words. Warm, natural. One beat of hesitation. "
        "Ground it in the user's topic if obvious."
    ),
    Verbosity.CHATTY: (
        "Up to 12 words. Natural, slightly expressive. Can include a "
        "light observation or curiosity about the topic."
    ),
}

_PROGRESS_STYLE = (
    "Even shorter and softer than the opening — 2 to 5 words. "
    "Feels like an under-breath continuation, not a new announcement. "
    "Never repeat a previous filler or progress line."
)

_BACKCHANNEL_STYLE = (
    "ONE backchannel — a tiny listener acknowledgement that signals 'I'm "
    "still here, keep going' WHILE the user is talking. ONE or two tokens "
    "max. Examples (don't reuse exactly): mm-hmm, yeah, right, mhm, ok, "
    "got it, sure, uh-huh. Quiet, soft, low energy. Never a sentence."
)


# ---------------------------------------------------------------------------
# Settings + state
# ---------------------------------------------------------------------------

@dataclass
class Settings:
    verbosity: Verbosity = DEFAULT_VERBOSITY
    progress_after_secs: float = 3.0
    progress_interval_secs: float = 4.0
    recency_window: int = 6          # remember last N fillers per session
    max_gen_tokens: int = 30
    temperature: float = 0.9          # higher = more variety
    timeout_secs: float = 2.5         # if LLM is slow, give up — pipeline keeps going


class RecentFillers:
    """Bounded ring of recent filler texts, joined and passed into the
    generator prompt to discourage repetition."""

    def __init__(self, window: int = 6):
        self._buf: collections.deque[str] = collections.deque(maxlen=window)

    def remember(self, phrase: str) -> None:
        phrase = (phrase or "").strip()
        if phrase:
            self._buf.append(phrase)

    def hint(self) -> str:
        if not self._buf:
            return ""
        return "Recent fillers we've already said this session (AVOID repeating):\n" + "\n".join(
            f"  - {p}" for p in self._buf
        )


# ---------------------------------------------------------------------------
# Generator
# ---------------------------------------------------------------------------


class FillerGenerator:
    """Generates backend-aware filler via a small local LLM."""

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
        self._recent = RecentFillers(self._settings.recency_window)

    @property
    def settings(self) -> Settings:
        return self._settings

    # --- public API --------------------------------------------------------

    async def opening(
        self,
        *,
        tool_name: str,
        tool_args: dict | None,
        user_utterance: str | None,
        tts_backend: str,
    ) -> str | None:
        """Generate a one-shot opening filler. Returns None if verbosity is
        SILENT (caller should skip)."""
        if self._settings.verbosity is Verbosity.SILENT:
            return None
        length_style = _VERBOSITY_STYLE[self._settings.verbosity]
        phrase = await self._generate(
            tool_name=tool_name,
            tool_args=tool_args,
            user_utterance=user_utterance,
            tts_backend=tts_backend,
            length_style=length_style,
            kind="opening",
        )
        if phrase:
            self._recent.remember(phrase)
        return phrase

    async def progress(
        self,
        *,
        tool_name: str,
        user_utterance: str | None,
        tts_backend: str,
    ) -> str | None:
        """Generate a periodic progress filler for a long-running tool."""
        if self._settings.verbosity in (Verbosity.SILENT, Verbosity.BRIEF):
            return None
        phrase = await self._generate(
            tool_name=tool_name,
            tool_args=None,
            user_utterance=user_utterance,
            tts_backend=tts_backend,
            length_style=_PROGRESS_STYLE,
            kind="progress",
        )
        if phrase:
            self._recent.remember(phrase)
        return phrase

    async def backchannel(self, *, tts_backend: str) -> str | None:
        """Generate a brief listener-ack ('mm-hmm', 'yeah') for use WHILE
        the user is talking. Suppressed when verbosity is SILENT.

        Renders quietly via [whisper]/[softly] tags on Fish; plain on Kokoro.
        """
        if self._settings.verbosity is Verbosity.SILENT:
            return None
        phrase = await self._generate(
            tool_name="(listening)",
            tool_args=None,
            user_utterance=None,
            tts_backend=tts_backend,
            length_style=_BACKCHANNEL_STYLE,
            kind="backchannel",
        )
        if not phrase:
            return None
        # Wrap Fish output in soft/whisper for unobtrusive delivery.
        if tts_backend == "fish" and not phrase.startswith("["):
            phrase = f"[whisper] [softly] {phrase}"
        self._recent.remember(phrase)
        return phrase

    # --- internal ----------------------------------------------------------

    async def _generate(
        self,
        *,
        tool_name: str,
        tool_args: dict | None,
        user_utterance: str | None,
        tts_backend: str,
        length_style: str,
        kind: str,
    ) -> str | None:
        backend_style = _FISH_STYLE if tts_backend == "fish" else _KOKORO_STYLE
        system = (
            "You generate ONE 'thinking out loud' filler line for a voice "
            "agent that's about to run a tool. The filler will be spoken "
            "immediately — it must sound like a natural in-line hesitation, "
            "NOT an announcement or a sentence. Output ONLY the filler text, "
            "no quotes, no markdown, no explanation."
        )
        user_parts = [
            f"Tool the agent is about to run: {tool_name}",
        ]
        if tool_args:
            try:
                args_preview = ", ".join(
                    f"{k}={v!r}" for k, v in tool_args.items() if v
                )[:160]
                if args_preview:
                    user_parts.append(f"Tool arguments: {args_preview}")
            except Exception:
                pass
        if user_utterance:
            user_parts.append(f"User's last message: {user_utterance.strip()[:200]}")
        user_parts.append("")
        user_parts.append(f"Length / tone: {length_style}")
        user_parts.append("")
        user_parts.append(backend_style.strip())
        recent = self._recent.hint()
        if recent:
            user_parts.append("")
            user_parts.append(recent)
        user_parts.append("")
        user_parts.append(f"Output the {kind} filler line and nothing else.")
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
        # Strip wrapping quotes the model sometimes adds.
        if text and text[0] in "\"'" and text[-1] in "\"'":
            text = text[1:-1].strip()
        if not text:
            return None
        # Paranoia: if a Kokoro filler came back with bracketed tags, strip
        # them so they don't get spoken as literal syllables.
        if tts_backend != "fish" and "[" in text:
            import re
            text = re.sub(r"\[[^\]]+\]", "", text).strip(" ,.;:")
        return text or None
