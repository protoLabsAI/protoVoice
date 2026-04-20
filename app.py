#!/usr/bin/env python3
"""protoVoice — Pipecat pipeline with duplex filler (through M2).

Pipeline:

  browser mic → SmallWebRTCTransport.input()
              → LocalWhisperSTT
              → user aggregator (VAD attached here in pipecat 1.0)
              → OpenAILLMService — has `deep_research` tool registered
              → TTS (Fish sidecar by default, Kokoro fallback)
              → SmallWebRTCTransport.output()
              → assistant aggregator

Duplex (M2):
  - on `on_function_calls_started`: queue a TTSSpeakFrame opening filler
  - `_progress_loop()`: emit periodic progress phrases while the tool runs
  - tool handlers are wrapped so they cancel the progress loop on return

Still ahead: M3 async tool inbox + push-interrupt (`cancel_on_interruption=False`),
M4 real tool set, M5 memory + skills + SOUL.
"""

import argparse
import asyncio
import logging
import os
import signal
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path

# Route HF downloads to the mounted model cache before transformers imports anything.
os.environ.setdefault("HF_HOME", os.environ.get("MODEL_DIR", "/models"))

import httpx
from fastapi import BackgroundTasks, FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.services.openai.llm import OpenAILLMService
from pipecat.transports.base_transport import TransportParams
from pipecat.transports.smallwebrtc.request_handler import (
    SmallWebRTCPatchRequest,
    SmallWebRTCRequest,
    SmallWebRTCRequestHandler,
)
from pipecat.frames.frames import TTSSpeakFrame
from pipecat.transports.smallwebrtc.transport import SmallWebRTCTransport

from a2a.registry import AgentRegistry
from a2a.server import register_a2a_routes
from agent.delivery import DeliveryController
from agent.filler import Settings as FillerSettings, Verbosity, opening_filler, progress_filler
from agent.tools import ASYNC_TOOL_NAMES, register_tools
from memory.window import MemoryManager
from skills.loader import load_skills, write_voice_clone_skill
from skills.models import DEFAULT_SOUL_SLUG, Skill
from voice import lifecycle
from voice.stt import LocalWhisperSTT, prewarm as prewarm_stt, transcribe_bytes
from voice.tts import TTS_BACKEND, make_tts, prewarm as prewarm_tts
from voice.tts.fish import add_reference as fish_add_reference, list_references as fish_list_references

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("protovoice")

PORT = int(os.environ.get("PORT", "7866"))
LLM_URL = os.environ.get("LLM_URL", f"http://localhost:{os.environ.get('VLLM_PORT', '8100')}/v1")
LLM_SERVED_NAME = os.environ.get("LLM_SERVED_NAME", "local")
LLM_API_KEY = os.environ.get("LLM_API_KEY", "not-needed")
LLM_MAX_TOKENS = int(os.environ.get("LLM_MAX_TOKENS", "150"))
LLM_TEMPERATURE = float(os.environ.get("LLM_TEMPERATURE", "0.7"))

CONFIG_DIR = Path(os.environ.get("CONFIG_DIR", "config"))

# Skills registry + the currently selected skill. A `SYSTEM_PROMPT` env
# override wins over the skill's prompt (kept for backwards compat).
_SKILLS: dict[str, Skill] = load_skills(CONFIG_DIR)
_ACTIVE_SKILL_SLUG: str = DEFAULT_SOUL_SLUG
_SYSTEM_PROMPT_ENV_OVERRIDE = os.environ.get("SYSTEM_PROMPT") or None

# Session-level filler settings. Module singleton for M5; per-session
# keying lands with multi-tenant in a later milestone.
_FILLER = FillerSettings()

# Agent registry — loaded once at boot, shared across all sessions.
_AGENTS_YAML = Path(os.environ.get("AGENTS_YAML", "config/agents.yaml"))
_AGENT_REGISTRY = AgentRegistry(_AGENTS_YAML)

# Tracks the most-recently-connected session's DeliveryController so the
# A2A callback route can speak push-notified results when a session is
# active. None when no one's connected.
_ACTIVE_DELIVERY: DeliveryController | None = None

