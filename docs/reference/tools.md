# Tools

Every tool registered on the voice LLM. Sync tools block the LLM loop until they return; async tools return to the LLM immediately and deliver their result later via the [DeliveryController](/guides/delivery-policies).

Source: [`agent/tools.py`](https://github.com/protoLabsAI/protoVoice/blob/main/agent/tools.py).

## Sync tools

### `calculator`

Safe AST-based arithmetic evaluation. Supports `+ - * / // % **` and unary minus. No attribute access, no function calls.

```json
{ "expression": "15 * 1.2 + 3" }
```

Returns a natural-language sentence the LLM can speak verbatim: `"15 * 1.2 + 3 equals 21."`

### `get_datetime`

Returns the current time in the container's configured timezone (default `America/New_York`, override via `TZ`).

```json
{}
```

### `web_search`

DuckDuckGo via the `ddgs` package. Returns up to 5 snippets concatenated into a single string, capped at 2000 characters so the LLM context stays sane.

```json
{ "query": "history of hot dogs" }
```

### `deep_research`

Routes through the orchestrator (ava) via A2A when an ava entry is present in `config/agents.yaml` AND `AVA_API_KEY` is set. Otherwise falls back to a synthetic placeholder — useful for developing without the fleet running.

```json
{ "query": "what's the current status of the ingestion pipeline?" }
```

### `a2a_dispatch`

Send a free-form message to any agent in the registry. Use when the LLM knows the target agent specifically; use `deep_research` when it doesn't care which agent handles the research.

```json
{ "agent": "ava", "message": "give me a sitrep on the dashboard project" }
```

The registry's schema follows [protoWorkstacean's agents.yaml](https://github.com/protoLabsAI/protoWorkstacean/blob/main/workspace/agents.yaml.example). Config-loaded on startup via `AGENTS_YAML` (default `config/agents.yaml`).

## Async tools

### `slow_research`

Long-running investigation. LLM acknowledges immediately ("Sure — I'll look into that..."); a background asyncio task runs for `SLOW_RESEARCH_SECS` seconds, then pushes the result via the DeliveryController with `NEXT_SILENCE` policy.

```json
{ "query": "history of hot dogs" }
```

Keywords for `when_asked` matching are derived from words in the query longer than 3 chars.

## Registry shape

```yaml
# config/agents.yaml
agents:
  - name: ava
    url: ${AVA_URL:-http://ava-host:3008/a2a}   # POSIX env expansion supported
    auth:
      scheme: apiKey
      credentialsEnv: AVA_API_KEY
    skills: [...]
```

Supported auth schemes: `apiKey` (`X-API-Key` header) and `bearer` (`Authorization: Bearer`). Missing env vars log a warning; entries with missing creds are still loaded but unauthenticated.

## Tool selection prompt

The LLM's system prompt instructs it to:

- Answer directly without a tool when it can
- Prefer `deep_research` for quick fact lookups
- Prefer `slow_research` when the user doesn't need an immediate answer

Ultimately the small router LLM (current or future) is the tie-breaker. If it over-dispatches, tighten the prompt.
