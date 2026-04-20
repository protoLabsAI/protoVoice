# A2A Integration

protoVoice speaks [A2A](https://a2a-protocol.org) (JSON-RPC 2.0 over HTTP) for both inbound and outbound traffic. Other agents in the protoLabs fleet can call us; we can dispatch to them through the `deep_research` and `a2a_dispatch` tools.

## Inbound — another agent calls us

When another agent (e.g. ava) sends us a `message/send` request, we run a one-shot text turn through the active skill's LLM + system prompt and return the result as an artifact.

No voice, no WebRTC. This path is for text-only coordination.

### Register protoVoice in the caller's registry

Assuming the caller uses the same `workspace/agents.yaml` schema (protoWorkstacean / protoAgent fleet):

```yaml
agents:
  - name: protovoice
    url: ${PROTOVOICE_URL:-http://protovoice:7866/a2a}
    auth:
      scheme: apiKey
      credentialsEnv: PROTOVOICE_API_KEY
    skills:
      - name: chat
        description: Conversational assistant with web search and calculator
```

### Auth

Set `A2A_AUTH_TOKEN=<shared secret>` on the protoVoice side. Inbound requests without `X-API-Key: <token>` (or `Authorization: Bearer <token>`) are rejected with `401`.

Omit `A2A_AUTH_TOKEN` for unauthenticated operation (dev / homelab). The agent card's `securitySchemes` adjusts based on whether the token is set.

### Agent card

```bash
curl http://localhost:7867/.well-known/agent-card.json
```

Returns our advertised name, skills, capabilities, auth schemes. Callers typically refresh this on boot.

### Wire format

`POST /a2a`:

```json
{
  "jsonrpc": "2.0",
  "id": "<caller-chosen>",
  "method": "message/send",
  "params": {
    "contextId": "<uuid>",
    "message": {
      "role": "user",
      "parts": [{"kind": "text", "text": "<the user's message>"}]
    }
  }
}
```

Response:

```json
{
  "jsonrpc": "2.0",
  "id": "<caller-chosen>",
  "result": {
    "id": "<artifact-id>",
    "contextId": "<uuid>",
    "status": {"state": "completed"},
    "artifacts": [{
      "artifactId": "<uuid>",
      "parts": [{"kind": "text", "text": "<assistant reply>"}]
    }]
  }
}
```

### Multi-turn

Reuse the same `contextId` across turns. protoVoice keeps a bounded buffer (`A2A_MAX_TURNS`, default 10) per context so the conversation stays coherent without growing unbounded.

### Known limits (M6)

- **No streaming** (`message/stream`). Synchronous only. Plan: add streaming once the inbound tool-loop lands.
- **No tool calls in the inbound path.** The text agent is a one-shot chat turn; it can't use `web_search` or `a2a_dispatch`. The voice side uses them freely. Add the ReAct loop if you need tools via A2A.
- **No task lifecycle** (`tasks/get`, `tasks/cancel`). Only `message/send`.

## Outbound — we call another agent

See [Tools → a2a_dispatch](/reference/tools#a2a_dispatch) and the [Agent Registry reference](/reference/agent-registry).

Voice tools `deep_research` and `a2a_dispatch` pull from `config/agents.yaml`. When `AVA_API_KEY` and `AVA_URL` are set, `deep_research` delegates to ava automatically; otherwise it falls back to a synthetic placeholder so the session keeps flowing.

## The callback endpoint

`POST /a2a/callback` — receives push-notification results from agents we dispatched to. If an active voice session is running, the result is spoken via the `DeliveryController` with `next_silence` policy:

```bash
curl -X POST http://localhost:7867/a2a/callback \
  -H "Content-Type: application/json" \
  -d '{"from":"ava","text":"status report complete; all green"}'
```

When no session is active, the callback returns `{"ok": true, "delivered": false, "reason": "no active session"}` — the caller can then decide to retry or drop the result.

::: tip
Current outbound dispatch is **synchronous** (our client waits for the response). Pushing through the callback path requires the caller agent to support push notifications — see `pushNotificationConfig` in the A2A spec. Outbound-with-callback wiring is planned for M7+.
:::

## Testing locally

```bash
# agent card
curl http://localhost:7867/.well-known/agent-card.json | jq .

# send a message (no auth)
curl -X POST http://localhost:7867/a2a \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc":"2.0","id":"t1","method":"message/send",
    "params":{"contextId":"ctx-demo","message":{"role":"user","parts":[{"kind":"text","text":"what time is it?"}]}}
  }' | jq .

# with auth
curl -H "X-API-Key: $A2A_AUTH_TOKEN" -X POST http://localhost:7867/a2a ...
```