# Simple in-process counters for /api/metrics. Reset on process restart.
_METRICS: dict = {
    "boot_at": time.time(),
    "sessions_total": 0,
    "sessions_active": 0,
    "a2a_inbound_total": 0,
    "tool_calls_total": 0,
    "tool_calls_by_name": {},
    "clone_requests_total": 0,
}


def _active_skill() -> Skill:
    return _SKILLS.get(_ACTIVE_SKILL_SLUG) or _SKILLS[DEFAULT_SOUL_SLUG]


def _effective_prompt(skill: Skill) -> str:
    return _SYSTEM_PROMPT_ENV_OVERRIDE or skill.system_prompt


# ---------------------------------------------------------------------------
# Text-only agent — used by inbound A2A (no voice, no tools, one-shot).
# Keeps dependence on the pipeline decoupled so callers can hit /a2a even
# when no WebRTC session is active.
# ---------------------------------------------------------------------------

from openai import AsyncOpenAI

_text_client: AsyncOpenAI | None = None
_A2A_CONTEXTS: dict[str, list[dict]] = {}
_A2A_MAX_TURNS = int(os.environ.get("A2A_MAX_TURNS", "10"))


def _get_text_client() -> AsyncOpenAI:
    global _text_client
    if _text_client is None:
        _text_client = AsyncOpenAI(api_key=LLM_API_KEY, base_url=LLM_URL)
    return _text_client


async def text_agent(message: str, session_id: str) -> str:
    """One-shot text turn — used by the A2A inbound handler.

    Keeps a small per-session history so multi-turn A2A conversations stay
    coherent. No tool calls in this path (yet) — the voice side is where
    tools live.
    """
    _METRICS["a2a_inbound_total"] += 1
    skill = _active_skill()
    history = _A2A_CONTEXTS.setdefault(session_id, [])
    history.append({"role": "user", "content": message})
    messages = [
        {"role": "system", "content": _effective_prompt(skill)},
        *history[-(_A2A_MAX_TURNS * 2):],
    ]
    r = await _get_text_client().chat.completions.create(
        model=LLM_SERVED_NAME,
        messages=messages,
        max_tokens=skill.max_tokens,
        temperature=skill.temperature,
        extra_body={"chat_template_kwargs": {"enable_thinking": False}},
    )
    reply = (r.choices[0].message.content or "").strip()
    history.append({"role": "assistant", "content": reply})
    # Keep the per-session buffer bounded.
    if len(history) > _A2A_MAX_TURNS * 2:
        del history[: len(history) - _A2A_MAX_TURNS * 2]
    return reply

STATIC_DIR = Path(__file__).parent / "static"

_handler = SmallWebRTCRequestHandler()


