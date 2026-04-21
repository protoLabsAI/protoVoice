"""Push-delivery controller for async tool results.

Pipecat's native async-tool path (`cancel_on_interruption=False`) already
implements a version of "next_silence" — when the tool returns, pipecat
injects the result as a developer message and the LLM speaks it at the
next pipeline opportunity (which is ~the next user-silence).

This module layers two richer behaviours on top:

  now          — speak the result IMMEDIATELY via TTSSpeakFrame, even
                 interrupting the user. Use sparingly.
  next_silence — wait for the next VAD-detected user silence, then speak.
                 Same effect as pipecat's default for async tools, but gives
                 us control over phrasing (e.g. "by the way, here's what I
                 found about X...").
  when_asked   — suppress until the user references the topic; keyword
                 match against the original query.

The controller is implemented as a FrameProcessor so it can observe
UserStartedSpeaking / UserStoppedSpeaking / TranscriptionFrame without
needing transport event hooks (which don't fire for these).
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import Enum

from pipecat.frames.frames import (
    Frame,
    TranscriptionFrame,
    TTSSpeakFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

logger = logging.getLogger(__name__)

# Signature: pushes a Frame into the PipelineTask from outside process_frame.
# app.py wires `task.queue_frame` into this so deliveries fire reliably no
# matter which asyncio context calls controller.deliver().
FrameEmitter = Callable[[Frame], Awaitable[None]]


class DeliveryPolicy(str, Enum):
    NOW = "now"
    NEXT_SILENCE = "next_silence"
    WHEN_ASKED = "when_asked"


class Priority(str, Enum):
    """Apple UNNotificationInterruptionLevel-shaped urgency. Callers
    specify this at enqueue time; the controller auto-maps it to a
    delivery policy. Explicit `policy=` still wins if supplied."""

    CRITICAL = "critical"             # hard-interrupt anything
    TIME_SENSITIVE = "time_sensitive" # drain on the next natural pause
    ACTIVE = "active"                 # wait for the user to reference it
    PASSIVE = "passive"               # low-signal; hold for later


# Auto-map priority → policy when caller doesn't force one.
_PRIORITY_TO_POLICY: dict[Priority, DeliveryPolicy] = {
    Priority.CRITICAL: DeliveryPolicy.NOW,
    Priority.TIME_SENSITIVE: DeliveryPolicy.NEXT_SILENCE,
    Priority.ACTIVE: DeliveryPolicy.WHEN_ASKED,
    # TODO: PASSIVE → dedicated DIGEST policy once the digest surface
    # exists. For now treat as when-asked (so it emits if the user brings
    # it up) but don't drain on silence.
    Priority.PASSIVE: DeliveryPolicy.WHEN_ASKED,
}


@dataclass
class _Pending:
    phrase: str
    policy: DeliveryPolicy
    priority: Priority = Priority.ACTIVE
    keywords: tuple[str, ...] = ()
    enqueued_at: float = field(default_factory=time.time)


# Silence (wait after UserStoppedSpeaking) before draining `next_silence`.
# Keeps the filler from stepping on the tail of the user's utterance.
_SILENCE_SETTLE_SECS = 0.6


class DeliveryController(FrameProcessor):
    """Tracks VAD state + user transcripts, drains pending deliveries."""

    def __init__(self) -> None:
        super().__init__()
        self._user_speaking = False
        self._pending: list[_Pending] = []
        self._settle_task: asyncio.Task | None = None
        # Set by app.py after PipelineTask exists. Required for correct
        # out-of-band emission (push_frame from another coroutine context
        # is unsafe).
        self._emitter: FrameEmitter | None = None

    def set_emitter(self, emitter: FrameEmitter) -> None:
        self._emitter = emitter

    # Public API — tool handlers call this to schedule a push delivery.
    async def deliver(
        self,
        phrase: str,
        *,
        priority: Priority = Priority.ACTIVE,
        policy: DeliveryPolicy | None = None,
        keywords: tuple[str, ...] = (),
        source: str | None = None,
    ) -> None:
        """Deliver `phrase` according to its urgency.

        Typical call: pass only `priority=` and let the controller map to
        the right policy. Pass explicit `policy=` to override.

        Policy semantics:
          NOW           — emit immediately (interrupts user if they're speaking)
          NEXT_SILENCE  — queue; emit when the user pauses
          WHEN_ASKED    — queue; emit only when the user's transcript
                          references one of `keywords`

        `source` — if provided and not our own agent, the phrase is
        prefixed with an attribution ("ava says — …"). CHI '23 data: voice
        attribution boosts trust after errors and makes delegated replies
        feel less like ours to own.
        """
        priority = Priority(priority)
        effective_policy = DeliveryPolicy(policy) if policy is not None else _PRIORITY_TO_POLICY[priority]
        attributed = _attribute(phrase, source)
        logger.info(
            f"[delivery] enqueue priority={priority.value} policy={effective_policy.value}"
            f"{f' source={source}' if source else ''}: {attributed[:60]!r}"
        )
        if effective_policy is DeliveryPolicy.NOW:
            await self._emit(attributed)
            return
        self._pending.append(
            _Pending(phrase=attributed, policy=effective_policy, priority=priority, keywords=keywords)
        )
        # If the user isn't currently speaking and there's something eligible
        # for NEXT_SILENCE, drain right away.
        if not self._user_speaking:
            await self._drain_eligible(new_transcript=None)

    async def _emit(self, phrase: str) -> None:
        logger.info(f"[delivery] emit {phrase[:60]!r}")
        # append_to_context=False — push deliveries are spoken directly;
        # they shouldn't show up as part of the assistant's LLM history
        # (the underlying tool already injected its result via the proper
        # channel — see app.py llm.supports_developer_role=False).
        frame = TTSSpeakFrame(phrase, append_to_context=False)
        if self._emitter is not None:
            await self._emitter(frame)
        else:
            logger.warning(
                "[delivery] no emitter wired; falling back to push_frame"
            )
            await self.push_frame(frame)

    async def _drain_eligible(self, *, new_transcript: str | None) -> None:
        """Pop + emit any pending entries whose policy conditions are met."""
        remaining: list[_Pending] = []
        for item in self._pending:
            if item.policy is DeliveryPolicy.NEXT_SILENCE:
                await self._emit(item.phrase)
                continue
            if item.policy is DeliveryPolicy.WHEN_ASKED:
                if new_transcript and _keyword_match(new_transcript, item.keywords):
                    await self._emit(item.phrase)
                    continue
                remaining.append(item)
                continue
            # Unknown policy — drop silently rather than blocking pipeline.
            logger.warning(f"[delivery] unknown policy on pending item: {item.policy!r}")
        self._pending = remaining

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)

        if isinstance(frame, UserStartedSpeakingFrame):
            self._user_speaking = True
            # Cancel any in-flight settle — if the user starts again before
            # the settle delay elapses, hold everything back.
            if self._settle_task and not self._settle_task.done():
                self._settle_task.cancel()
        elif isinstance(frame, UserStoppedSpeakingFrame):
            self._user_speaking = False
            # Schedule a settle-delay drain.
            self._settle_task = asyncio.create_task(self._settle_then_drain())
        elif isinstance(frame, TranscriptionFrame) and frame.text:
            # Give `when_asked` entries a chance to match the new utterance.
            await self._drain_eligible(new_transcript=frame.text)

        await self.push_frame(frame, direction)

    async def _settle_then_drain(self) -> None:
        try:
            await asyncio.sleep(_SILENCE_SETTLE_SECS)
        except asyncio.CancelledError:
            return
        if self._user_speaking:
            return
        await self._drain_eligible(new_transcript=None)


def _keyword_match(text: str, keywords: tuple[str, ...]) -> bool:
    """Naive substring match — good enough for M3 validation.

    Replace with a proper semantic match in M4+ if we want to be less dumb
    about synonyms ("hot dogs" vs "frankfurters" etc).
    """
    if not keywords:
        return False
    low = text.lower()
    return any(k.lower() in low for k in keywords)


def _attribute(phrase: str, source: str | None) -> str:
    """Prefix a delivery phrase with its source if one is given and it's
    not our own turn. Uses "{source} says — …" framing; the em-dash +
    space prompts a natural Fish/Kokoro pause."""
    if not source:
        return phrase
    # Avoid double-attributing if a caller already included the source.
    low = phrase.lower()
    if low.startswith(f"{source.lower()} "):
        return phrase
    return f"{source} says — {phrase}"
