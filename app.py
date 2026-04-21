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

# Load .env BEFORE any other module reads os.environ. python-dotenv leaves
# already-set env vars alone (shell env wins over .env — standard).
# For deployed boxes, Infisical (or whichever secrets manager) injects
# env vars at container start; this block then no-ops because the file
# isn't there. Local dev + CI keep a .env; production doesn't.
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    # Missing dotenv shouldn't crash boot — secrets just have to come
    # from the shell env in that case.
    pass

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

from a2a.server import register_a2a_routes
from agent.backchannel import BackchannelController
from agent.bargein import BargeInGate
from agent.delegates import DelegateRegistry
from agent.echo_guard import (
    ECHO_GUARD_MS,
    HALF_DUPLEX,
    EchoGuardObserver,
    EchoGuardState,
    EchoGuardSuppressor,
)
from agent.delivery import DeliveryController
from agent.prosody import ProsodyTagStripper
from agent.filler import (
    FillerGenerator,
    Latency,
    Settings as FillerSettings,
    Verbosity,
    tool_use_block,
)
from agent.tools import ASYNC_TOOL_NAMES, latency_for, register_tools
from memory.window import MemoryManager
from skills.loader import load_skills, write_voice_clone_skill
from skills.models import DEFAULT_SOUL_SLUG, Skill
from voice import lifecycle
from voice.stt import STT_BACKEND, make_stt, prewarm as prewarm_stt, transcribe_bytes
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

# Generative filler — one module-level instance sharing the same local
# LLM endpoint that the voice pipeline uses. Cheap to keep warm.
_FILLER_GEN = FillerGenerator(
    llm_url=LLM_URL,
    model=LLM_SERVED_NAME,
    api_key=LLM_API_KEY,
    settings=_FILLER,
)

# Delegate registry — A2A agents + OpenAI-compat endpoints the agent can
# hand off to via `delegate_to`. Loaded once at boot.
_DELEGATES_YAML = Path(os.environ.get("DELEGATES_YAML", "config/delegates.yaml"))
_DELEGATES = DelegateRegistry(_DELEGATES_YAML)

# Tracks the most-recently-connected session's DeliveryController so the
# A2A callback route can speak push-notified results when a session is
# active. None when no one's connected.
_ACTIVE_DELIVERY: DeliveryController | None = None


# ---------------------------------------------------------------------------
# Audio + turn enhancements (echo guard already imported above)
# Env-driven so the heavy/optional deps stay opt-in.
# ---------------------------------------------------------------------------

NOISE_FILTER = os.environ.get("NOISE_FILTER", "off").lower()  # off | rnnoise
SMART_TURN = os.environ.get("SMART_TURN", "off").lower()      # off | local


def _build_audio_in_filter():
    """Return a BaseAudioFilter for TransportParams.audio_in_filter, or None."""
    if NOISE_FILTER == "rnnoise":
        try:
            from pipecat.audio.filters.rnnoise_filter import RNNoiseFilter
        except ImportError as e:
            logger.error(
                "NOISE_FILTER=rnnoise but pipecat[rnnoise] not installed: %s", e
            )
            return None
        logger.info("Audio in-filter: RNNoise")
        return RNNoiseFilter()
    if NOISE_FILTER != "off":
        logger.warning(f"Unknown NOISE_FILTER={NOISE_FILTER!r}; disabling")
    return None


def _build_user_turn_strategies():
    """Return a `UserTurnStrategies` object wrapping a smart-turn analyzer,
    or None for naive VAD-only behaviour. Smart-turn discriminates real
    turn-ends from mid-thought pauses + echo bleed."""
    if SMART_TURN in ("local", "v3"):
        try:
            from pipecat.audio.turn.smart_turn.local_smart_turn_v3 import (
                LocalSmartTurnAnalyzerV3,
            )
            from pipecat.turns.user_stop import TurnAnalyzerUserTurnStopStrategy
            from pipecat.turns.user_turn_strategies import UserTurnStrategies
        except ImportError as e:
            logger.error(
                "SMART_TURN=local but pipecat[local-smart-turn] not installed: %s", e
            )
            return None
        logger.info("Turn analyzer: LocalSmartTurnAnalyzerV3 (bundled CPU model)")
        return UserTurnStrategies(
            stop=[TurnAnalyzerUserTurnStopStrategy(turn_analyzer=LocalSmartTurnAnalyzerV3())]
        )
    if SMART_TURN != "off":
        logger.warning(f"Unknown SMART_TURN={SMART_TURN!r}; disabling")
    return None