async def run_bot(webrtc_connection) -> None:
    """One bot instance per connected WebRTC client."""
    # Snapshot the active skill at connect time; the session keeps it even
    # if the operator flips the dropdown mid-call. Matches UX expectation.
    skill = _active_skill()
    tts_backend = skill.tts_backend or TTS_BACKEND
    logger.info(
        f"[session] skill={skill.slug!r} tts_backend={tts_backend} "
        f"voice={skill.voice!r} verbosity={_FILLER.verbosity.value}"
    )

    # Skills may override session-level filler verbosity.
    if skill.filler_verbosity:
        try:
            _FILLER.verbosity = Verbosity(skill.filler_verbosity)
        except ValueError:
            pass

    transport = SmallWebRTCTransport(
        webrtc_connection=webrtc_connection,
        params=TransportParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            audio_out_10ms_chunks=2,
        ),
    )

    stt = LocalWhisperSTT()
    llm = OpenAILLMService(
        api_key=LLM_API_KEY,
        base_url=LLM_URL,
        settings=OpenAILLMService.Settings(
            model=LLM_SERVED_NAME,
            temperature=skill.temperature if skill else LLM_TEMPERATURE,
            max_tokens=skill.max_tokens if skill else LLM_MAX_TOKENS,
            # Qwen3.5/3.6 stream `reasoning` + empty `content` by default.
            # extra_body is forwarded as OpenAI "extra_body" — vLLM accepts
            # `chat_template_kwargs` to toggle the thinking template off, so
            # the model produces spoken-style content directly.
            extra={
                "extra_body": {
                    "chat_template_kwargs": {"enable_thinking": False},
                },
            },
        ),
    )
    tts_kwargs: dict = {"backend": tts_backend}
    if skill.voice:
        if tts_backend == "kokoro":
            tts_kwargs["voice"] = skill.voice
            if skill.lang:
                tts_kwargs["lang"] = skill.lang
        elif tts_backend == "fish":
            tts_kwargs["reference_id"] = skill.voice
    tts = make_tts(**tts_kwargs)

    # Delivery controller — observes VAD + transcripts, drains push deliveries.
    delivery = DeliveryController()

    # `_cancel_progress` is defined below; register_tools captures it via
    # closure so each SYNC tool handler auto-stops the progress loop on return.
    def _cancel_progress():
        while progress_tasks:
            t = progress_tasks.pop()
            t.cancel()

    tools_schema = register_tools(
        llm,
        on_finish=_cancel_progress,
        delivery=delivery,
        registry=_AGENT_REGISTRY,
    )

    context = LLMContext(
        [{"role": "system", "content": _effective_prompt(skill)}],
        tools=tools_schema,
    )

    memory = MemoryManager(context, summarizer_llm=llm)
    user_agg, assistant_agg = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(vad_analyzer=SileroVADAnalyzer()),
    )

    pipeline = Pipeline([
        transport.input(),
        stt,
        user_agg,
        # Placed after user_agg so it sees TranscriptionFrames and VAD frames
        # produced by the aggregator. Its downstream push_frame goes straight
        # to TTS → transport output.
        delivery,
        llm,
        tts,
        transport.output(),
        assistant_agg,
        # Memory sits at the tail so it observes LLMFullResponseEndFrame on
        # each turn and prunes/summarizes asynchronously without blocking.
        memory,
    ])

    task = PipelineTask(
        pipeline,
        params=PipelineParams(enable_metrics=True),
    )

    # Wire the delivery controller's out-of-band emit path now that the task
    # exists. queue_frame is the only safe way to inject frames from a
    # foreign coroutine (e.g. the slow_research background task).
    delivery.set_emitter(task.queue_frame)

    # --- Duplex speak-while-thinking ---
    # When the LLM dispatches a tool, queue an immediate TTSSpeakFrame so
    # the user hears a filler phrase while the tool runs, plus periodic
    # progress updates. register_tools(on_finish=...) above captures the
    # cancel hook so handlers auto-stop the progress loop on return.
    progress_tasks: set[asyncio.Task] = set()

    async def _progress_loop():
        """Emit periodic progress fillers if the tool drags on."""
        try:
            await asyncio.sleep(_FILLER.progress_after_secs)
            while True:
                phrase = progress_filler(_FILLER)
                if phrase:
                    logger.info(f"[filler:progress] {phrase!r}")
                    await task.queue_frame(TTSSpeakFrame(phrase))
                await asyncio.sleep(_FILLER.progress_interval_secs)
        except asyncio.CancelledError:
            pass

    @llm.event_handler("on_function_calls_started")
    async def _on_tool_start(_svc, function_calls):
        names = [fc.function_name for fc in function_calls]
        any_async = any(n in ASYNC_TOOL_NAMES for n in names)
        logger.info(
            f"[filler:open] tool={','.join(names)} "
            f"verbosity={_FILLER.verbosity.value} async={any_async}"
        )
        # Counters
        _METRICS["tool_calls_total"] += len(names)
        for n in names:
            _METRICS["tool_calls_by_name"][n] = _METRICS["tool_calls_by_name"].get(n, 0) + 1
        phrase = opening_filler(_FILLER)
        if phrase:
            await task.queue_frame(TTSSpeakFrame(phrase))
        # Async tools drive their own narration via the DeliveryController
        # when they complete. Running the progress loop for them leaks a
        # never-cancelled asyncio task (on_finish doesn't fire — the sync
        # wrapper isn't on their path) and the user hears filler forever.
        if not any_async:
            progress_tasks.add(asyncio.create_task(_progress_loop()))

    @llm.event_handler("on_function_calls_cancelled")
    async def _on_tool_cancel(_svc, _calls):
        logger.info("[filler] tool cancelled (barge-in)")
        _cancel_progress()

    @transport.event_handler("on_client_connected")
    async def _on_connect(_t, _c):
        global _ACTIVE_DELIVERY
        _ACTIVE_DELIVERY = delivery
        _METRICS["sessions_total"] += 1
        _METRICS["sessions_active"] += 1
        logger.info("client connected")

    @transport.event_handler("on_client_disconnected")
    async def _on_disconnect(_t, _c):
        global _ACTIVE_DELIVERY
        logger.info("client disconnected")
        if _ACTIVE_DELIVERY is delivery:
            _ACTIVE_DELIVERY = None
        _METRICS["sessions_active"] = max(0, _METRICS["sessions_active"] - 1)
        _cancel_progress()
        await task.cancel()

    await PipelineRunner(handle_sigint=False).run(task)


