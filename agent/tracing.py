"""Langfuse tracing — singleton + session/trace helpers.

One Langfuse client per process. Each WebRTC session creates a
Langfuse session; each user turn creates a trace spanning STT → LLM →
(optional tools) → TTS. Spans within the trace are labelled with the
same prefixes that appear in our log lines so grep-and-Langfuse stay
grep-correlatable.

Cross-fleet propagation: every outbound call to another agent carries
the current trace's `session_id` + `trace_id` via headers
(`Langfuse-Session-Id`, `Langfuse-Trace-Id`). Receiving agents adopt
those values when constructing their own spans; traces stitch together
across the protoLabs fleet rather than ending at our service boundary.
See `docs/reference/tracing-contract.md`.

Fail-open: if LANGFUSE_* env vars are unset, every helper here is a
no-op. Local dev without Langfuse keeps working; production gets full
tracing when the keys are present.
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

_ENABLED = all(
    os.environ.get(k, "").strip()
    for k in ("LANGFUSE_HOST", "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY")
)
_CLIENT: Any = None


def _lazy_client() -> Any:
    """Return the Langfuse client or None if not configured. Creates on
    first use to keep import-time work minimal."""
    global _CLIENT
    if not _ENABLED:
        return None
    if _CLIENT is not None:
        return _CLIENT
    try:
        from langfuse import Langfuse  # type: ignore[import-not-found]
    except Exception as e:
        logger.warning(f"[tracing] langfuse SDK import failed, disabling: {e}")
        return None
    try:
        _CLIENT = Langfuse(
            public_key=os.environ["LANGFUSE_PUBLIC_KEY"],
            secret_key=os.environ["LANGFUSE_SECRET_KEY"],
            host=os.environ["LANGFUSE_HOST"],
        )
        logger.info(f"[tracing] langfuse client ready → {os.environ['LANGFUSE_HOST']}")
        return _CLIENT
    except Exception as e:
        logger.warning(f"[tracing] langfuse init failed, disabling: {e}")
        return None


def enabled() -> bool:
    """Public flag for callers that want to skip expensive prep when off."""
    return _ENABLED and _lazy_client() is not None


# ---------------------------------------------------------------------------
# Session / trace helpers — all no-op when disabled
# ---------------------------------------------------------------------------

class _NullSpan:
    """Stand-in object returned when tracing is off, so callers can use
    `with trace.span(...)` / `span.update(...)` / `span.end(...)` without
    `if enabled` guards everywhere."""
    def __enter__(self): return self
    def __exit__(self, *_): return False
    def update(self, **_kwargs): return self
    def end(self, **_kwargs): return None
    def score(self, *_args, **_kwargs): return None
    def span(self, *_args, **_kwargs): return _NullSpan()
    def generation(self, *_args, **_kwargs): return _NullSpan()
    @property
    def id(self) -> str: return ""
    @property
    def trace_id(self) -> str: return ""


_NULL = _NullSpan()


def start_session(session_id: str, *, user_id: str | None = None) -> None:
    """Mark a WebRTC session — no Langfuse object is created (sessions
    are implicit via session_id on traces), but we log for correlation."""
    if not enabled():
        return
    logger.info(f"[tracing] session start id={session_id!r} user={user_id!r}")


def start_turn_trace(
    *,
    session_id: str,
    name: str = "user_turn",
    input: Any = None,
    user_id: str | None = None,
    metadata: dict | None = None,
) -> Any:
    """Open a new trace for a single user turn. Returns a trace handle
    (or a NullSpan when disabled). Caller is responsible for calling
    `.update(output=…)` and `.end()` when the turn completes."""
    client = _lazy_client()
    if client is None:
        return _NULL
    try:
        return client.trace(
            name=name,
            session_id=session_id,
            user_id=user_id,
            input=input,
            metadata=metadata or {},
        )
    except Exception as e:
        logger.warning(f"[tracing] trace() failed: {e}")
        return _NULL


def continue_trace(
    *,
    trace_id: str,
    session_id: str,
) -> Any:
    """Re-attach to a trace started elsewhere — used when we receive a
    cross-fleet call with Langfuse-Trace-Id / Langfuse-Session-Id headers
    and want our spans to nest inside the caller's trace."""
    client = _lazy_client()
    if client is None:
        return _NULL
    try:
        return client.trace(id=trace_id, session_id=session_id)
    except Exception as e:
        logger.warning(f"[tracing] continue_trace() failed: {e}")
        return _NULL


# ---------------------------------------------------------------------------
# TurnTracer — pipeline observer owning the trace lifecycle
# ---------------------------------------------------------------------------

