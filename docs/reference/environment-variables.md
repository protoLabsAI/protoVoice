# Environment Variables

All values have sensible defaults. Set via (in order of precedence):

1. Shell env / `docker compose environment:` — highest precedence
2. A local `.env` file at the repo root (auto-loaded via python-dotenv at startup — gitignored; `.env.example` in the repo is the template)
3. Built-in defaults in the code

For deployed boxes, inject secrets via your secrets manager (Infisical, Vault, SOPS, k8s Secret + envFrom, etc.). The app reads `os.environ` — it doesn't care where values came from.

## Server

| Variable | Default | Purpose |
|:---|:---|:---|
| `PORT` | `7866` | HTTP port the FastAPI/Pipecat server listens on |
| `HF_HOME` | `/models` | HuggingFace cache dir (inside the container) |
| `MODEL_DIR` | `/models` | Alias for `HF_HOME` — set one, both resolve |
| `SYSTEM_PROMPT` | *(built-in)* | Overrides the default voice-assistant system prompt |
| `VERBOSITY` | `brief` | Default filler verbosity: `silent` / `brief` / `narrated` / `chatty` |
| `TZ` | `America/New_York` | Timezone for the `get_datetime` tool |
| `DELEGATES_YAML` | `config/delegates.yaml` | Path to the delegate registry (A2A + OpenAI) |

## LLM

| Variable | Default | Purpose |
|:---|:---|:---|
| `START_VLLM` | `1` | Set `0` to use an external endpoint |
| `VLLM_PORT` | `8100` | Port the built-in vLLM subprocess listens on (loopback only) |
| `LLM_MODEL` | `Qwen/Qwen3.5-4B` | Model to serve if `START_VLLM=1` |
| `LLM_URL` | `http://localhost:8100/v1` | OpenAI-compat endpoint |
| `LLM_SERVED_NAME` | `local` | Model name as served at `LLM_URL` |
| `LLM_API_KEY` | `not-needed` | Bearer for `LLM_URL` |
| `LLM_MAX_TOKENS` | `150` | Cap per response |
| `LLM_TEMPERATURE` | `0.7` | Sampling temperature |

## STT

| Variable | Default | Purpose |
|:---|:---|:---|
| `STT_BACKEND` | `local` | `local` (HF Whisper, in-process) or `openai` (any compat /v1/audio/transcriptions) |
| `WHISPER_MODEL` | `openai/whisper-large-v3-turbo` | HF model id when `STT_BACKEND=local` |
| `STT_URL` | `https://api.openai.com/v1` | Base URL when `STT_BACKEND=openai` |
| `STT_MODEL` | `whisper-1` | Model name when `STT_BACKEND=openai` |
| `STT_API_KEY` | `not-needed` | Bearer when `STT_BACKEND=openai` |

## TTS

| Variable | Default | Purpose |
|:---|:---|:---|
| `TTS_BACKEND` | `fish` | `fish` (sidecar w/ cloning), `kokoro` (in-process), or `openai` (any compat /v1/audio/speech) |
| `FISH_URL` | `http://fish-speech:8092` | Fish sidecar endpoint |
| `FISH_REFERENCE_ID` | *(unset)* | Default saved voice reference |
| `FISH_SAMPLE_RATE` | `44100` | Fish's native output SR |
| `FISH_TIMEOUT` | `180` | Per-call timeout (seconds). Covers cold compile |
| `KOKORO_VOICE` | `af_heart` | Kokoro preset voice |
| `KOKORO_LANG` | `a` | Kokoro language code (`a` = American English, `b` = British, `j` = Japanese, …) |
| `TTS_OPENAI_URL` | `https://api.openai.com/v1` | Base URL when `TTS_BACKEND=openai` |
| `TTS_OPENAI_MODEL` | `tts-1` | Model name when `TTS_BACKEND=openai` |
| `TTS_OPENAI_VOICE` | `alloy` | Voice id when `TTS_BACKEND=openai` |
| `TTS_OPENAI_API_KEY` | `not-needed` | Bearer when `TTS_BACKEND=openai` |
| `TTS_OPENAI_SAMPLE_RATE` | `24000` | Output SR claimed when `TTS_BACKEND=openai` |

## GPU / compose

| Variable | Default | Purpose |
|:---|:---|:---|
| `PROTOVOICE_GPU` | `0` | GPU index for the protovoice container |
| `FISH_GPU` | `1` | GPU index for the fish-speech container |
| `NVIDIA_VISIBLE_DEVICES` | `0` | Inside-container GPU visibility |
| `FISH_REFERENCES_DIR` | `/mnt/data/fish-references` | Host path for saved voice references |

## Tool behaviour

| Variable | Default | Purpose |
|:---|:---|:---|
| `FAKE_RESEARCH_SECS` | `4` | Synthetic fallback sleep for `deep_research` when no ava configured |
| `SLOW_RESEARCH_SECS` | `20` | Synthetic `slow_research` sleep length (async-delivery validation) |

## Memory

| Variable | Default | Purpose |
|:---|:---|:---|
| `MEMORY_MAX_MESSAGES` | `20` | Prune threshold (user + assistant + tool messages) |
| `MEMORY_SUMMARIZE` | `1` | Run LLM summarization on prune overflow. Set `0` to drop silently. |

## Config paths

| Variable | Default | Purpose |
|:---|:---|:---|
| `CONFIG_DIR` | `config` | Where SOUL.md + skills/ + agents.yaml live |

## Delegate authentication

Referenced from `config/delegates.yaml` via `credentialsEnv` (a2a) or `api_key_env` (openai). Common values:

| Variable | Purpose |
|:---|:---|
| `AVA_API_KEY` | Ava orchestrator auth (when `type: a2a`) |
| `AVA_URL` | Override ava's URL without editing YAML (`${AVA_URL:-...}` expansion) |
| `LITELLM_MASTER_KEY` | Bearer for a LiteLLM-fronted openai delegate |
| `OPENAI_API_KEY` | If a delegate points directly at OpenAI |

Add more as you extend the registry.

## A2A inbound (our own server)

| Variable | Default | Purpose |
|:---|:---|:---|
| `A2A_AUTH_TOKEN` | *(unset)* | Shared secret required on inbound `/a2a`. When set, requests must carry `X-API-Key: <token>` or `Authorization: Bearer <token>`. Unset = anonymous inbound. |
| `AGENT_NAME` | `protovoice` | Advertised name in the agent card |
| `AGENT_VERSION` | `0.2.0` | Advertised version in the agent card |
| `A2A_MAX_TURNS` | `10` | Per-contextId history cap for inbound text turns |

## Backchannels

| Variable | Default | Purpose |
|:---|:---|:---|
| `BACKCHANNEL_FIRST_SECS` | `5.0` | Seconds into a user turn before the first backchannel fires |
| `BACKCHANNEL_INTERVAL_SECS` | `6.0` | Interval between subsequent backchannels |

## Audio handling (echo / feedback / turn)

| Variable | Default | Purpose |
|:---|:---|:---|
| `ECHO_GUARD_MS` | `300` | Drop mic audio for this many ms after the bot stops speaking. `0` = disable. |
| `HALF_DUPLEX` | `0` | `1` = mute mic entirely while bot speaks (loses barge-in, kills echo loops). |
| `NOISE_FILTER` | `off` | `rnnoise` enables RNNoise filter on the mic stream. Requires `pip install -e .[rnnoise]`. |
| `SMART_TURN` | `off` | `local` enables LocalSmartTurnAnalyzerV3 — learned end-of-turn detection. Requires `pip install -e .[smart-turn]`. |

See [Audio Handling guide](/guides/audio-handling) for when to use which.
