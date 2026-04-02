#!/usr/bin/env python3
"""
protoVoice — Sub-200ms real-time voice agent

Pipeline: Mic → Silero VAD → Whisper STT → LLM → Kokoro TTS → Speaker

Modes:
  chat       — default conversational assistant
  transcribe — STT only, transcript log
  agent      — ReAct loop with web search, calculator, datetime
  wake_word  — gates chat on trigger phrase
  skill:*    — auto-loaded from .proto/skills/*.md
"""

import json
import logging
import os
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime

os.environ.setdefault("HF_HOME", os.environ.get("MODEL_DIR", "/models"))

import gradio as gr
import httpx
import numpy as np
from fastrtc import ReplyOnPause, Stream
from fastrtc.reply_on_pause import AlgoOptions

from skills.loader import load_skills
from voice.agent import VoiceAgent, VoiceConfig
from voice.stt import get_stt
from voice.tts import get_kokoro, list_voices

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config — env vars
# ---------------------------------------------------------------------------
PORT = int(os.environ.get("PORT", "7866"))
VLLM_PORT = int(os.environ.get("VLLM_PORT", "8100"))
LLM_URL = os.environ.get("LLM_URL", f"http://localhost:{VLLM_PORT}/v1")
LLM_MODEL = os.environ.get("LLM_MODEL", "Qwen/Qwen3.5-4B")
LLM_SERVED_NAME = os.environ.get("LLM_SERVED_NAME", "local")
LLM_API_KEY = os.environ.get("LLM_API_KEY", "")
WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "openai/whisper-large-v3-turbo")
KOKORO_VOICE = os.environ.get("KOKORO_VOICE", "af_heart")
KOKORO_LANG = os.environ.get("KOKORO_LANG", "a")
START_VLLM = os.environ.get("START_VLLM", "1") == "1"

VOICE_PREAMBLE = (
    "You are speaking directly to the user through a voice interface. "
    "Your response will be read aloud by a text-to-speech engine, so you must follow these rules strictly: "
    "never use markdown, bullet points, numbered lists, headers, code blocks, or any formatting. "
    "Never use emojis, symbols, or special unicode characters — they will be spoken literally and sound broken. "
    "Speak in casual, natural, conversational sentences as if you are talking out loud. "
    "Keep responses short: 1 to 3 sentences unless more detail is truly necessary. "
    "/no_think"
)

CHAT_SYSTEM_PROMPT = VOICE_PREAMBLE + os.environ.get("SYSTEM_PROMPT", (
    "You are a helpful voice assistant. Be warm, direct, and concise."
))

AGENT_SYSTEM_PROMPT = VOICE_PREAMBLE + (
    "You are a helpful voice assistant with access to tools for searching the web, "
    "doing calculations, and checking the current date and time. "
    "Use tools when needed to give accurate answers. "
    "After getting tool results, respond in 1 to 2 spoken sentences."
)

# Maps voice names to their Kokoro lang code
VOICE_LANG_MAP: dict[str, str] = {
    "af_heart": "a", "af_bella": "a", "af_sarah": "a", "af_nicole": "a", "af_sky": "a",
    "am_adam": "a", "am_michael": "a",
    "bf_emma": "b", "bf_isabella": "b",
    "bm_george": "b", "bm_lewis": "b",
}

# ---------------------------------------------------------------------------
# Shared mutable state (single-user design)
# ---------------------------------------------------------------------------
_algo_options = AlgoOptions(
    audio_chunk_duration=0.6,
    started_talking_threshold=0.5,
    speech_threshold=0.1,
)

_config = VoiceConfig(
    mode="chat",
    voice=KOKORO_VOICE,
    lang=KOKORO_LANG,
    temperature=0.7,
    max_tokens=150,
    system_prompt=CHAT_SYSTEM_PROMPT,
    llm_url=LLM_URL,
    model=LLM_SERVED_NAME,
    api_key=LLM_API_KEY,
    whisper_model=WHISPER_MODEL,
)

