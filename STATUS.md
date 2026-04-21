# Tonight's Status — Pickup for Tomorrow

Updated after v0.6.0 ship. Branch: `main`. Working tree: clean.

## TL;DR

**v0.5.0 + v0.6.0 both shipped tonight.** Biggest remaining track for tomorrow is the **React frontend migration** — vanilla `static/index.html` will be replaced. The server-side Langfuse tracing + RTVI observer are already in place waiting for client consumption.

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

### What's NOT in v0.6 (explicitly deferred)
| ID | Item | Why deferred | Target |
|---|---|---|---|
| E2/E3/E4 | Client RTVI consumption + orb rewire + state chip | React migration tomorrow | v0.7 (React track) |
| C11 | RTVI observer adoption in client | Same as above | v0.7 |
| K25 | Prompt registry migration (Langfuse prompts) | Not load-bearing for v0.6 value | v0.7+ |
| F8 | JWT + JWKS on `/a2a/push` | Shared-secret fine for tailnet | when public-exposed |
| H15 | Per-skill TTS backend | Stretch, not critical | v0.7+ |
| G9-G11 | Multi-tenant session keying | Single-user homelab still | v0.7+ |
| I16-I18 | Prometheus / HF Spaces / E2E tests | Observability + deploy polish | v0.7+ |
| J19 | Confidence-aware prosody | Nice-to-have, needs router confidence surface | v0.7+ |

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

## Tomorrow's main track — React frontend

The big piece. Scope notes:

### Motivation
- vanilla `static/index.html` has drifted into significant complexity (500+ lines of JS, orb wiring, drawer UI, preset persistence, settings controls)
- RTVI server-side is in place — we want to consume it client-side so orb state comes from authoritative events, not audio-RMS heuristics
- UI wants room to grow (state chip, settings panel, transcript view, voice-clone flow, developer-view HUD)

### Recommended approach
1. **New top-level dir `web/`** alongside `static/`. Vite + React + TypeScript. Keep `static/` mountable at `/static` for a deprecation period.
2. **RTVI consumer**: choose one of
   - `@pipecat-ai/client-web` — full client library (signaling, audio, RTVI events). Wholesale, but you get RTVI out of the box.
   - Minimal custom consumer — ~50 lines in a React hook that reads the data channel directly. Less dep, more code.

   Given we want the React frontend to potentially become more sophisticated, `@pipecat-ai/client-web` is probably the right call — but validate its bundle size + audio handling match what we want before committing.
3. **Port Three.js orb to a React component.** The shader code + audio-reactive logic in `static/viz.js` is already well-separated; wrap it in a `useRef`-based component that mounts the canvas. Adapt `attachStream()` hooks to work off RTVI events *and* raw audio tracks (keep energy reactivity on top of authoritative state).
4. **Build the server wire-up**: `app.py` mounts `/` → React SPA, `/static/` → legacy vanilla still available for a release cycle as fallback. Signalling stays at `/api/offer`.
5. **Drop C11 task** when E2/E3/E4 land in React — they were always meant to go together.

### Things to preserve from vanilla
- Orb visualizer (all the audio-reactive + mouse interaction + state transitions)
- Presets (5 palettes, custom saved presets, localStorage persistence)
- Drawer UI (hamburger, voice/orb tabs, Skill + Verbosity + Visual Preset controls, voice clone form)
- Double-click-orb-to-start
- Status pill (connected — speak, fade after 3s)

### Things to add in React (that were deferred)
- **E3 viz rewire** — orb state (idle/listening/thinking/speaking) driven by RTVI events, not envelope thresholds.
- **E4 state chip** — `idle`/`listening`/`thinking`/`speaking` text label visible alongside the orb.
- **Settings panel** expanded with the K20+ trace debug-view (trace ID display, link to Langfuse UI).

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
