# Users & API keys

protoVoice identifies each caller by an API key. Keys resolve to **users** in a roster loaded from Infisical (primary) or a local YAML file (fallback). Every `/api/*` route requires one of:

```http
X-API-Key: <key>
Authorization: Bearer <key>
```

Keys are compared in constant time against the sha256 of each roster entry's `api_key`. Clients never see other users' keys — only the resolved user id + display name via [`GET /api/whoami`](../reference/http-api#get-api-whoami).

## Single-user fallback (the default)

When the roster is empty — no Infisical secret and no `config/users.yaml` — every request resolves to a synthetic user:

```json
{ "id": "default", "display_name": "Default", "auth_source": "empty" }
```

No auth is enforced. Keeps local dev + existing tailnet-only deployments working unchanged. The moment the registry has ≥1 real user, auth enforcement kicks in and unknown keys return `401`.

## Roster shape

```yaml
# config/users.yaml
users:
  - id: alice
    api_key: pv_ak_aXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX
    display_name: Alice

  - id: bob
    api_key: pv_ak_bYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYY
    display_name: Bob
```

- `id` — short, URL-safe identifier. Used in session-memory paths (`/tmp/protovoice_sessions/{id}/…`) and Langfuse attribution.
- `api_key` — the secret the client sends. Generate with `python3 -c "import secrets; print('pv_ak_' + secrets.token_urlsafe(32))"`.
- `display_name` — optional; defaults to the id title-cased.

Copy `config/users.example.yaml` as a starting point.

## Infisical roster (recommended for the fleet)

Store a single Infisical secret named `USERS_YAML` whose value is the full YAML content above. protoVoice fetches and parses it at boot (and on `POST /api/users/reload`).

Env vars:

| Variable | Purpose |
|:---|:---|
| `INFISICAL_API_URL` | Base URL (default `https://app.infisical.com`; point at self-hosted pve01 instance) |
| `INFISICAL_CLIENT_ID` + `INFISICAL_CLIENT_SECRET` | Machine-identity credentials (universal-auth) |
| `INFISICAL_PROJECT_ID` | Workspace/project id |
| `INFISICAL_ENVIRONMENT` | Env slug, default `prod` |
| `INFISICAL_SECRET_PATH` | Folder path, default `/protovoice` |
| `INFISICAL_USERS_SECRET_NAME` | Secret name, default `USERS_YAML` |

When all three credential vars are set, Infisical becomes the active source; `config/users.yaml` is ignored. `GET /api/whoami.auth_source` reports `"infisical"` in that case.

**Rationale for a single YAML blob** rather than one secret per user: atomic updates, exact parity with the on-disk file, and `POST /api/users/reload` re-parses in one shot.

## What's scoped per-user

| Concern | Behavior |
|:---|:---|
| Skill selection (`/api/skills`) | Each user has their own active skill — Alice on `chef` doesn't affect Bob's dropdown. |
| Verbosity (`/api/verbosity`) | Per-user. Alice's `silent` is invisible to Bob's `chatty`. |
| Session memory | Stored at `{SESSION_STORE_DIR}/{user_id}/{skill_slug}.txt` — no cross-user sharing by design. |
| Stashed deliveries | Same path pattern. Replays only to the user who was offline when the push arrived. |
| Active DeliveryController / Langfuse tracer | Each concurrent user gets their own; no singleton cross-contamination. |
| Langfuse spans | Stamped with `user_id` + `session_id` so you can filter traces by user. |

## What's still process-global

- `/config/delegates.yaml` — same registry for everyone; each skill filters via `skill.delegates: [...]`.
- `/config/skills/*.yaml` — same catalog for everyone; users pick one at a time.
- `LLM_URL` / `TTS_BACKEND` / `STT_BACKEND` env defaults — per-skill overrides via `skill.llm` take priority.

## A2A inbound

Inbound A2A traffic (`POST /a2a`) is gated separately by `A2A_AUTH_TOKEN`. Stashed deliveries + skill attribution for the inbound path resolve to the user named by `A2A_USER_ID` (default `default`). True per-caller A2A auth — each fleet agent holding its own key against the same users roster — is future work.

## Reloading the roster

```bash
curl -X POST https://protovoice/api/users/reload \
  -H "X-API-Key: <your-key>"
# → {"ok": true, "users": ["alice", "bob"], "source": "infisical"}
```

Safe mid-session. Active clients keep their authenticated state until they reconnect; new connections see the refreshed registry.

## Implementation

- `auth/users.py` — UserRegistry, sha256 lookup, FastAPI `require_user` dependency
- `auth/infisical.py` — universal-auth login + single-secret fetch
- `auth/context.py` — `current_user_id` / `current_session_id` ContextVars
- `agent/user_state.py` — `UserState` dataclass + registry, lazy FillerGenerator per user

Each of the pieces above is importable in isolation; auth can be swapped for a different source (OAuth, header-stamping reverse-proxy, etc.) by replacing the UserRegistry's source.
