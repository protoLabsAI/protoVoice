# Two-Model Split

protoVoice runs the in-pipeline LLM as a fast **router** and offloads heavy reasoning to a separate **thinker**. The router stays in the critical path of every turn; the thinker only fires when the router calls `deep_research`.

There are **two ways to wire the thinker** — pick whichever fits your fleet.

## The split

- **Router** — the local OpenAI-compatible LLM at `LLM_URL` (typically a small fast model: Qwen3.5-4B / 9B, GPT-4o-mini, Llama 3 8B). Handles every user turn: chitchat, tool selection, the inline preamble before tool calls, and speaking the final answer.
- **Thinker** — a more capable model OR another agent. Invoked only when the router decides it needs help (calls the `deep_research` tool).

This pattern matters because:

- **TTFA**. The router is in the critical path of every turn; latency is felt directly. Tiny models do 150-300 tok/s locally; big models do 30-50. Don't put a big model in the router slot.
- **Cost**. Routing chitchat to a tiny local model burns zero API tokens. Only research questions hit the thinker.
- **Latency isolation**. The thinker can take 2-30 s without blocking the conversation, because it runs behind a tool call that the user hears acknowledged immediately via the [inline preamble](/explanation/natural-fillers).

## Two thinker mechanisms

### Option 1 — direct LLM endpoint (`THINKER_URL`)

Point at any OpenAI-compatible chat-completions endpoint:

```bash
THINKER_URL=http://gateway:4000/v1
THINKER_MODEL=claude-opus-4-6
THINKER_API_KEY=$LITELLM_MASTER_KEY
```

`deep_research(query="...")` POSTs the query as a one-shot user message to that endpoint and speaks the response. No agent context, no tools — just "raw model with a research-flavored system prompt."

**Best when**: you have a LiteLLM gateway / cloud API and just want a bigger model behind one tool. Smallest moving part.

**Tunables**:

```bash
THINKER_MAX_TOKENS=400
THINKER_TEMPERATURE=0.4
THINKER_SYSTEM_PROMPT="You are a research assistant. Answer thoroughly but concisely (2-4 sentences). Plain text only — no markdown."
```

### Option 2 — A2A dispatch to another agent (ava)

Point at an A2A-speaking agent in `config/agents.yaml`:

```yaml
agents:
  - name: ava
    url: ${AVA_URL:-http://ava:3008/a2a}
    auth:
      scheme: apiKey
      credentialsEnv: AVA_API_KEY
```

`deep_research(query="...")` does an A2A `message/send` to ava. Ava is a full agent with her own tools, memory, subagents, and dispatch authority. The answer comes back as an artifact and the router speaks it.

**Best when**: you have an orchestrator agent in the protoLabs fleet that's already wired up with the right knowledge / sub-agents / context. Strongest answers; one extra hop.

### Resolution priority

When the user calls `deep_research`:

1. If `THINKER_URL` and `THINKER_MODEL` are set, use the direct endpoint.
2. Else if `ava` is in the registry, dispatch via A2A.
3. Else, return a synthetic placeholder (so dev without a fleet running doesn't see errors).

You can configure both — the thinker takes priority. Use this if you want a fast cloud model for most lookups and ava as a fallback.

## Why not just one model

One good model at every turn is simpler and produces marginally better individual answers. It's also 3-5× more expensive and 2-10× slower at steady state. For a voice agent where the user is listening live, the latency tax is unacceptable for routine turns.

If gateway latency drops below ~300 ms TTFB and local inference becomes free, the split collapses. We're not there yet.

## Health check

```bash
curl http://localhost:7867/healthz | jq .thinker
# {"configured": true, "model": "claude-opus-4-6"}
# or
# {"configured": false, "model": null}
```

## Code references

- `agent/tools.py::_deep_research_handler` — the priority-ordered routing
- `app.py::_thinker_or_none` + `_thinker_call` — the direct-endpoint client
- `a2a/registry.py::AgentRegistry` — the A2A path