# ---------------------------------------------------------------------------
# Prewarm
# ---------------------------------------------------------------------------

def prewarm_llm() -> None:
    try:
        httpx.post(
            f"{LLM_URL}/chat/completions",
            json={
                "model": LLM_SERVED_NAME,
                "messages": [{"role": "user", "content": "Hi"}],
                "max_tokens": 1,
                "temperature": 0,
                "chat_template_kwargs": {"enable_thinking": False},
            },
            headers={"Authorization": f"Bearer {LLM_API_KEY}"} if LLM_API_KEY else {},
            timeout=30.0,
        )
        logger.info("LLM warm")
    except Exception as e:
        logger.warning(f"LLM prewarm skipped: {e}")


def prewarm_all() -> None:
    logger.info(f"Prewarming (tts_backend={TTS_BACKEND})")
    prewarm_stt()
    prewarm_tts()
    prewarm_llm()


# ---------------------------------------------------------------------------
# FastAPI
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    lifecycle.start()
    # Prewarm off the event loop so the startup handshake isn't blocked by
    # Fish's ~2min cold compile; we just begin work in the background.
    asyncio.get_running_loop().run_in_executor(None, prewarm_all)
    try:
        yield
    finally:
        await _handler.close()
        lifecycle.stop()


app = FastAPI(title="protoVoice", lifespan=lifespan)


@app.post("/api/offer")
async def offer(req: SmallWebRTCRequest, bg: BackgroundTasks):
    async def on_conn(conn):
        bg.add_task(run_bot, conn)
    return await _handler.handle_web_request(request=req, webrtc_connection_callback=on_conn)


@app.patch("/api/offer")
async def ice(req: SmallWebRTCPatchRequest):
    await _handler.handle_patch_request(req)
    return {"status": "success"}


@app.get("/healthz")
async def health():
    return {
        "status": "ok",
        "tts_backend": TTS_BACKEND,
        "verbosity": _FILLER.verbosity.value,
        "known_agents": _AGENT_REGISTRY.names(),
        "skill": _ACTIVE_SKILL_SLUG,
        "skills": list(_SKILLS.keys()),
    }


@app.get("/api/metrics")
async def metrics():
    uptime = time.time() - _METRICS["boot_at"]
    return {
        **_METRICS,
        "uptime_secs": round(uptime, 1),
    }


@app.get("/api/verbosity")
async def get_verbosity():
    return {"verbosity": _FILLER.verbosity.value}


@app.post("/api/verbosity")
async def set_verbosity(body: dict):
    from agent.filler import Verbosity
    try:
        _FILLER.verbosity = Verbosity(body.get("level", "").lower())
    except ValueError:
        return {"error": "level must be silent|brief|narrated|chatty"}
    return {"verbosity": _FILLER.verbosity.value}


@app.get("/api/skills")
async def get_skills():
    return {
        "active": _ACTIVE_SKILL_SLUG,
        "skills": [
            {"slug": s.slug, "name": s.name, "description": s.description}
            for s in _SKILLS.values()
        ],
    }


