import logging
import time
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)

# One KPipeline instance per lang_code
_kokoro_pipes: dict[str, object] = {}

# Default warmup voice per lang prefix
_LANG_WARMUP_VOICE = {
    "a": "af_heart",
    "b": "bf_emma",
    "j": "jf_alpha",
    "z": "zf_xiaobei",
    "e": "ef_dora",
    "f": "ff_siwis",
    "h": "hf_alpha",
    "i": "if_sara",
    "p": "pf_dora",
}

# Custom voices dir — .proto/voices/<name>.pt
CUSTOM_VOICES_DIR = Path(".proto/voices")


def get_kokoro(lang: str):
    if lang not in _kokoro_pipes:
        logger.info(f"Loading Kokoro for lang={lang!r}...")
        t0 = time.time()
        from kokoro import KPipeline
        pipe = KPipeline(lang_code=lang)
        warmup_voice = _LANG_WARMUP_VOICE.get(lang[:1], "af_heart")
        try:
            list(pipe("Hello.", voice=warmup_voice, speed=1))
        except Exception:
            pass
        _kokoro_pipes[lang] = pipe
        logger.info(f"Kokoro lang={lang!r} ready in {time.time() - t0:.1f}s")
    return _kokoro_pipes[lang]


def load_custom_voice(name: str):
    """Load a custom voice tensor from .proto/voices/<name>.pt"""
    import torch
    path = CUSTOM_VOICES_DIR / f"{name}.pt"
    if path.exists():
        return torch.load(path, weights_only=True)
    return None


def tts_kokoro(text: str, voice: str, lang: str) -> tuple[int, np.ndarray]:
    pipe = get_kokoro(lang)

    # Check for custom voice embedding
    voice_tensor = None
    if not voice.startswith(("af_", "am_", "bf_", "bm_", "jf_", "jm_",
                              "zf_", "zm_", "ef_", "em_", "ff_", "hf_",
                              "if_", "pf_")):
        voice_tensor = load_custom_voice(voice)

    if voice_tensor is not None:
        chunks = list(pipe(text, voice=voice_tensor, speed=1))
    else:
        chunks = list(pipe(text, voice=voice, speed=1))

    if not chunks:
        return 24000, np.zeros(2400, dtype=np.int16)
    audio = np.concatenate([c[2] for c in chunks])
    return 24000, (audio * 32767).clip(-32768, 32767).astype(np.int16)


def list_voices() -> list[str]:
    """Return all available voice names (built-in + custom)."""
    built_in = [
        "af_heart", "af_bella", "af_sarah", "af_nicole", "af_sky",
        "am_adam", "am_michael",
        "bf_emma", "bf_isabella",
        "bm_george", "bm_lewis",
    ]
    custom = []
    if CUSTOM_VOICES_DIR.exists():
        custom = [p.stem for p in sorted(CUSTOM_VOICES_DIR.glob("*.pt"))]
    return built_in + custom
