"""Local Whisper STT as a Pipecat SegmentedSTTService.

SegmentedSTTService accumulates audio between VAD Started/Stopped boundaries
and hands us a complete WAV blob per utterance — ideal for offline Whisper
(which needs a full utterance, not a continuous stream).
"""

import io
import logging
import os
import time
from collections.abc import AsyncGenerator

import numpy as np
import soundfile as sf
import soxr
import torch
from pipecat.frames.frames import ErrorFrame, Frame, TranscriptionFrame
from pipecat.services.settings import STTSettings
from pipecat.services.stt_service import SegmentedSTTService
from pipecat.utils.time import time_now_iso8601
from transformers import pipeline as hf_pipeline

logger = logging.getLogger(__name__)

WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "openai/whisper-large-v3-turbo")
DEVICE = "cuda" if torch.cuda.is_available() else "cpu"

_pipe = None


def _get_pipe():
    """Lazy-load + warm the HF Whisper pipeline. Idempotent."""
    global _pipe
    if _pipe is not None:
        return _pipe
    logger.info(f"Loading {WHISPER_MODEL} on {DEVICE}")
    t0 = time.time()
    _pipe = hf_pipeline(
        "automatic-speech-recognition",
        model=WHISPER_MODEL,
        torch_dtype=torch.float16 if DEVICE == "cuda" else torch.float32,
        device=DEVICE,
        model_kwargs={"attn_implementation": "sdpa"} if DEVICE == "cuda" else {},
    )
    _pipe({"raw": np.zeros(16000, dtype=np.float32), "sampling_rate": 16000})
    logger.info(f"Whisper ready in {time.time() - t0:.1f}s")
    return _pipe


class LocalWhisperSTT(SegmentedSTTService):
    """Pipecat STT wrapper around a HuggingFace Whisper pipeline."""

    def __init__(self, *, user_id: str = "user", **kwargs):
        # Provide explicit None for cloud-STT-oriented fields so pipecat's
        # settings validator stops logging them as NOT_GIVEN at ERROR level.
        kwargs.setdefault("settings", STTSettings(model=None, language=None))
        super().__init__(**kwargs)
        self._user_id = user_id

    async def run_stt(self, audio: bytes) -> AsyncGenerator[Frame, None]:
        # SegmentedSTTService gives us a WAV blob from the VAD segmenter.
        try:
            data, sr = sf.read(io.BytesIO(audio), dtype="float32")
        except Exception as e:
            yield ErrorFrame(error=f"STT decode failed: {e}")
            return

        if data.ndim > 1:
            data = data.mean(axis=1)
        if sr != 16000:
            data = soxr.resample(data, sr, 16000)

        try:
            result = _get_pipe()({"raw": data.flatten(), "sampling_rate": 16000})
            text = (result.get("text") or "").strip()
        except Exception as e:
            yield ErrorFrame(error=f"STT inference failed: {e}")
            return

        if text:
            yield TranscriptionFrame(text, self._user_id, time_now_iso8601())


def prewarm() -> None:
    _get_pipe()


def transcribe_bytes(audio_bytes: bytes) -> str:
    """One-shot transcribe arbitrary audio bytes (WAV / MP3 / FLAC / OGG).

    Used by the voice-clone endpoint to auto-generate a reference
    transcript. Reuses the already-loaded pipeline; first call after boot
    blocks until the model is warm.
    """
    data, sr = sf.read(io.BytesIO(audio_bytes), dtype="float32")
    if data.ndim > 1:
        data = data.mean(axis=1)
    if sr != 16000:
        data = soxr.resample(data, sr, 16000)
    result = _get_pipe()({"raw": data.flatten(), "sampling_rate": 16000})
    return (result.get("text") or "").strip()
