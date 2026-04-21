"""BackchannelController — emits brief listener-acks during long user turns.

Backchannels ("mm-hmm", "yeah", "right") are different from fillers:

  - **Filler** fires AFTER the user stops speaking, while the agent does
    a tool call.
  - **Backchannel** fires WHILE the user is speaking, signalling "I'm
    tracking, keep going."

Implementation: watch UserStartedSpeakingFrame / UserStoppedSpeakingFrame.
On started, kick off a timer task. After `first_after_secs`, emit a
backchannel via the FillerGenerator. Then continue every
`interval_secs` until the user stops. Cancel + drain on UserStopped.

This is intentionally minimal — most user turns are <5s and don't need
a backchannel at all. The defaults err quiet.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Awaitable, Callable

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    Frame,
    TTSSpeakFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from .filler import FillerGenerator, Verbosity

logger = logging.getLogger(__name__)

FIRST_AFTER_SECS = float(os.environ.get("BACKCHANNEL_FIRST_SECS", "5.0"))
INTERVAL_SECS = float(os.environ.get("BACKCHANNEL_INTERVAL_SECS", "6.0"))

FrameEmitter = Callable[[Frame], Awaitable[None]]


class BackchannelController(FrameProcessor):
    def __init__(
        self,
        *,
        generator: FillerGenerator,
        tts_backend: str,
    ):
        super().__init__()
        self._gen = generator
        self._backend = tts_backend
        self._loop_task: asyncio.Task | None = None
        self._emitter: FrameEmitter | None = None
        self._bot_speaking = False
        self._user_speaking = False

    def set_emitter(self, emitter: FrameEmitter) -> None:
        """Wired by app.py post-construction (task.queue_frame)."""
        self._emitter = emitter

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if isinstance(frame, UserStartedSpeakingFrame):
            self._user_speaking = True
            self._start_loop()
        elif isinstance(frame, UserStoppedSpeakingFrame):
            self._user_speaking = False
            self._cancel_loop()
        elif isinstance(frame, BotStartedSpeakingFrame):
            self._bot_speaking = True
            # Bot is now talking — any pending backchannel is out of place.
            self._cancel_loop()
        elif isinstance(frame, BotStoppedSpeakingFrame):
            self._bot_speaking = False
        await self.push_frame(frame, direction)

    def _start_loop(self) -> None:
        # Suppress entirely when verbosity is SILENT.
        if self._gen.settings.verbosity is Verbosity.SILENT:
            return
        # Don't start while bot is talking — a UserStartedSpeakingFrame
        # during agent TTS is almost always echo bleed past the guard.
        if self._bot_speaking:
            return
        # Avoid stacking — if we already have one running, leave it.
        if self._loop_task and not self._loop_task.done():
            return
        self._loop_task = asyncio.create_task(self._loop())

    def _cancel_loop(self) -> None:
        if self._loop_task and not self._loop_task.done():
            self._loop_task.cancel()
        self._loop_task = None

    async def _loop(self) -> None:
        try:
            await asyncio.sleep(FIRST_AFTER_SECS)
            await self._emit_one()
            while True:
                await asyncio.sleep(INTERVAL_SECS)
                await self._emit_one()
        except asyncio.CancelledError:
            pass

    async def _emit_one(self) -> None:
        from agent import tracing
        with tracing.span("backchannel.emit") as sp:
            try:
                phrase = await self._gen.backchannel(tts_backend=self._backend)
            except Exception as e:
                sp.update(level="WARNING", status_message=str(e))
                logger.warning(f"[backchannel] generator raised: {e}")
                return
            if not phrase:
                sp.update(status_message="skipped (empty)")
                return
            # The generator call may have taken long enough that the user
            # has stopped and the bot started responding. Emitting now would
            # tack an "mm-hmm" onto the end of the agent's reply. Drop it.
            if self._bot_speaking or not self._user_speaking:
                sp.update(status_message="skipped (state changed mid-generate)")
                return
            sp.update(output=phrase)
            logger.info(f"[backchannel] {phrase!r}")
            # Backchannels MUST stay out of LLM context — they're listener
            # noises, not assistant turns.
            frame = TTSSpeakFrame(phrase, append_to_context=False)
            if self._emitter is not None:
                await self._emitter(frame)
            else:
                await self.push_frame(frame)
