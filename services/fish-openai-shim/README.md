# Fish OpenAI Shim

Wraps the Fish Speech sidecar's proprietary `POST /v1/tts` as the OpenAI `POST /v1/audio/speech` contract so LiteLLM (or any OpenAI SDK client) can route TTS to Fish by name.

Runs as a sidecar container alongside `fish-speech` in the top-level `docker-compose.yml` and listens on `:8093`.

For end-to-end usage (LiteLLM route config, `voice` requirement, request shape), see [TTS Backends → Exposing Fish as an OpenAI-compatible endpoint](../../docs/reference/tts-backends.md).

## Local dev / quick test

```bash
# Direct to the shim on this host (voice omitted OK — shim defaults to "default")
curl -s http://localhost:8093/v1/audio/speech \
  -H "Content-Type: application/json" \
  -d '{"model":"fish-s2-pro","input":"hello","response_format":"mp3"}' \
  -o out.mp3
```

Through a gateway, `voice` is **required** (the OpenAI spec demands it; LiteLLM returns 500 without it):

```bash
curl -s https://api.proto-labs.ai/v1/audio/speech \
  -H "Authorization: Bearer sk-gateway-..." \
  -H "Content-Type: application/json" \
  -d '{"model":"fish-s2-pro","input":"hello","voice":"voice_01","response_format":"mp3"}' \
  -o out.mp3
```

## Response formats

| Format | Streaming | Notes |
|---|:---:|---|
| `wav` (default) | no | One-shot Fish WAV blob |
| `pcm` | yes | Raw int16 LE @ 44100 Hz mono |
| `mp3` | yes | ffmpeg transcodes PCM → mp3 @ 128 kbps |

## Env

- `FISH_URL` (default `http://fish-speech:8092` — the compose service name)
- `FISH_TIMEOUT` (default 180s)
- `FISH_SAMPLE_RATE` (default 44100)
- `FISH_TEMPERATURE` (default 0.8)
- `FISH_TOP_P` (default 0.8)