_transcript_entries: list[str] = []
_transcript_lock = threading.Lock()

agent = VoiceAgent()

# ---------------------------------------------------------------------------
# Transcript helpers
# ---------------------------------------------------------------------------
def _add_transcript(text: str):
    ts = datetime.now().strftime("%H:%M:%S")
    with _transcript_lock:
        _transcript_entries.append(f"[{ts}] {text}")


def _get_transcript() -> str:
    with _transcript_lock:
        return "\n".join(_transcript_entries)


def _clear_transcript() -> str:
    with _transcript_lock:
        _transcript_entries.clear()
    return ""


# ---------------------------------------------------------------------------
# Voice handler (FastRTC entry point)
# ---------------------------------------------------------------------------
def voice_handler(audio: tuple[int, np.ndarray]):
    agent.interrupt()
    config = _config
    for event_type, payload in agent.process(audio, config):
        if event_type == "audio":
            yield payload
        elif event_type == "transcript":
            _add_transcript(payload)


# ---------------------------------------------------------------------------
# Built-in vLLM subprocess
# ---------------------------------------------------------------------------
_vllm_proc = None


def start_vllm():
    global _vllm_proc
    if not START_VLLM:
        logger.info(f"Using external LLM at {LLM_URL}")
        return
    logger.info(f"Starting vLLM with {LLM_MODEL} on port {VLLM_PORT}...")
    _vllm_proc = subprocess.Popen(
        [
            sys.executable, "-m", "vllm.entrypoints.openai.api_server",
            "--model", LLM_MODEL,
            "--host", "127.0.0.1",
            "--port", str(VLLM_PORT),
            "--served-model-name", "local",
            "--max-model-len", "32768",
            "--gpu-memory-utilization", "0.40",
            "--enable-prefix-caching",
            "--enable-chunked-prefill",
            "--chat-template-kwargs", json.dumps({"enable_thinking": False}),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    for _ in range(120):
        try:
            r = httpx.get(f"http://localhost:{VLLM_PORT}/v1/models", timeout=2.0)
            if r.status_code == 200:
                logger.info("vLLM ready")
                return
        except Exception:
            pass
        time.sleep(1)
    logger.error("vLLM failed to start within 120s")


def stop_vllm():
    global _vllm_proc
    if _vllm_proc:
        _vllm_proc.terminate()
        _vllm_proc.wait(timeout=10)
        _vllm_proc = None


# ---------------------------------------------------------------------------
# Model pre-warming
# ---------------------------------------------------------------------------
def prewarm():
    logger.info("Pre-warming models...")
    t0 = time.time()
    get_stt(WHISPER_MODEL)
    get_kokoro(KOKORO_LANG)
    try:
        httpx.post(
            f"{LLM_URL}/chat/completions",
            json={
                "model": LLM_SERVED_NAME,
                "messages": [
                    {"role": "system", "content": CHAT_SYSTEM_PROMPT},
                    {"role": "user", "content": "Hi"},
                ],
                "max_tokens": 1,
                "temperature": 0,
                "chat_template_kwargs": {"enable_thinking": False},
            },
            timeout=30.0,
        )
        logger.info("LLM prefix cache warmed")
    except Exception as e:
        logger.warning(f"LLM warmup skipped: {e}")
    logger.info(f"All models ready in {time.time() - t0:.1f}s")


# ---------------------------------------------------------------------------
# Gradio UI
# ---------------------------------------------------------------------------
def build_ui(skills):
    skills_map = {s.slug: s for s in skills}

    mode_choices = [
        ("Chat", "chat"),
        ("Transcribe", "transcribe"),
        ("Agent", "agent"),
        ("Wake Word", "wake_word"),
    ] + [(s.name, f"skill:{s.slug}") for s in skills]

    all_voices = list_voices()
    # Extend VOICE_LANG_MAP with any custom voices (default to current lang)
    for v in all_voices:
        if v not in VOICE_LANG_MAP:
            VOICE_LANG_MAP[v] = KOKORO_LANG

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------
    def on_mode_change(mode: str):
        if mode.startswith("skill:"):
            slug = mode[6:]
            skill = skills_map.get(slug)
            if skill:
                _config.system_prompt = skill.system_prompt
                _config.voice = skill.voice
                _config.lang = skill.lang
                _config.max_tokens = skill.max_tokens
                _config.temperature = skill.temperature
                _config.llm_url = skill.llm_url or LLM_URL
                _config.model = skill.model or LLM_SERVED_NAME
                voice_update = gr.update(value=skill.voice)
                temp_update = gr.update(value=skill.temperature)
                tokens_update = gr.update(value=skill.max_tokens)
            else:
                voice_update = gr.update()
                temp_update = gr.update()
                tokens_update = gr.update()
        else:
            prompt = AGENT_SYSTEM_PROMPT if mode == "agent" else CHAT_SYSTEM_PROMPT
            _config.system_prompt = prompt
            _config.voice = KOKORO_VOICE
            _config.lang = KOKORO_LANG
            _config.max_tokens = 150
            _config.temperature = 0.7
            _config.llm_url = LLM_URL
            _config.model = LLM_SERVED_NAME
            voice_update = gr.update(value=KOKORO_VOICE)
            temp_update = gr.update(value=0.7)
            tokens_update = gr.update(value=150)

        _config.mode = mode
        is_wake = mode == "wake_word"
        is_transcribe = mode == "transcribe"

        return (
            gr.update(visible=is_wake),       # wake_word_box
            gr.update(visible=is_transcribe),  # transcript_col
            voice_update,
            temp_update,
            tokens_update,
        )

    def on_voice_change(voice: str):
        _config.voice = voice
        _config.lang = VOICE_LANG_MAP.get(voice, KOKORO_LANG)

    def on_clear_history():
        agent.clear_history()

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------
    with gr.Blocks(
        title="protoVoice",
        css="#transcript-col { margin-top: 8px; }",
    ) as demo:

        # Header
        gr.Markdown("## protoVoice")

        # Wake word input — shown only when mode = wake_word
        wake_word_box = gr.Textbox(
            label="Trigger phrase",
            placeholder="e.g. Hey Proto",
            visible=False,
            interactive=True,
            max_lines=1,
        )

        # Main audio stream
        Stream(
            ReplyOnPause(
                voice_handler,
                algo_options=_algo_options,
                output_sample_rate=24000,
                can_interrupt=True,
            ),
            modality="audio",
            mode="send-receive",
            rtc_configuration={"iceServers": [{"urls": "stun:stun.l.google.com:19302"}]},
        )

        # Transcript panel — shown only when mode = transcribe
        with gr.Column(visible=False, elem_id="transcript-col") as transcript_col:
            transcript_box = gr.Textbox(
                label="Transcript",
                lines=10,
                max_lines=20,
                interactive=False,
                show_copy_button=True,
            )
            clear_transcript_btn = gr.Button("Clear transcript", size="sm", variant="secondary")

        # Settings sidebar — native Gradio drawer
        with gr.Sidebar(label="Settings", open=False, position="right"):
            gr.Markdown("**Mode**")
            mode_dd = gr.Dropdown(
                choices=mode_choices,
                value="chat",
                label=None,
                show_label=False,
                interactive=True,
            )

            gr.Markdown("**VAD**")
            speech_thresh = gr.Slider(
                0.0, 1.0, value=0.1, step=0.05,
                label="Speech threshold",
                info="Higher = less sensitive",
            )
            start_thresh = gr.Slider(
                0.0, 1.0, value=0.5, step=0.05,
                label="Start threshold",
            )
            chunk_dur = gr.Slider(
                0.2, 1.2, value=0.6, step=0.1,
                label="Chunk duration (s)",
                info="Latency vs accuracy",
            )

            gr.Markdown("**Voice**")
            voice_dd = gr.Dropdown(
                choices=all_voices,
                value=KOKORO_VOICE,
                label="TTS voice",
                interactive=True,
            )

            gr.Markdown("**LLM**")
            llm_url_box = gr.Textbox(
                label="Endpoint URL",
                value=LLM_URL,
                placeholder="http://gateway:8000/v1",
                interactive=True,
                max_lines=1,
            )
            llm_model_box = gr.Textbox(
                label="Model name",
                value=LLM_SERVED_NAME,
                placeholder="gpt-4o, claude-3-5-sonnet, local, …",
                interactive=True,
                max_lines=1,
            )
            llm_api_key_box = gr.Textbox(
                label="API key",
                value=LLM_API_KEY,
                placeholder="sk-…  (leave blank for local)",
                type="password",
                interactive=True,
                max_lines=1,
            )
            temp_slider = gr.Slider(0.0, 1.0, value=0.7, step=0.05, label="Temperature")
            tokens_slider = gr.Slider(50, 500, value=150, step=25, label="Max tokens")

            gr.Markdown("**Agent**")
            tz_box = gr.Textbox(
                label="Timezone",
                value="UTC",
                placeholder="e.g. America/New_York",
                interactive=True,
                max_lines=1,
            )

            gr.Markdown("**Session**")
            clear_history_btn = gr.Button("Clear conversation history", size="sm", variant="secondary")

        # ------------------------------------------------------------------
        # Event wiring
        # ------------------------------------------------------------------

        mode_dd.change(
            fn=on_mode_change,
            inputs=[mode_dd],
            outputs=[wake_word_box, transcript_col, voice_dd, temp_slider, tokens_slider],
        )

        # VAD options — mutate AlgoOptions in place
        speech_thresh.change(
            fn=lambda v: setattr(_algo_options, "speech_threshold", v),
            inputs=[speech_thresh],
        )
        start_thresh.change(
            fn=lambda v: setattr(_algo_options, "started_talking_threshold", v),
            inputs=[start_thresh],
        )
        chunk_dur.change(
            fn=lambda v: setattr(_algo_options, "audio_chunk_duration", v),
            inputs=[chunk_dur],
        )

        # Voice / LLM settings
        voice_dd.change(fn=on_voice_change, inputs=[voice_dd])
        temp_slider.change(fn=lambda v: setattr(_config, "temperature", v), inputs=[temp_slider])
        tokens_slider.change(fn=lambda v: setattr(_config, "max_tokens", int(v)), inputs=[tokens_slider])
        wake_word_box.change(fn=lambda v: setattr(_config, "wake_word", v), inputs=[wake_word_box])
        llm_url_box.change(fn=lambda v: setattr(_config, "llm_url", v.strip()), inputs=[llm_url_box])
        llm_model_box.change(fn=lambda v: setattr(_config, "model", v.strip()), inputs=[llm_model_box])
        llm_api_key_box.change(fn=lambda v: setattr(_config, "api_key", v.strip()), inputs=[llm_api_key_box])
        tz_box.change(fn=lambda v: setattr(_config, "timezone", v.strip() or "UTC"), inputs=[tz_box])

        # Session controls
        clear_transcript_btn.click(fn=_clear_transcript, outputs=[transcript_box])
        clear_history_btn.click(fn=on_clear_history)

        # Transcript polling — fires every second
        timer = gr.Timer(value=1.0)
        timer.tick(fn=_get_transcript, outputs=[transcript_box])

    return demo


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    start_vllm()

    def shutdown(sig, frame):
        logger.info("Shutting down...")
        stop_vllm()
        sys.exit(0)

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    prewarm()

    skills = load_skills()
    if skills:
        logger.info(f"Loaded skills: {[s.name for s in skills]}")

    demo = build_ui(skills)

    demo.launch(
        server_name="0.0.0.0",
        server_port=PORT,
        share=False,
        show_error=True,
    )


if __name__ == "__main__":
    main()
