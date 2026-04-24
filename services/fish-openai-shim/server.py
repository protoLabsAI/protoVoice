"""
OpenAI-compatible /v1/audio/speech shim for Fish Speech S2-Pro.

Wraps Fish's proprietary POST /v1/tts (ServeTTSRequest) with the OpenAI
audio-speech contract so LiteLLM / OpenAI SDK clients can hit it by name:

    openai.audio.speech.create(model="fish-s2-pro", voice="default", input="Hello")
                                        └─ ignored        └─ reference_id passthrough

Endpoints:
    POST /v1/audio/speech       OpenAI-compatible TTS
    GET  /v1/models             lists "fish-s2-pro" + saved reference voices
    GET  /health                liveness

Usage:
    FISH_URL=http://localhost:8092 uvicorn server:app --host 0.0.0.0 --port 8093

LiteLLM gateway config (add to config.yaml):
    model_list:
      - model_name: fish-s2-pro
        litellm_params:
          model: openai/fish-s2-pro
          api_base: http://protolabs:8093/v1
          api_key: fake-key-not-needed

Response formats:
    wav   (default)   one-shot, Fish returns full WAV blob, lowest complexity
    pcm               streaming int16 LE PCM @ 44100 Hz, lowest TTFA (<200ms)
    mp3               streaming PCM → ffmpeg stdin → mp3 chunks, ~50ms extra TTFA
"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
from typing import AsyncIterator, Literal

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field

logger = logging.getLogger("fish-shim")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

FISH_URL = os.environ.get("FISH_URL", "http://localhost:8092").rstrip("/")
FISH_TIMEOUT = float(os.environ.get("FISH_TIMEOUT", "180"))
FISH_SAMPLE_RATE = int(os.environ.get("FISH_SAMPLE_RATE", "44100"))
DEFAULT_TEMPERATURE = float(os.environ.get("FISH_TEMPERATURE", "0.8"))
DEFAULT_TOP_P = float(os.environ.get("FISH_TOP_P", "0.8"))

app = FastAPI(title="Fish-Speech OpenAI Shim", version="0.1.0")


class SpeechRequest(BaseModel):
    model: str = "fish-s2-pro"
    input: str
    voice: str = "default"                  # -> Fish reference_id (or "default" for built-in)
    response_format: Literal["wav", "pcm", "mp3"] = "wav"
    speed: float = Field(1.0, ge=0.25, le=4.0)


def _wav_header(sample_rate: int, num_channels: int = 1, bits_per_sample: int = 16) -> bytes:
    """16-bit PCM WAV header with 0xFFFFFFFF sizes (streaming, unknown length)."""
    byte_rate = sample_rate * num_channels * bits_per_sample // 8
    block_align = num_channels * bits_per_sample // 8
    return (
        b"RIFF" + b"\xff\xff\xff\xff"
        + b"WAVEfmt " + (16).to_bytes(4, "little")
        + (1).to_bytes(2, "little")            # PCM
        + num_channels.to_bytes(2, "little")
        + sample_rate.to_bytes(4, "little")
        + byte_rate.to_bytes(4, "little")
        + block_align.to_bytes(2, "little")
        + bits_per_sample.to_bytes(2, "little")
        + b"data" + b"\xff\xff\xff\xff"
    )


def _build_fish_payload(req: SpeechRequest, streaming: bool) -> dict:
    payload: dict = {
        "text": req.input,
        "format": "wav",
        "streaming": streaming,
        "temperature": DEFAULT_TEMPERATURE,
        "top_p": DEFAULT_TOP_P,
        "chunk_length": 200,
        "normalize": True,
    }
    if req.voice and req.voice != "default":
        payload["reference_id"] = req.voice
    return payload


async def _stream_pcm(req: SpeechRequest) -> AsyncIterator[bytes]:
    """Stream raw int16 PCM from Fish. Handles odd-byte alignment."""
    payload = _build_fish_payload(req, streaming=True)
    carry = b""
    async with httpx.AsyncClient(timeout=FISH_TIMEOUT) as client:
        async with client.stream("POST", f"{FISH_URL}/v1/tts", json=payload) as resp:
            if resp.status_code != 200:
                body = (await resp.aread()).decode("utf-8", errors="ignore")
                raise HTTPException(status_code=502, detail=f"Fish {resp.status_code}: {body[:200]}")
            async for chunk in resp.aiter_bytes():
                if not chunk:
                    continue
                buf = carry + chunk
                if len(buf) & 1:
                    carry = buf[-1:]
                    buf = buf[:-1]
                else:
                    carry = b""
                if buf:
                    yield buf
    if carry:
        yield carry + b"\x00"


async def _stream_wav(req: SpeechRequest) -> AsyncIterator[bytes]:
    """Prepend a WAV header then stream PCM body."""
    yield _wav_header(FISH_SAMPLE_RATE, 1, 16)
    async for buf in _stream_pcm(req):
        yield buf


async def _stream_mp3(req: SpeechRequest) -> AsyncIterator[bytes]:
    """Pipe PCM bytes through ffmpeg stdin → mp3 stdout."""
    ffmpeg = await asyncio.create_subprocess_exec(
        "ffmpeg", "-hide_banner", "-loglevel", "error",
        "-f", "s16le", "-ar", str(FISH_SAMPLE_RATE), "-ac", "1", "-i", "pipe:0",
        "-f", "mp3", "-b:a", "128k", "pipe:1",
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )

    async def feed() -> None:
        try:
            async for chunk in _stream_pcm(req):
                ffmpeg.stdin.write(chunk)
                await ffmpeg.stdin.drain()
        except Exception as e:
            logger.warning(f"pcm feed error: {e}")
        finally:
            try:
                ffmpeg.stdin.close()
            except Exception:
                pass

    feed_task = asyncio.create_task(feed())
    try:
        while True:
            chunk = await ffmpeg.stdout.read(8192)
            if not chunk:
                break
            yield chunk
    finally:
        await feed_task
        try:
            await ffmpeg.wait()
        except Exception:
            pass


@app.post("/v1/audio/speech")
async def audio_speech(req: SpeechRequest):
    if not req.input.strip():
        raise HTTPException(400, "input is empty")
    if req.speed != 1.0:
        logger.warning(f"speed={req.speed} requested; Fish has no speed param, ignoring")

    # Non-streaming WAV (simplest — Fish returns a full WAV blob).
    if req.response_format == "wav":
        payload = _build_fish_payload(req, streaming=False)
        async with httpx.AsyncClient(timeout=FISH_TIMEOUT) as client:
            r = await client.post(f"{FISH_URL}/v1/tts", json=payload)
        if r.status_code != 200:
            raise HTTPException(status_code=502, detail=f"Fish {r.status_code}: {r.text[:200]}")
        return Response(content=r.content, media_type="audio/wav")

    if req.response_format == "pcm":
        return StreamingResponse(_stream_pcm(req), media_type="audio/pcm")

    if req.response_format == "mp3":
        return StreamingResponse(_stream_mp3(req), media_type="audio/mpeg")

    raise HTTPException(400, f"unsupported response_format: {req.response_format}")


@app.get("/v1/models")
async def list_models():
    """Expose fish-s2-pro plus any server-saved reference voices."""
    voices: list[str] = []
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{FISH_URL}/v1/references/list", headers={"Accept": "application/json"})
            if r.status_code == 200:
                voices = r.json().get("reference_ids", [])
    except Exception as e:
        logger.warning(f"references/list failed: {e}")

    return {
        "object": "list",
        "data": [
            {"id": "fish-s2-pro", "object": "model", "owned_by": "fish-audio"},
            *[{"id": v, "object": "model", "owned_by": "fish-audio"} for v in voices],
        ],
    }


@app.get("/health")
async def health():
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            r = await client.get(f"{FISH_URL}/v1/references/list", headers={"Accept": "application/json"})
        fish_ok = r.status_code == 200
    except Exception:
        fish_ok = False
    return {"shim": "ok", "fish_reachable": fish_ok, "fish_url": FISH_URL}
