"""Sliding-window memory with background summarization.

Pipecat's LLMContext accumulates all user + assistant messages indefinitely.
For a long conversation this blows up the prompt, hurts TTFB, and drops
the oldest (often most relevant) context.

The MemoryManager processor watches turn boundaries and, when the context
grows past `max_messages`, prunes the oldest user/assistant pair(s).
Optionally runs an LLM summarization pass in the background and prepends
the summary as a system message so the agent "remembers" the gist.

Summarization is best-effort — if the LLM summary call fails, we simply
drop the messages without a summary. The goal is preventing context
bloat, not preserving every turn verbatim.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

from pipecat.frames.frames import Frame, LLMFullResponseEndFrame
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.services.llm_service import LLMService

logger = logging.getLogger(__name__)

MAX_MESSAGES = int(os.environ.get("MEMORY_MAX_MESSAGES", "20"))
SUMMARIZE = os.environ.get("MEMORY_SUMMARIZE", "1") == "1"
SUMMARY_PROMPT = (
    "Summarize the following conversation turns in 2-3 sentences. "
    "Focus on facts mentioned, decisions made, and topics discussed. "
    "Do NOT include greetings or pleasantries."
)


class MemoryManager(FrameProcessor):
    """Sliding-window pruner; fires at the end of each assistant turn."""

    def __init__(
        self,
        context: LLMContext,
        *,
        summarizer_llm: LLMService | None = None,
        max_messages: int = MAX_MESSAGES,
    ):
        super().__init__()
        self._context = context
        self._summarizer = summarizer_llm if SUMMARIZE else None
        self._max_messages = max_messages
        self._summarizing = False  # avoid stacking summary jobs

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        # Prune on turn end to catch any overflow promptly.
        if isinstance(frame, LLMFullResponseEndFrame):
            asyncio.create_task(self._maybe_prune())
        await self.push_frame(frame, direction)

    async def _maybe_prune(self) -> None:
        msgs = self._context.messages
        # Count user/assistant/tool messages (keep system messages pinned).
        body = [m for m in msgs if m.get("role") in ("user", "assistant", "tool")]
        if len(body) <= self._max_messages:
            return
        if self._summarizing:
            return  # a summary is still in-flight; skip

        # Take oldest body messages down to half the cap so we don't thrash.
        keep = self._max_messages // 2
        overflow = body[: len(body) - keep]
        if not overflow:
            return

        logger.info(
            f"[memory] pruning {len(overflow)} oldest message(s) "
            f"(keeping {len(body) - len(overflow)} recent)"
        )

        # Actually remove the overflow messages from the live context.
        overflow_ids = {id(m) for m in overflow}
        self._context.messages[:] = [m for m in msgs if id(m) not in overflow_ids]

        if self._summarizer is None:
            return

        # Summarize the overflow and prepend as a system message.
        self._summarizing = True
        try:
            summary = await self._summarize(overflow)
            if summary:
                self._inject_summary(summary)
        except Exception as e:
            logger.warning(f"[memory] summarization failed: {e}")
        finally:
            self._summarizing = False

    async def _summarize(self, turns: list[dict[str, Any]]) -> str | None:
        transcript = "\n".join(
            f"{m['role']}: {m.get('content') or ''}" for m in turns
        )
        try:
            summary = await self._summarizer.run_inference(
                LLMContext([
                    {"role": "system", "content": SUMMARY_PROMPT},
                    {"role": "user", "content": transcript},
                ]),
                max_tokens=150,
            )
            return (summary or "").strip() or None
        except Exception as e:
            logger.warning(f"[memory] run_inference failed: {e}")
            return None

    def _inject_summary(self, summary: str) -> None:
        """Prepend / update a `Previous conversation: ...` system message
        so the existing one (if any) is replaced rather than stacked."""
        marker = "Previous conversation summary: "
        msgs = self._context.messages
        for i, m in enumerate(msgs):
            if m.get("role") == "system" and (m.get("content") or "").startswith(marker):
                m["content"] = marker + summary
                logger.info(f"[memory] summary updated ({len(summary)} chars)")
                return
        # Insert after the first system message (which is usually SOUL / persona).
        insert_at = 1 if msgs and msgs[0].get("role") == "system" else 0
        msgs.insert(insert_at, {"role": "system", "content": marker + summary})
        logger.info(f"[memory] summary inserted ({len(summary)} chars)")
