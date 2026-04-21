"""Cross-session summary persistence.

Pipecat's LLMContextSummarizer produces per-session summaries that die
when the session ends. For a single-user homelab, a tiny file-backed
store per skill lets the NEXT session open with a "last time we
discussed X" callback.

Sesame CSM research: memory callbacks at session-open boost "presence"
ratings; mid-turn recall is rated "creepy." This module only produces
callbacks at session-open (see `app.py::_effective_prompt`).

File layout:
  {STORE_DIR}/{skill_slug}.txt   ← plain text summary, overwritten each
                                   time the summarizer applies a new one

Not multi-tenant. Single user ≡ single device ≡ single store. Adding
per-user keying is future work.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

_DEFAULT_DIR = Path(os.environ.get("SESSION_STORE_DIR", "/tmp/protovoice_sessions"))


def _path(skill_slug: str) -> Path:
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in skill_slug)
    return _DEFAULT_DIR / f"{safe}.txt"


def load_last_summary(skill_slug: str) -> str | None:
    p = _path(skill_slug)
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
    p = _path(skill_slug)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(summary.strip(), encoding="utf-8")
        logger.info(f"[session_store] saved summary for {skill_slug!r} ({len(summary)} chars)")
    except Exception as e:
        logger.warning(f"[session_store] failed to write {p}: {e}")