@app.post("/api/skills")
async def set_skill(body: dict):
    global _ACTIVE_SKILL_SLUG
    slug = (body.get("slug") or "").strip()
    if slug not in _SKILLS:
        return {"error": f"unknown skill: {slug}", "available": list(_SKILLS.keys())}
    _ACTIVE_SKILL_SLUG = slug
    return {"active": _ACTIVE_SKILL_SLUG}


# ---------------------------------------------------------------------------
# Voice cloning — upload a reference clip, optionally auto-transcribe, save
# on the Fish server, and stamp a new skill so it shows in the dropdown.
# ---------------------------------------------------------------------------

import re as _re
from fastapi import File, Form, UploadFile

_SLUG_RE = _re.compile(r"^[a-z0-9][a-z0-9\-_]{1,63}$")


@app.get("/api/voice/references")
async def voice_references():
    """List the Fish server's saved voice references."""
    if TTS_BACKEND != "fish":
        return {"backend": TTS_BACKEND, "references": []}
    return {"backend": "fish", "references": fish_list_references()}


@app.post("/api/voice/clone")
async def voice_clone(
    audio: UploadFile = File(...),
    slug: str = Form(...),
    name: str | None = Form(None),
    transcript: str | None = Form(None),
    description: str = Form(""),
):
    """Upload a reference clip, optionally auto-transcribe, save on Fish,
    and create a new skill that uses it."""
    global _SKILLS
    slug = slug.strip().lower()
    if not _SLUG_RE.match(slug):
        return {"error": "slug must be lowercase letters/numbers/hyphens (2-64 chars)"}
    if slug in _SKILLS:
        return {"error": f"slug '{slug}' already exists"}

    audio_bytes = await audio.read()
    if not audio_bytes:
        return {"error": "empty audio upload"}

    # Auto-transcribe if the user didn't provide one.
    final_transcript = (transcript or "").strip()
    auto_transcribed = False
    if not final_transcript:
        try:
            final_transcript = await asyncio.to_thread(transcribe_bytes, audio_bytes)
            auto_transcribed = True
        except Exception as e:
            logger.exception("[voice/clone] whisper transcription failed")
            return {"error": f"auto-transcribe failed: {e}"}
        if not final_transcript:
            return {"error": "auto-transcribe produced empty text — provide a transcript manually"}

    # Save on Fish using the slug as the reference id. Fish's regex requires
    # `^[a-zA-Z0-9\-_ ]+$` which our stricter slug_re already satisfies.
    ok = await asyncio.to_thread(fish_add_reference, slug, audio_bytes, final_transcript)
    if not ok:
        return {"error": "Fish server rejected the reference — check the sidecar logs"}

    # Stamp a skill YAML and hot-reload the skills dict.
    display_name = (name or slug.replace("-", " ").title()).strip()
    write_voice_clone_skill(
        slug=slug,
        name=display_name,
        reference_id=slug,
        description=description.strip(),
        config_dir=CONFIG_DIR,
    )
    _SKILLS = load_skills(CONFIG_DIR)
    _METRICS["clone_requests_total"] += 1
    return {
        "ok": True,
        "slug": slug,
        "name": display_name,
        "transcript": final_transcript,
        "auto_transcribed": auto_transcribed,
    }


@app.get("/")
async def index():
    # Canonical p2p-webrtc client — adds BOTH audio+video transceivers
    # (required by SmallWebRTCTransport) and queues ICE until pc_id is known.
    return FileResponse(str(STATIC_DIR / "index.html"))


if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# Inbound A2A — other agents can send us JSON-RPC `message/send`.
register_a2a_routes(
    app,
    text_agent=text_agent,
    delivery_provider=lambda: _ACTIVE_DELIVERY,
)


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=PORT)
    args = parser.parse_args()

    def _shutdown(_sig, _frame):
        logger.info("Shutting down")
        lifecycle.stop()
        sys.exit(0)

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=args.port)


if __name__ == "__main__":
    main()
