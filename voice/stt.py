import logging
import time

import numpy as np
import soxr
import torch
from transformers import pipeline as hf_pipeline

logger = logging.getLogger(__name__)

DEVICE = "cuda" if torch.cuda.is_available() else "cpu"

_stt_pipes: dict[str, object] = {}


def get_stt(model: str):
    if model not in _stt_pipes:
        logger.info(f"Loading STT model {model}...")
        t0 = time.time()
        pipe = hf_pipeline(
            "automatic-speech-recognition",
            model=model,
            torch_dtype=torch.float16 if DEVICE == "cuda" else torch.float32,
            device=DEVICE,
            model_kwargs={"attn_implementation": "sdpa"} if DEVICE == "cuda" else {},
        )
        silence = np.zeros(16000, dtype=np.float32)
        pipe({"raw": silence, "sampling_rate": 16000})
        _stt_pipes[model] = pipe
        logger.info(f"STT ready in {time.time() - t0:.1f}s")
    return _stt_pipes[model]


def transcribe(audio_tuple: tuple[int, np.ndarray], model: str) -> str:
    sr_in, audio_in = audio_tuple
    pipe = get_stt(model)

    if audio_in.ndim > 1:
        audio_in = audio_in[:, 0] if audio_in.shape[1] < audio_in.shape[0] else audio_in[0]
    if audio_in.dtype != np.float32:
        audio_in = audio_in.astype(np.float32) / max(np.iinfo(audio_in.dtype).max, 1)
    if sr_in != 16000:
        audio_in = soxr.resample(audio_in.reshape(-1), sr_in, 16000)

    result = pipe({"raw": audio_in.flatten(), "sampling_rate": 16000})
    return result["text"].strip()
