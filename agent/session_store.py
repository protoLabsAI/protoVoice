"""Cross-session persistence — summary + orphaned deliveries.

Two things survive a WebRTC disconnect today:

  1. Rolling conversation summary (pipecat LLMContextSummarizer output) —
     used for session-open callbacks (Sesame "presence" finding).
  2. Pending push deliveries that couldn't land because the session ended
     — slow_research completing after disconnect, a2a push arriving with
     no active voice session, etc.

Both are file-backed per skill slug. Single-user homelab scope; per-user
keying is future work.

Layout:
  {STORE_DIR}/{skill_slug}.txt       ← plain text summary
  {STORE_DIR}/{skill_slug}.pending.json  ← list of serialized _Pending
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_DEFAULT_DIR = Path(os.environ.get("SESSION_STORE_DIR", "/tmp/protovoice_sessions"))


def _safe_slug(skill_slug: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in skill_slug)


def _summary_path(skill_slug: str) -> Path:
    return _DEFAULT_DIR / f"{_safe_slug(skill_slug)}.txt"


def _pending_path(skill_slug: str) -> Path:
    return _DEFAULT_DIR / f"{_safe_slug(skill_slug)}.pending.json"


# --- Summary -----------------------------------------------------------------

def load_last_summary(skill_slug: str) -> str | None:
    p = _summary_path(skill_slug)
    if not p.exists():
        return None
    try:
        text = p.read_text(encoding="utf-8").strip()
        return text or None
    except Exception as e:
        logger.warning(f"[session_store] failed to read {p}: {e}")
        return None


def save_summary(skill_slug: str, summary: str) -> None:
    if not summary or not summary.strip():
        return
    p = _summary_path(skill_slug)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(summary.strip(), encoding="utf-8")
        logger.info(f"[session_store] saved summary for {skill_slug!r} ({len(summary)} chars)")
    except Exception as e:
        logger.warning(f"[session_store] failed to write {p}: {e}")


# --- Orphan deliveries -------------------------------------------------------

def stash_delivery(skill_slug: str, item: dict[str, Any]) -> None:
    """Append a single delivery (phrase + priority + keywords + source) to
    the skill's orphan queue. Called when a push arrives with no active
    session OR when a live session shuts down with pending items."""
    p = _pending_path(skill_slug)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        existing: list[dict[str, Any]] = []
        if p.exists():
            try:
                existing = json.loads(p.read_text(encoding="utf-8"))
                if not isinstance(existing, list):
                    existing = []
            except Exception:
                existing = []
        existing.append(item)
        p.write_text(json.dumps(existing), encoding="utf-8")
        logger.info(f"[session_store] stashed delivery for {skill_slug!r} (now {len(existing)} pending)")
    except Exception as e:
        logger.warning(f"[session_store] failed to stash {p}: {e}")


def drain_stashed_deliveries(skill_slug: str) -> list[dict[str, Any]]:
    """Load all orphan deliveries for this skill, delete the file, return
    the list. Called at session-connect time to replay what was missed."""
    p = _pending_path(skill_slug)
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            data = []
    except Exception as e:
        logger.warning(f"[session_store] failed to read {p}: {e}")
        data = []
    try:
        p.unlink()
    except Exception:
        pass
    logger.info(f"[session_store] drained {len(data)} stashed deliveries for {skill_slug!r}")
    return data
