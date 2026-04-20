# Use LocalAI (or any all-API stack)

protoVoice's STT, LLM, and TTS are all swappable via env. Point them at [LocalAI](https://localai.io), [OpenRouter](https://openrouter.ai), OpenAI, [vllm-omni](https://github.com/vllm-omni/vllm-omni), or any mix — protoVoice doesn't need a single GPU on the host if you've already got those services running elsewhere.

## All-API setup (no in-process model loads)

If you already have LocalAI hosting Whisper STT + Kokoro TTS + an LLM, this is enough:

```bash
# LLM
START_VLLM=0
LLM_URL=http://localai:8080/v1
LLM_SERVED_NAME=qwen3.5-4b-instruct
LLM_API_KEY=                       # leave blank if your LocalAI is open

# STT
STT_BACKEND=openai
STT_URL=http://localai:8080/v1
STT_MODEL=whisper-1                # whatever name LocalAI registers it as

# TTS
TTS_BACKEND=openai
TTS_OPENAI_URL=http://localai:8080/v1
TTS_OPENAI_MODEL=kokoro            # the model name in LocalAI
TTS_OPENAI_VOICE=af_heart          # the model's voice id
TTS_OPENAI_SAMPLE_RATE=24000       # Kokoro's native rate
```

protoVoice now does no GPU work itself — it's a thin orchestration layer on top of LocalAI. You can run the protovoice container on a CPU box.

## Mixing providers

There's no requirement that all three use the same endpoint. Common splits:

```bash
# Local STT + LLM, OpenAI TTS for premium voice
STT_BACKEND=local                  # in-process Whisper on this host's GPU
LLM_URL=http://gateway:4000/v1
TTS_BACKEND=openai
TTS_OPENAI_URL=https://api.openai.com/v1
TTS_OPENAI_API_KEY=sk-...
TTS_OPENAI_MODEL=tts-1-hd
TTS_OPENAI_VOICE=nova
```

```bash
# OpenAI for STT, local Fish for cloned voice, gateway LLM
STT_BACKEND=openai
STT_URL=https://api.openai.com/v1
STT_API_KEY=sk-...
LLM_URL=http://gateway:4000/v1
TTS_BACKEND=fish                   # voice cloning
```

## What works with which backend

| Feature | local Whisper | OpenAI STT | Fish TTS | Kokoro TTS | OpenAI TTS |
|:---|:---:|:---:|:---:|:---:|:---:|
| Streaming voice in/out | ✓ | ✓ | ✓ | ✓ | ✓ |
| One-shot transcribe (clone endpoint) | ✓ | ✓ | — | — | — |
| Voice cloning (`/api/voice/clone`) | — | — | ✓ | ✗ | ✗ |
| Inline prosody tags (`[hmm]`, `[pause]`) | — | — | ✓ | ✗ | depends on model |

The clone endpoint guards itself — if `TTS_BACKEND` isn't `fish`, it returns an error explaining cloning needs Fish.

## Health check

```bash
curl http://localhost:7867/healthz
# {"status":"ok","stt_backend":"openai","tts_backend":"openai", ...}
```

`stt_backend` and `tts_backend` reflect what's actually wired.

## Latency considerations

Going all-API trades local-GPU latency for network round-trips. Rough budgets:

| Path | Local (GPU on this box) | API (LAN gateway) | API (cloud) |
|:---|:---:|:---:|:---:|
| STT (8s utterance) | ~55 ms | ~150-300 ms | ~600-1500 ms |
| LLM TTFB | ~40 ms | ~100-200 ms | ~300-800 ms |
| TTS TTFA (Fish-quality) | ~580 ms | ~200-400 ms (LocalAI) | ~400-800 ms (OpenAI tts-1) |

Sub-second turn-around is realistic with a LAN gateway. Cloud-only paths push to 1-2 seconds.

## Examples

**Joe's setup**: LocalAI hosts Whisper + Kokoro + an LLM on the same box.

```bash
START_VLLM=0 \
LLM_URL=http://localai:8080/v1 LLM_SERVED_NAME=qwen3.5-4b \
STT_BACKEND=openai STT_URL=http://localai:8080/v1 STT_MODEL=whisper-1 \
TTS_BACKEND=openai TTS_OPENAI_URL=http://localai:8080/v1 TTS_OPENAI_MODEL=kokoro TTS_OPENAI_VOICE=af_heart \
docker compose up -d protovoice
```

(The protovoice container has no GPU work to do; you can drop the `runtime: nvidia` and `device_ids` from the compose file in this configuration.)
