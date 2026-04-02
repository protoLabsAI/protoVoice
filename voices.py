#!/usr/bin/env python3
"""
protoVoice — Voice Studio

Blend Kokoro voices and save custom voice presets to .proto/voices/.
Saved voices appear automatically in the main app's voice selector.

Usage:
    python voices.py            # runs on port 7867
    VOICES_PORT=7868 python voices.py
"""

import logging
import os
import re
from pathlib import Path

os.environ.setdefault("HF_HOME", os.environ.get("MODEL_DIR", "/models"))

import gradio as gr
import numpy as np
import torch

from voice.tts import CUSTOM_VOICES_DIR, get_kokoro, list_voices

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

PORT = int(os.environ.get("VOICES_PORT", "7867"))

# Built-in voices grouped by language/accent
VOICE_GROUPS: dict[str, list[str]] = {
    "American (a)": [
        "af_heart", "af_bella", "af_sarah", "af_nicole", "af_sky",
        "am_adam", "am_michael",
    ],
    "British (b)": [
        "bf_emma", "bf_isabella",
        "bm_george", "bm_lewis",
    ],
}

# Map accent label → kokoro lang code
ACCENT_LANG: dict[str, str] = {
    "American (a)": "a",
    "British (b)": "b",
}

PREVIEW_TEXT = (
    "Hello! This is a preview of the blended voice. "
    "How does it sound to you?"
)

MAX_SLOTS = 4


# ---------------------------------------------------------------------------
# Core blend logic
# ---------------------------------------------------------------------------

def load_voice_tensor(name: str, lang: str) -> torch.Tensor:
    """Load a named built-in or custom voice tensor."""
    # Custom voices stored in .proto/voices/
    custom_path = CUSTOM_VOICES_DIR / f"{name}.pt"
    if custom_path.exists():
        return torch.load(custom_path, weights_only=True)
    # Built-in voices via KPipeline
    pipe = get_kokoro(lang)
    return pipe.load_single_voice(name)


def compute_blend(slots: list[tuple[str, float]], lang: str) -> torch.Tensor | None:
    """
    Weighted blend of voice tensors.
    slots: list of (voice_name, weight) — zero-weight slots are skipped.
    """
    active = [(v, w) for v, w in slots if v and w > 0]
    if not active:
        return None

    tensors, weights = [], []
    for name, weight in active:
        try:
            t = load_voice_tensor(name, lang)
            tensors.append(t)
            weights.append(weight)
        except Exception as e:
            logger.warning(f"Could not load voice {name!r}: {e}")

    if not tensors:
        return None
    if len(tensors) == 1:
        return tensors[0]

    total = sum(weights)
    blended = sum((w / total) * t for w, t in zip(weights, tensors))
    return blended


def render_audio(tensor: torch.Tensor, text: str, lang: str) -> tuple[int, np.ndarray]:
    pipe = get_kokoro(lang)
    chunks = list(pipe(text, voice=tensor, speed=1))
    if not chunks:
        return 24000, np.zeros(2400, dtype=np.int16)
    audio = np.concatenate([c[2] for c in chunks])
    return 24000, (audio * 32767).clip(-32768, 32767).astype(np.int16)


def safe_slug(name: str) -> str:
    return re.sub(r"[^a-z0-9_-]", "_", name.strip().lower())


# ---------------------------------------------------------------------------
# Gradio event handlers
# ---------------------------------------------------------------------------

def on_accent_change(accent: str) -> list:
    voices = VOICE_GROUPS.get(accent, [])
    # Include saved custom voices regardless of accent
    custom = [p.stem for p in sorted(CUSTOM_VOICES_DIR.glob("*.pt"))] if CUSTOM_VOICES_DIR.exists() else []
    all_voices = voices + custom
    empty = ""
    updates = []
    for i in range(MAX_SLOTS):
        default = all_voices[i] if i < len(all_voices) else empty
        updates.append(gr.update(choices=[empty] + all_voices, value=default))
    return updates


def on_preview(accent, text, v1, w1, v2, w2, v3, w3, v4, w4):
    lang = ACCENT_LANG.get(accent, "a")
    slots = [(v1, w1), (v2, w2), (v3, w3), (v4, w4)]
    tensor = compute_blend(slots, lang)
    if tensor is None:
        return None, "Select at least one voice with weight > 0."
    try:
        audio = render_audio(tensor, text or PREVIEW_TEXT, lang)
        return audio, ""
    except Exception as e:
        return None, f"Preview error: {e}"


