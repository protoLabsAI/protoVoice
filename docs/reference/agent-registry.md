# Agent Registry

The voice agent dispatches to other protoLabs agents via A2A (JSON-RPC over HTTP). The registry is a YAML file loaded once at startup.

## File location

Default `config/agents.yaml`. Override with `AGENTS_YAML=<path>`.

## Schema

```yaml
agents:
  - name: ava
    url: ${AVA_URL:-http://ava-host:3008/a2a}
    auth:
      scheme: apiKey                 # or "bearer"
      credentialsEnv: AVA_API_KEY
    headers:
      x-protolabs-extension: opt-in  # static request headers, optional
    skills:
      - name: chat
        description: Free-form conversation
      # skills are informational — the LLM reads them via the `a2a_dispatch`
      # description but doesn't need strict typing
```

Matches [protoWorkstacean's `workspace/agents.yaml`](https://github.com/protoLabsAI/protoWorkstacean/blob/main/workspace/agents.yaml.example). Reusing the same file across the fleet is fine.

## POSIX env expansion

Values support `${VAR}` and `${VAR:-default}` substitution. The registry evaluates them at load time:

```yaml
url: ${AVA_URL:-http://ava-host:3008/a2a}
```

If `AVA_URL` is set, its value replaces the placeholder. Otherwise the default is used.

## Auth

**`apiKey`** — the credential is sent as `X-API-Key`.

**`bearer`** — sent as `Authorization: Bearer <cred>`.

**Missing credentials** — logged as a WARNING at load time. The entry is still usable for unauthenticated endpoints. Hitting an endpoint that required auth returns `HTTP 401`; the tool wrapper surfaces that as a spoken error.

## Outbound wire format

`POST {agent.url}`:

```json
{
  "jsonrpc": "2.0",
  "id": "<uuid>",
  "method": "message/send",
  "params": {
    "contextId": "<uuid>",
    "message": {
      "role": "user",
      "parts": [{"kind": "text", "text": "<user query>"}]
    }
  }
}
```

Response format:

```json
{
  "jsonrpc": "2.0",
  "id": "<uuid>",
  "result": {
    "id": "...",
    "contextId": "...",
    "status": {"state": "completed"},
    "artifacts": [
      {"artifactId": "...", "parts": [{"kind": "text", "text": "<reply>"}]}
    ]
  }
}
```

We extract the first text part from the first artifact. Malformed responses raise `A2ADispatchError`, surfaced as a spoken error.

## Known limits (M4)

- **Synchronous only.** One HTTP request, block until response. For delegations that take 30+ seconds, this blocks the tool slot. Either use `slow_research` to wrap it or wait for M6 which adds push-notification callbacks.
- **No retry / backoff.** First failure is the spoken error.
- **No concurrency guard.** Multiple `a2a_dispatch` calls to the same agent run in parallel; that's the caller's problem.

## Verifying from the voice agent

```bash
curl http://localhost:7867/healthz
# {"status":"ok","tts_backend":"fish","verbosity":"brief","known_agents":["ava"]}
```
