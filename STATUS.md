# Tonight's Status — Pickup for Tomorrow

Updated after v0.6.2 + React frontend migration. Branch: `main`.

## TL;DR

**React frontend migration shipped.** `web/` (bun + Vite 6 + React 19 + shadcn/ui + Tailwind 4 + PWA) replaces vanilla `static/index.html` at `/`. Legacy shell stays mounted at `/static/legacy/` behind `FRONTEND=vanilla` for instant rollback. RTVI protocol fully consumed — orb state is driven by authoritative events, not audio-RMS heuristics. Plugin architecture in place with UI-slot registry for future extensibility.

## Where we are

### Deployed
- Running at `https://protolabs.taild25506.ts.net/` (tailnet-only, ava Blackwell node, Fish S2-Pro TTS, Qwen 3.6-35B via host vLLM on `:8000`).
- Docker image: `ghcr.io/protolabsai/protovoice:v0.6.0` and `:latest`.
- Docs: `https://protolabsai.github.io/protoVoice/` — latest pages include Tracing + Tracing Contract.

### What v0.6.0 added (highlights)
- **Langfuse tracing**: TurnTracer observer, session/trace skeleton, LLM + tool + manual spans, `langfuse.openai` drop-in for filler generations, cross-fleet header propagation.
- **RTVI (server-side only)**: `RTVIProcessor` + `RTVIObserver` in the pipeline. Events stream over the WebRTC data channel — nothing consuming them yet.
- **Inbound A2A streaming (F5)**: `/a2a` handles both `message/send` and `message/stream` (SSE).
- **Inbound ReAct loop (F6)**: text agent now has access to `calculator`, `get_datetime`, `web_search`, `delegate_to`; bounded 3 iterations.
- **Delivery polish**: NEXT_SILENCE fallback timer (muted-mic), WHEN_ASKED TTL.
- **Client fix**: outbound A2A messages now carry `messageId` per spec.

### What landed with the React migration (v0.7 track)
- **E3** — orb state driven by RTVI events (UserStartedSpeaking / BotLlmStarted / BotStartedSpeaking / BotStoppedSpeaking); audio-envelope derivation remains as fallback
- **E4** — `idle / listening / thinking / speaking` state chip (shadcn Badge, top-right)
- **C11** — RTVI observer fully consumed via `@pipecat-ai/client-react` + `@pipecat-ai/small-webrtc-transport`
- Plugin architecture: `plugins/{orb, orb-settings, status-chip, status-pill, voice-panel}` with a UI-slot registry (`stage`, `overlay-top`, `overlay-bottom`, `drawer-voice`, `drawer-orb`)
- PWA — installable, app shell precached, `/api/*` + `/.well-known/*` excluded from service-worker interception

### What's still deferred
| ID | Item | Why deferred | Target |
|---|---|---|---|
| K25 | Prompt registry migration (Langfuse prompts) | Not load-bearing | v0.7+ |
| F8 | JWT + JWKS on `/a2a/push` | Shared-secret fine for tailnet | when public-exposed |
| H15 | Per-skill TTS backend | Stretch, not critical | v0.7+ |
| G9-G11 | Multi-tenant session keying | Single-user homelab still | v0.7+ |
| I16-I18 | Prometheus / HF Spaces / E2E tests | Observability + deploy polish | v0.7+ |
| J19 | Confidence-aware prosody | Nice-to-have, needs router confidence surface | v0.7+ |
| — | Transcript panel plugin | Cheap add — RTVI transcripts already flowing | v0.7+ |
| — | Trace-chip plugin (Langfuse trace id link) | Lands when Langfuse env vars are wired | v0.7+ |
| — | Per-user server-side preset storage | localStorage fine single-user; server-side blocked on G9 | v0.7+ |

## Waiting on external