def on_save(accent, name, v1, w1, v2, w2, v3, w3, v4, w4):
    slug = safe_slug(name)
    if not slug:
        return "Please enter a name.", gr.update(), gr.update()

    lang = ACCENT_LANG.get(accent, "a")
    slots = [(v1, w1), (v2, w2), (v3, w3), (v4, w4)]
    tensor = compute_blend(slots, lang)
    if tensor is None:
        return "Select at least one voice with weight > 0.", gr.update(), gr.update()

    CUSTOM_VOICES_DIR.mkdir(parents=True, exist_ok=True)
    path = CUSTOM_VOICES_DIR / f"{slug}.pt"
    torch.save(tensor, path)
    logger.info(f"Saved voice {slug!r} → {path}")

    updated = _custom_voice_rows()
    return f"Saved as {slug!r}", gr.update(value=""), gr.update(value=updated)


def on_delete(name: str):
    path = CUSTOM_VOICES_DIR / f"{name}.pt"
    if path.exists():
        path.unlink()
        logger.info(f"Deleted voice {name!r}")
    return gr.update(value=_custom_voice_rows())


def _custom_voice_rows() -> list[list]:
    if not CUSTOM_VOICES_DIR.exists():
        return []
    return [[p.stem, str(p)] for p in sorted(CUSTOM_VOICES_DIR.glob("*.pt"))]


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------

def build_ui() -> gr.Blocks:
    all_builtin = [v for voices in VOICE_GROUPS.values() for v in voices]
    default_accent = "American (a)"
    default_voices = VOICE_GROUPS[default_accent]
    empty = ""
    slot_choices = [empty] + default_voices

    with gr.Blocks(title="protoVoice — Voice Studio", css="""
        .slot-row { align-items: flex-end !important; }
        .weight-col { max-width: 120px; }
    """) as demo:
        gr.Markdown("# Voice Studio\nBlend existing voices to create a custom preset.")

        accent_dd = gr.Dropdown(
            choices=list(VOICE_GROUPS.keys()),
            value=default_accent,
            label="Accent / Language",
            interactive=True,
        )

        gr.Markdown("### Voice blend")
        gr.Markdown(
            "Select up to 4 voices and set their relative weights. "
            "Weights are normalized automatically."
        )

        voice_dds, weight_sliders = [], []
        for i in range(MAX_SLOTS):
            with gr.Row(elem_classes="slot-row"):
                vd = gr.Dropdown(
                    choices=slot_choices,
                    value=default_voices[i] if i < len(default_voices) else empty,
                    label=f"Voice {i + 1}",
                    interactive=True,
                    scale=3,
                )
                ws = gr.Slider(
                    0, 100,
                    value=100 if i == 0 else 0,
                    step=5,
                    label="Weight",
                    scale=1,
                    elem_classes="weight-col",
                )
                voice_dds.append(vd)
                weight_sliders.append(ws)

        gr.Markdown("### Preview")
        preview_text = gr.Textbox(
            value=PREVIEW_TEXT,
            label="Preview text",
            max_lines=2,
            interactive=True,
        )
        with gr.Row():
            preview_btn = gr.Button("Preview blend", variant="primary")
        preview_audio = gr.Audio(label="Preview", type="numpy", interactive=False)
        preview_status = gr.Markdown("")

        gr.Markdown("### Save")
        with gr.Row():
            name_box = gr.Textbox(
                label="Voice name",
                placeholder="e.g. warm_blend",
                max_lines=1,
                scale=3,
                interactive=True,
            )
            save_btn = gr.Button("Save voice", variant="primary", scale=1)
        save_status = gr.Markdown("")

        gr.Markdown("### Saved voices")
        gr.Markdown(
            "These appear in the main app's voice selector immediately. "
            "Restart the main app to pick up newly saved voices."
        )
        saved_table = gr.Dataframe(
            value=_custom_voice_rows(),
            headers=["Name", "Path"],
            datatype=["str", "str"],
            interactive=False,
            label=None,
        )
        with gr.Row():
            delete_name = gr.Textbox(
                label="Delete voice (enter name)",
                placeholder="e.g. warm_blend",
                max_lines=1,
                scale=3,
                interactive=True,
            )
            delete_btn = gr.Button("Delete", variant="stop", scale=1)

        # ------------------------------------------------------------------
        # Events
        # ------------------------------------------------------------------
        accent_dd.change(
            fn=on_accent_change,
            inputs=[accent_dd],
            outputs=voice_dds,
        )

        preview_btn.click(
            fn=on_preview,
            inputs=[accent_dd, preview_text] + voice_dds + weight_sliders,
            outputs=[preview_audio, preview_status],
        )

        save_btn.click(
            fn=on_save,
            inputs=[accent_dd, name_box] + voice_dds + weight_sliders,
            outputs=[save_status, name_box, saved_table],
        )

        delete_btn.click(
            fn=on_delete,
            inputs=[delete_name],
            outputs=[saved_table],
        )

    return demo


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    auth = os.environ.get("GRADIO_AUTH")
    auth_pairs = None
    if auth:
        auth_pairs = [tuple(p.split(":", 1)) for p in auth.split(",")]

    demo = build_ui()
    demo.launch(
        server_name="0.0.0.0",
        server_port=PORT,
        share=False,
        show_error=True,
        auth=auth_pairs,
    )