# Deferred imports so this module stays cheap when Langfuse is off.
def _frame_types():
    from pipecat.frames.frames import (
        BotStoppedSpeakingFrame,
        LLMFullResponseEndFrame,
        LLMFullResponseStartFrame,
        TranscriptionFrame,
        UserStoppedSpeakingFrame,
    )
    return (
        UserStoppedSpeakingFrame,
        BotStoppedSpeakingFrame,
        TranscriptionFrame,
        LLMFullResponseStartFrame,
        LLMFullResponseEndFrame,
    )


class TurnTracer:
    """Owns the per-turn trace lifecycle.

    Used as a pipecat `BaseObserver`. Subclasses `BaseObserver` when
    Langfuse is enabled; otherwise a no-op shim. We dynamically base-class
    at import time to avoid paying the observer overhead when tracing is
    off.

    Turn model:
      - User stops speaking (UserStoppedSpeakingFrame) → open a new trace.
      - LLM response end (LLMFullResponseEndFrame) + bot speaking ends
        (BotStoppedSpeakingFrame) → close the trace.
      - Spans are added by other code via `get_current_trace()` — this
        observer only bounds them.
    """

    def __init__(self, session_id: str, user_id: str | None = None) -> None:
        self.session_id = session_id
        self.user_id = user_id
        self._current_trace: Any = None
        self._last_transcript: str | None = None
        self._llm_response_closed = False
        self._bot_stopped = False

    def get_current_trace(self) -> Any:
        """Other code pulls this to add spans under the active turn."""
        return self._current_trace or _NULL

    async def on_push_frame(self, data: Any) -> None:
        (
            UserStoppedSpeakingFrame,
            BotStoppedSpeakingFrame,
            TranscriptionFrame,
            LLMFullResponseStartFrame,
            LLMFullResponseEndFrame,
        ) = _frame_types()

        frame = data.frame

        # Capture the latest transcript so the trace's input field carries
        # the user's actual message, not a placeholder.
        if isinstance(frame, TranscriptionFrame) and getattr(frame, "text", None):
            self._last_transcript = frame.text

        if isinstance(frame, UserStoppedSpeakingFrame):
            if self._current_trace is None:
                self._current_trace = start_turn_trace(
                    session_id=self.session_id,
                    user_id=self.user_id,
                    input=self._last_transcript,
                    metadata={"trigger": "user_stopped_speaking"},
                )
                self._llm_response_closed = False
                self._bot_stopped = False

        elif isinstance(frame, LLMFullResponseEndFrame):
            self._llm_response_closed = True
            self._maybe_close_trace()

        elif isinstance(frame, BotStoppedSpeakingFrame):
            self._bot_stopped = True
            self._maybe_close_trace()

    def _maybe_close_trace(self) -> None:
        # End the trace only after BOTH the LLM response has closed and
        # the bot audio has finished playing. If either fires alone, we
        # may be mid-tool-call or mid-TTS.
        if not (self._llm_response_closed and self._bot_stopped):
            return
        if self._current_trace is None:
            return
        try:
            self._current_trace.end()
        except Exception as e:
            logger.warning(f"[tracing] trace.end() failed: {e}")
        self._current_trace = None
        self._llm_response_closed = False
        self._bot_stopped = False


def make_turn_tracer(session_id: str, user_id: str | None = None) -> Any:
    """Create a TurnTracer that's either a real pipecat BaseObserver (when
    tracing is on) or a no-op shim. Return type is duck-typed `BaseObserver`
    in both cases — app.py can pass it to `PipelineTask(observers=[…])`
    unconditionally."""
    if not enabled():
        # Minimal no-op observer: inherit from BaseObserver so the
        # PipelineTask accepts it, but do nothing.
        from pipecat.observers.base_observer import BaseObserver
        class _NoopTracer(BaseObserver):
            def get_current_trace(self):
                return _NULL
            async def on_push_frame(self, _data):
                return
        return _NoopTracer()
    from pipecat.observers.base_observer import BaseObserver
    # Dynamically compose so TurnTracer gets the Observer mixin.
    class _ActiveTracer(TurnTracer, BaseObserver):
        pass
    return _ActiveTracer(session_id=session_id, user_id=user_id)


def flush() -> None:
    """Drain queued events before shutdown so nothing is lost. Safe to
    call when disabled."""
    client = _lazy_client()
    if client is None:
        return
    try:
        client.flush()
    except Exception as e:
        logger.warning(f"[tracing] flush failed: {e}")
