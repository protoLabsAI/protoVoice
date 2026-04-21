# Memory

Per-session memory is a **token-budgeted sliding window with LLM summarization**, powered by pipecat's built-in `LLMContextSummarizer`. It prevents the LLM context from growing unboundedly while preserving the gist of older turns.

## How it works

`LLMContextSummarizer` is wired into the assistant aggregator, not a separate pipeline processor. It watches every assistant turn and triggers a summarization pass when either threshold is crossed:

- **Token limit** (`MEMORY_MAX_CONTEXT_TOKENS`, default 8000) — approximate, 4 chars/token.
- **Message count** (`MEMORY_MAX_MESSAGES`, default 20) — number of unsummarized user + assistant turns since the last compression.

When triggered, the summarizer asks the same LLM that drives the conversation to compress the oldest turns into a summary, keeping the most recent messages untouched. The summary lands as a system message in the context, so the agent "remembers" the gist. Summary emits a `SummaryAppliedEvent` observable via the aggregator's `on_summary_applied` handler.

## Tunables

| Variable | Default | Purpose |
|:---|:---|:---|
| `MEMORY_SUMMARIZE` | `1` | Set `0` to disable auto-summarization entirely. |
| `MEMORY_MAX_CONTEXT_TOKENS` | `8000` | Token-based trigger threshold. |
| `MEMORY_MAX_MESSAGES` | `20` | Message-count trigger threshold (user + assistant + tool). |
| `MEMORY_TARGET_CONTEXT_TOKENS` | `MEMORY_MAX_CONTEXT_TOKENS / 2` | What the summarizer tries to compress down to. Lower = more aggressive. |

Either threshold alone fires a summary — use whichever shape matters for your deployment. Long-horizon coaching sessions benefit from token gating; short task-oriented sessions rarely hit it and only trip the message cap.

## Failure modes

- **Summary call errors or times out** — the summarizer logs the failure and leaves the context alone; the next trigger retries.
- **Multiple triggers stacking** — pipecat's summarizer has an internal in-progress guard (`_summarization_in_progress`); subsequent triggers are no-ops until the first completes.
- **Summary is wrong / hallucinates** — you'll hear it on the next tool-less turn. Tune `MEMORY_TARGET_CONTEXT_TOKENS` lower (more aggressive) or turn it off with `MEMORY_SUMMARIZE=0`.

## Intentional non-features

- **No persistence across sessions** (yet — `C14` in the roadmap adds reconnect replay).
- **No semantic recall.** No vector search. Just a rolling summary + recent window.
- **No per-user memory.** Module-level singleton pipeline ties memory to the connection. Multi-tenant would refactor this.

## Observing it

Pipecat logs summarization events under the `LLMContextSummarizer` logger. Watch for:

```
[ContextSummarizer] triggered — 24 messages, ~9200 tokens → summarizing
[ContextSummarizer] summary applied — 14 messages compressed, 6 preserved
```

Tail with:

```bash
docker logs -f protovoice | grep ContextSummarizer
```