# Echo-guard state — shared across observer and suppressor for THIS session.
# Module-level since pipeline is single-tenant for now.
_ECHO_STATE = EchoGuardState()

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


def _effective_prompt(skill: Skill, tts_backend: str) -> str:
    """Compose the system prompt = persona + TOOL USE block.

    The TOOL USE block is verbosity-and-backend-aware — it instructs the
    LLM to emit a brief preamble before each tool call (the new "filler"
    primitive), with prosody guidance per backend.
    """
    base = _SYSTEM_PROMPT_ENV_OVERRIDE or skill.system_prompt
    return base + "\n\n" + tool_use_block(_FILLER.verbosity, tts_backend)


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
            # Optional in-filter (rnnoise) for noise reduction on the mic
            # stream. Wired only when NOISE_FILTER is enabled in env.
            audio_in_filter=_build_audio_in_filter(),
        ),
    )

    stt = make_stt()
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
    # vLLM (and most non-OpenAI endpoints) reject `role: developer`. Pipecat
    # uses that role to inject async-tool results back into the context;
    # without this flip we 400 on every turn after a slow_research returns.
    # The adapter converts developer → user when this is False.
    llm.supports_developer_role = False
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

    # Backchannel controller — emits brief listener-acks ("mm-hmm") during
    # long user utterances. Reuses the shared FillerGenerator.
    backchannel = BackchannelController(generator=_FILLER_GEN, tts_backend=tts_backend)

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
        delegates=_DELEGATES,
    )

    # Per-skill tool restriction. If skill.tools is non-empty, scope the
    # ToolsSchema down to that allow-list — the LLM only SEES (and so
    # only calls) the listed names. Handlers stay registered on the LLM
    # service either way; if the schema doesn't expose them, they can't
    # be reached.
    if skill.tools:
        from pipecat.adapters.schemas.tools_schema import ToolsSchema
        allowed = set(skill.tools)
        kept = [s for s in tools_schema.standard_tools if s.name in allowed]
        unknown = allowed - {s.name for s in tools_schema.standard_tools}
        if unknown:
            logger.warning(
                f"[skill] {skill.slug!r}: tools={list(unknown)} not in registry; "
                "ignored"
            )
        if kept:
            logger.info(
                f"[skill] {skill.slug!r} restricted to tools: "
                f"{[s.name for s in kept]}"
            )
            tools_schema = ToolsSchema(standard_tools=kept)
        else:
            logger.warning(
                f"[skill] {skill.slug!r}: tools list matched zero registered tools; "
                "exposing all (refuse to leave the agent toolless)"
            )

    context = LLMContext(
        [{"role": "system", "content": _effective_prompt(skill, tts_backend)}],
        tools=tools_schema,
    )

    memory = MemoryManager(context, summarizer_llm=llm)
    _turn_strategies = _build_user_turn_strategies()
    _user_agg_kwargs: dict = {"vad_analyzer": SileroVADAnalyzer()}
    if _turn_strategies is not None:
        # Only pass user_turn_strategies when we actually built one — passing
        # None keeps the default (naive VAD endpointing).
        _user_agg_kwargs["user_turn_strategies"] = _turn_strategies
    user_agg, assistant_agg = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(**_user_agg_kwargs),
    )

    pipeline = Pipeline([
        transport.input(),
        # Echo-guard sits IMMEDIATELY after transport.input — drops mic
        # audio while the bot is speaking (HALF_DUPLEX) and for ECHO_GUARD_MS
        # after it stops. VAD downstream never sees the suppressed audio.
        EchoGuardSuppressor(_ECHO_STATE),
        stt,
        user_agg,
        # Adaptive barge-in gate — suppresses VAD-triggered interrupts
        # that resolve within the grace window as coughs / backchannels /
        # background noise. Real interrupts still fire, just confirmed.
        BargeInGate(),
        # Both placed after the gate — they need TranscriptionFrames and
        # VAD frames produced by the aggregator. Push downstream into TTS.
        backchannel,
        delivery,
        llm,
        # Backends that can't consume prosody tags need them stripped
        # before the TTS call; Fish consumes `[softly]` / `[pause:300]`
        # directly as control tokens, so pass those through.
        *([ProsodyTagStripper()] if tts_backend != "fish" else []),
        tts,
        transport.output(),
        # Strip Fish-style prosody tags ([softly], [pause:300ms], …) from
        # TextFrames BEFORE the assistant aggregator sees them, so tags
        # don't accumulate in LLM context for future turns. Fish has
        # already spoken the tags; Kokoro/OpenAI strip them at the adapter
        # level. This is the context-safety net.
        ProsodyTagStripper(),
        assistant_agg,
        # Memory sits at the tail so it observes LLMFullResponseEndFrame on
        # each turn and prunes/summarizes asynchronously without blocking.
        memory,
    ])

    task = PipelineTask(
        pipeline,
        params=PipelineParams(enable_metrics=True),
        # Observer keeps the echo-guard state in sync with bot speaking
        # frames. Observers see every frame at the pipeline level without
        # being a transformation node.
        observers=[EchoGuardObserver(_ECHO_STATE)],
    )

    # Wire the delivery + backchannel controllers' out-of-band emit paths
    # now that the task exists. queue_frame is the only safe way to inject
    # frames from a foreign coroutine.
    delivery.set_emitter(task.queue_frame)
    backchannel.set_emitter(task.queue_frame)

    # --- Duplex speak-while-thinking ---
    # Pre-tool acknowledgement ("hmm, let me check") is now emitted INLINE
    # by the LLM — see the TOOL USE block in tool_use_block(). Pipecat's
    # OpenAILLMService streams those tokens to TTS BEFORE running the
    # function call, so the user hears them naturally.
    #
    # This file only handles the channels pipecat's main response stream
    # cannot cover:
    #   - SLOW tools: LLM is blocked on the result, can't narrate.
    #     We synthesize "still working" lines via _FILLER_GEN.progress().
    #   - Backchannels during the user's turn: see BackchannelController.
    progress_tasks: set[asyncio.Task] = set()

    def _last_user_text() -> str | None:
        for m in reversed(context.messages):
            if m.get("role") == "user" and m.get("content"):
                c = m["content"]
                return c if isinstance(c, str) else str(c)
        return None

    async def _progress_loop(tool_name: str):
        try:
            await asyncio.sleep(_FILLER.progress_after_secs)
            while True:
                try:
                    phrase = await _FILLER_GEN.progress(
                        tool_name=tool_name,
                        user_utterance=_last_user_text(),
                        tts_backend=tts_backend,
                    )
                except Exception as e:
                    logger.warning(f"[filler:progress] generator raised: {e}")
                    phrase = None
                if phrase:
                    logger.info(f"[filler:progress] {phrase!r}")
                    await task.queue_frame(
                        TTSSpeakFrame(phrase, append_to_context=False)
                    )
                await asyncio.sleep(_FILLER.progress_interval_secs)
        except asyncio.CancelledError:
            pass

    @llm.event_handler("on_function_calls_started")
    async def _on_tool_start(_svc, function_calls):
        names = [fc.function_name for fc in function_calls]
        tier = max((latency_for(n) for n in names), key=lambda l: ["fast","medium","slow"].index(l.value))
        any_async = any(n in ASYNC_TOOL_NAMES for n in names)
        logger.info(
            f"[tool] {','.join(names)} tier={tier.value} async={any_async}"
        )
        _METRICS["tool_calls_total"] += len(names)
        for n in names:
            _METRICS["tool_calls_by_name"][n] = _METRICS["tool_calls_by_name"].get(n, 0) + 1

        # Only SLOW sync tools get the progress narration loop. The opening
        # acknowledgement is handled inline by the LLM via the TOOL USE
        # prompt block. Async tools narrate themselves via DeliveryController.
        if tier is Latency.SLOW and not any_async:
            progress_tasks.add(asyncio.create_task(_progress_loop(names[0])))

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
        "stt_backend": STT_BACKEND,
        "tts_backend": TTS_BACKEND,
        "verbosity": _FILLER.verbosity.value,
        "delegates": [
            {"name": d.name, "type": d.type} for d in _DELEGATES.all()
        ],
        "skill": _ACTIVE_SKILL_SLUG,
        "skills": list(_SKILLS.keys()),
        "audio": {
            "half_duplex": HALF_DUPLEX,
            "echo_guard_ms": ECHO_GUARD_MS,
            "noise_filter": NOISE_FILTER,
            "smart_turn": SMART_TURN,
        },
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
    if TTS_BACKEND != "fish":
        return {
            "error": (
                f"voice cloning requires TTS_BACKEND=fish (currently {TTS_BACKEND!r}). "
                "OpenAI/Kokoro backends use preset voices only."
            )
        }
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