### Workstacean
1. **F7 ava delegate flip** — code-complete on our side (messageId ✓, streaming ✓, auth ✓, trace headers ✓, spec-tolerant fallback to `status.message` when `artifacts` is empty ✓). Held at `type: openai` pending [protoWorkstacean#471](https://github.com/protoLabsAI/protoWorkstacean/issues/471): (a) `message/send` routes to protoBot instead of ava, (b) `message/stream` intermittently returns an upstream "Cannot POST /" 404 as the reply text. When both land, edit `config/delegates.yaml` and swap the commented a2a block for the openai one — nothing else needed.
2. **Tracing contract implementation** — [`docs/reference/tracing-contract.md`](https://protolabsai.github.io/protoVoice/reference/tracing-contract/) defines `Langfuse-Trace-Id` / `Langfuse-Session-Id` / `Langfuse-Parent-Observation-Id`. They implement "continue, don't create" in their `/a2a` handler. Until they do, we attach the headers but traces don't stitch across the fleet.

### Langfuse config
Not wired yet (code fails open when `LANGFUSE_*` env is unset). When the Langfuse on the ava node is ready for us:
```
LANGFUSE_HOST=http://ava:3000        # or the actual URL
LANGFUSE_PUBLIC_KEY=pk-lf-...
LANGFUSE_SECRET_KEY=sk-lf-...
```
In `.env` (local) or the deployment env. No code changes needed.

## Web frontend reference (where things live)

- `web/` — bun + Vite 6 + React 19 + shadcn/ui + Tailwind 4 + vite-plugin-pwa.
- `web/src/voice/` — PipecatClient wiring, derived voice-state store (useSyncExternalStore), hooks (`useVoiceState`, `useVoiceStateSelector`, `useVoiceSession`, `useBotTurnEvents`, `useUserTurnEvents`).
- `web/src/plugins/` — one directory per plugin. Each registers at module import via `registerPlugin({ id, slots })`. Add a plugin: drop a dir, export a component, side-effect-import from `App.tsx`.
- `web/src/lib/api.ts` — typed fetches for `/api/{skills,verbosity,voice/clone}`.
- `web/src/components/Drawer.tsx` — shadcn Sheet + Tabs hosting the voice/orb drawer panels.
- `app.py` — `FRONTEND=auto|react|vanilla` env flag picks the shell. `auto` uses `web/dist/` when present, legacy `static/` otherwise.
- `Dockerfile` — stage 1 builds `web/` via `oven/bun:1`; stage 2 copies `web/dist/` into the CUDA runtime image.
- Dev: `cd web && bun run dev` + `sudo tailscale serve --bg --https=8443 http://127.0.0.1:5173` for tailnet-accessible HMR with full HTTPS (mic permission).

## Tomorrow's main track — v0.7 polish + next features

Easy vertical slices to land on top of the React frontend:
- **Transcript panel plugin** — new `plugins/transcript/` that listens to `UserTranscript` + `BotTranscript` and renders in `overlay-bottom` or a new drawer tab. Cheap add — events already flowing.
- **Trace-chip plugin** — show active Langfuse trace id (needs `LANGFUSE_*` env set on the deployed box). Open-in-Langfuse link.
- **Code-split the bundle** — current 359 KB gz is under budget but `three` is ~130 KB gz of that; dynamic `import()` of the orb plugin shaves the initial shell.
- **Remove legacy `static/`** once React has a few weeks in prod without rollback. Drop `FRONTEND=vanilla` handling at that point.

## Quick-start commands

```bash
# Where we left off
cd ~/dev/protoVoice
git log --oneline -5
git status

# Current container
docker compose ps
docker compose logs --tail=20 protovoice

# Health + smoke
curl -sS https://protolabs.taild25506.ts.net/healthz | jq .
curl -sS -X POST https://protolabs.taild25506.ts.net/a2a \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":"t","method":"message/send",
       "params":{"contextId":"c","message":{"messageId":"m","role":"user",
       "parts":[{"kind":"text","text":"what time is it?"}]}}}'

# Release info
gh release view v0.6.0
```

## Context + gotchas

- `_ACTIVE_TRACER` module-level registry pattern lets `a2a/client.py`, `agent/delegates.py`, etc. reach the live Langfuse trace without importing `app.py` (circular-import-safe).
- Pipecat's `LLMContextSummarizer` is plumbed inside the assistant aggregator — no separate pipeline processor. Env knobs: `MEMORY_MAX_CONTEXT_TOKENS`, `MEMORY_MAX_MESSAGES`, `MEMORY_TARGET_CONTEXT_TOKENS`.
- `ProsodyTextFilter` plugs into pipecat's TTS `text_filters=` kwarg (Kokoro + OpenAI); Fish passes tags through.
- DeliveryController's watchdog is lazy: starts on first `deliver()`, exits when queue drains, re-arms on next enqueue.
- `agent/tracing.py::_NullSpan` is the fail-open stand-in — every span-using call site works unmodified when Langfuse is off.
- `cancel_on_interruption=True` is the default for sync tools; only `slow_research` is `False` (true async path).
- In `agent/session_store.py`: `{SESSION_STORE_DIR}/{skill}.txt` holds the summary; `{skill}.pending.json` holds orphan deliveries.

## Known tripwires (things NOT to change lightly)

- **Don't touch getUserMedia constraints.** We tried disabling AGC/NS/EC — broke the server VAD's ability to detect speech. Browser defaults stay.
- **Don't reintroduce a hand-rolled memory pruner.** Pipecat's `LLMContextSummarizer` is the right primitive — our `memory/window.py` is deleted for a reason.
- **Don't remove `messageId`** from outbound A2A calls. Spec-required; workstacean enforces.
- **Don't forget `uTime` + `orb.rotation` wrap** in the visualizer — GLSL float32 precision degrades after ~10 minutes; wrap at 2π·N keeps sin/cos clean.
- **Don't touch `append_to_context=False`** on filler / backchannel / delivery `TTSSpeakFrame`s — without it the LLM riffs on its own fillers.

## One-line rollback, if needed

```bash
git checkout v0.5.0     # previous known-good tag
docker compose up -d --no-deps --force-recreate protovoice  # restart with v0.5.0 image if needed
```

---

Tomorrow's first action: pick framework + RTVI client approach, scaffold `web/` dir, wire signalling against `/api/offer`.
