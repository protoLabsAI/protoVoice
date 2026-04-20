# Environment Variables

All values have sensible defaults. Set via shell, `docker compose` environment, or a `.env` file alongside `docker-compose.yml`.

## Server

| Variable | Default | Purpose |
|:---|:---|:---|
| `PORT` | `7866` | HTTP port the FastAPI/Pipecat server listens on |
| `HF_HOME` | `/models` | HuggingFace cache dir (inside the container) |
| `MODEL_DIR` | `/models` | Alias for `HF_HOME` — set one, both resolve |
| `SYSTEM_PROMPT` | *(built-in)* | Overrides the default voice-assistant system prompt |
| `VERBOSITY` | `brief` | Default filler verbosity: `silent` / `brief` / `narrated` / `chatty` |
| `TZ` | `America/New_York` | Timezone for the `get_datetime` tool |
| `AGENTS_YAML` | `config/agents.yaml` | Path to the outbound A2A registry |

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
| `WHISPER_MODEL` | `openai/whisper-large-v3-turbo` | HF model id for the STT pipeline |

## TTS

| Variable | Default | Purpose |
|:---|:---|:---|
| `TTS_BACKEND` | `fish` | `fish` or `kokoro` |
| `FISH_URL` | `http://fish-speech:8092` | Fish sidecar endpoint |
| `FISH_REFERENCE_ID` | *(unset)* | Default saved voice reference |
| `FISH_SAMPLE_RATE` | `44100` | Fish's native output SR |
| `FISH_TIMEOUT` | `180` | Per-call timeout (seconds). Covers cold compile |
| `KOKORO_VOICE` | `af_heart` | Kokoro preset voice |
| `KOKORO_LANG` | `a` | Kokoro language code (`a` = American English, `b` = British, `j` = Japanese, …) |

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

## A2A authentication

Referenced from `config/agents.yaml` via `credentialsEnv`. Common values:

| Variable | Purpose |
|:---|:---|
| `AVA_API_KEY` | Ava orchestrator auth |
| `AVA_URL` | Override ava's URL without editing YAML (used via `${AVA_URL:-...}` expansion) |
| `QUINN_API_KEY` | (If quinn is added to the registry) |

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
