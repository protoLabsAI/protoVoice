# Memory

Per-session memory is a **sliding window with background summarization**. It prevents the LLM context from growing unboundedly while preserving the gist of older turns.

## How it works

1. `MemoryManager` sits at the tail of the pipeline. It watches for `LLMFullResponseEndFrame` (end of each assistant turn).
2. At turn end, it counts user/assistant/tool messages (system messages are pinned — never evicted).
3. If the count exceeds `MEMORY_MAX_MESSAGES` (default 20), it trims down to half the cap. The trimmed block becomes the summary input.
4. If `MEMORY_SUMMARIZE=1` (default), a background `asyncio` task calls the same LLM service with a "summarize in 2-3 sentences" prompt.
5. On success, the summary is inserted (or updated, if one already exists) as a system message right after the persona's SOUL prompt:

   ```
   system: <skill system prompt — SOUL.md>
   system: Previous conversation summary: <rolling summary>
   user:   <recent turns>
   ...
   ```

## Tunables

| Variable | Default | Purpose |
|:---|:---|:---|
| `MEMORY_MAX_MESSAGES` | `20` | Prune threshold. Counts user/assistant/tool; system messages don't count. |
| `MEMORY_SUMMARIZE` | `1` | Set `0` to drop overflow silently with no summary |

`MEMORY_MAX_MESSAGES` of 20 = 10 turns (user + assistant per turn). Adjust up for longer-horizon conversations at the cost of slower TTFB.

## Failure modes

- **Summary call times out or errors** — logged as a warning, overflow messages are dropped without a summary. Conversation continues.
- **Multiple prunes stacking** — `_summarizing` flag guards against concurrent summary jobs. If a second prune fires while the first's summary is in flight, the overflow is trimmed but not summarized (the older summary will cover it).
- **Summary is wrong / hallucinates** — you'll hear it on the next tool-less turn as the agent misremembers. Prompt the summarizer tighter, or set `MEMORY_SUMMARIZE=0` to skip.

## Intentional non-features

- **No persistence.** Memory lives in the session's LLMContext. Close the tab, memory's gone. Persistence across sessions (Graphiti or similar) is a potential later milestone — the design explicitly avoids the complexity for now.
- **No semantic recall.** No vector search, no embedding. Just a rolling summary + recent window. This is fine for ~15-minute voice sessions.
- **No per-user memory.** Module-level singleton pipeline ties memory to the connection. Multi-tenant will refactor this.

## Observing it

Watch the server log for:

```
[memory] pruning 10 oldest message(s) (keeping 10 recent)
[memory] summary inserted (187 chars)
```

Tail with:

```bash
docker logs -f protovoice | grep memory
```
