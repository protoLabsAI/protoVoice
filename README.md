# protoVoice

Full-duplex voice agent. Speak, get spoken replies fast; the agent speaks *before* it's done thinking, can push async results back mid-conversation, and can delegate to other agents or bigger LLMs when it needs to.

```
browser mic → WebRTC → STT → router LLM → TTS → speaker
                              │
                              └─ calls a tool → speaks a preamble inline →
                                 (sync: web_search, calculator, datetime)
                                 (delegate: ava, opus, any OpenAI-compat)
                                 (async: slow_research — you keep talking;
                                  agent drops the answer in at next silence)
```

Built on [Pipecat](https://docs.pipecat.ai) 1.0.

## Quick start

```bash
git clone https://github.com/protoLabsAI/protoVoice.git
cd protoVoice
cp .env.example .env      # edit for any secrets (AVA_API_KEY, LITELLM_MASTER_KEY, etc.)
docker compose up -d
```

UI at `http://localhost:7866`. Browsers require HTTPS for mic access on non-localhost — use `tailscale serve 7866` (tailnet-only HTTPS) or a reverse proxy with TLS. Headphones recommended — see [audio handling](https://protolabsai.github.io/protoVoice/guides/audio-handling/) for speaker-echo mitigations.

## What you get

- **Pipecat pipeline** — WebRTC, VAD, streaming STT → LLM → TTS, barge-in
- **Inline preamble** — the router LLM speaks "hmm, let me check" *before* calling a tool, in the same response stream (no second LLM call, no race conditions). Details in [natural-fillers](https://protolabsai.github.io/protoVoice/explanation/natural-fillers/).
- **Delegates** — a single `delegate_to(target, query)` tool covering both A2A agents AND OpenAI-compatible LLM endpoints. Configured in `config/delegates.yaml`; the LLM picks targets by the descriptions you write.
- **Async tools** — `slow_research`-style work can return later; pipecat injects the result as a developer message when ready, and the LLM speaks it at the next pipeline opportunity.
- **Voice cloning in-browser** — upload a 10-30 s clip, auto-transcribed by Whisper, saved on Fish Audio, registered as a new skill. Instant new voice, no restart.
- **Personas & skills** — `config/SOUL.md` + `config/skills/*.yaml` for swappable personas with per-skill TTS voice, LLM tuning, and tool restrictions.
- **A2A both ways** — outbound via `delegate_to`; inbound via `/a2a` JSON-RPC so other fleet agents can call *us* (text-only).
- **Sliding-window memory** — with background LLM summarization when context grows.
- **Pluggable backends** — STT and TTS both swappable via env (`STT_BACKEND=local|openai`, `TTS_BACKEND=fish|kokoro|openai`). Run fully API-backed via LocalAI, LiteLLM, OpenAI — no GPU on the protovoice container needed. See the [use-localai guide](https://protolabsai.github.io/protoVoice/guides/use-localai/).

## Stack defaults

| Layer | Default |
|:---|:---|
| STT | HF Whisper large-v3-turbo (GPU, in-process) |
| Router LLM | local vLLM (Qwen3.5-4B — any OpenAI-compat works) |
| TTS | Fish Audio S2-Pro sidecar (`--half --compile`; ~400-800 ms TTFA); Kokoro 82M (~50 ms) and OpenAI-compat endpoints also selectable |
| Transport | Pipecat `SmallWebRTCTransport` |
| Delegates | ava (at `https://ava.proto-labs.ai/v1`) — add more in `config/delegates.yaml` |

## Configuration

Everything's env-driven. `cp .env.example .env` and edit — all 44+ env vars are documented there and in [Environment Variables](https://protolabsai.github.io/protoVoice/reference/environment-variables/). Defaults work for a single-GPU homelab install.

For production boxes, inject secrets via Infisical / Vault / k8s Secret + envFrom — the app reads `os.environ` and doesn't care where values came from.

## Docs

Full site: **https://protolabsai.github.io/protoVoice/** — Diátaxis-organized (tutorials / guides / reference / explanation).

Common starting points:

- [First Voice Session](https://protolabsai.github.io/protoVoice/tutorials/first-voice-session/) — clone, configure, talk
- [Build a Tool](https://protolabsai.github.io/protoVoice/guides/build-tools/) — sync vs async, the `result_callback` gotcha
- [Delegates](https://protolabsai.github.io/protoVoice/reference/delegates/) — add an A2A agent or OpenAI endpoint
- [Audio Handling](https://protolabsai.github.io/protoVoice/guides/audio-handling/) — echo guard, half-duplex, noise filter, smart-turn
- [Two-Model Split](https://protolabsai.github.io/protoVoice/explanation/two-model-split/) — router LLM vs. delegated thinker pattern

## Release pipeline

- Push to `main` → GHCR `:latest` + `sha-<short>` images via `.github/workflows/docker-publish.yml`
- `vX.Y.Z` tag → stable semver images + GitHub release via `.github/workflows/release.yml`
- Manual `workflow_dispatch` on `prepare-release.yml` → bumps version, opens + auto-merges the release PR, tags

## License

MIT
