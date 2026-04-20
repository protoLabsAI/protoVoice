"""TTS backend selection.

Two backends:
  - fish   : Fish Audio S2-Pro sidecar (default). Supports voice cloning.
  - kokoro : Local Kokoro 82M (fallback / low-latency preset voices).

Choose via env: TTS_BACKEND={fish|kokoro}
"""

import logging
import os

from pipecat.services.tts_service import TTSService

logger = logging.getLogger(__name__)

TTS_BACKEND = os.environ.get("TTS_BACKEND", "fish").lower()


def make_tts(**overrides) -> TTSService:
    backend = overrides.pop("backend", TTS_BACKEND)
    if backend == "kokoro":
        from .kokoro import LocalKokoroTTS
        return LocalKokoroTTS(**overrides)
    if backend == "fish":
        from .fish import FishAudioTTS
        return FishAudioTTS(**overrides)
    raise ValueError(f"Unknown TTS backend: {backend!r}")


def prewarm() -> None:
    """Prewarm the configured backend so the first real request is fast."""
    if TTS_BACKEND == "kokoro":
        from .kokoro import prewarm as _prewarm
    elif TTS_BACKEND == "fish":
        from .fish import prewarm as _prewarm
    else:
        logger.warning(f"No prewarm for backend {TTS_BACKEND!r}")
        return
    _prewarm()
