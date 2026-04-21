# Delivery Policies

Tools can return results in three different ways. Verbosity controls *how chatty* the filler is; delivery policy controls *when* the real result gets spoken.

## Priority (the front-door API)

Callers normally pass a **priority** — the controller maps it to the right policy. Priority model matches Apple's [UNNotificationInterruptionLevel](https://developer.apple.com/documentation/usernotifications/unnotificationinterruptionlevel):

| Priority | Auto-maps to | Use for |
|:---|:---|:---|
| `critical` | `now` | Hard alerts the user must hear immediately |
| `time_sensitive` | `next_silence` | Normal push results — wait for the next pause |
| `active` *(default)* | `when_asked` | Results that matter only if the user comes back to the topic |
| `passive` | `when_asked` (TODO: dedicated digest) | Low-signal background info, hold for a digest |

Explicit `policy=` still wins if a caller needs to override the mapping.

## The three policies

| Policy | When it speaks | Use for |
|:---|:---|:---|
| `now` | Immediately — interrupting the user if they're speaking | Urgent alerts, hard deadlines |
| `next_silence` | Next VAD-detected user silence + 600 ms settle | Normal push results |
| `when_asked` | Only if the user's next utterance contains a query keyword | Background lookups that might not matter anymore |

`now` and `when_asked` are opt-in via `controller.deliver(..., priority=)` or by passing `policy=` directly.

## How it plumbs

```
LLM calls slow_research(query="history of hot dogs")
  ↓
Tool handler returns to LLM immediately:
  "Sure — I'll look that up and let you know. You can keep talking."
  (LLM speaks this as its turn; user can chat in the meantime)
  ↓
Background asyncio task runs the real work
  ↓
After 20s: handler calls controller.deliver(result, policy=NEXT_SILENCE, keywords=...)
  ↓
DeliveryController is a pipeline processor; it sees VAD + transcripts
  ↓
  next_silence:  waits for UserStoppedSpeakingFrame + 600ms,
                 then pushes TTSSpeakFrame(result)
  now:           pushes TTSSpeakFrame immediately (barges in)
  when_asked:    holds until next TranscriptionFrame matches keywords
```

## Keyword matching for `when_asked`

The tool handler passes keywords derived from the original query:

```python
keywords = tuple(w for w in query.split() if len(w) > 3)
await controller.deliver(phrase, priority=Priority.ACTIVE, keywords=keywords)
```

Matching is a naive case-insensitive substring search. If any keyword appears in the user's next utterance, the result drops. No synonyms, no stemming. A smarter matcher (embedding similarity) is an obvious upgrade.

## Filler during async tools

When an async tool dispatches, the opening filler still fires (via `on_function_calls_started`), but:

- Progress loop does NOT run for async tools — the LLM's initial "I'll look into it" reply handles the acknowledgement.
- Delivery is driven entirely by the policy when the tool completes.

This avoids the annoying "still looking, still looking, still looking" chorus when the user has already moved on.

## Testing locally

`slow_research` is wired as an async tool for validation. Ask:

> "Can you investigate the history of hot dogs when you have a moment?"

The agent should:
1. Acknowledge the request ("Sure — I'll look into that...")
2. Let you chat about other things for ~20 seconds
3. Drop in when you pause: "Okay, I found what you asked about the history of hot dogs..."

Tune the sleep with `SLOW_RESEARCH_SECS`.

## Known edge cases

- **Two overlapping async results.** Both queue; both get drained at the next silence. They play back-to-back. Not ideal; no interleaving or summarization yet.
- **User mutes their own mic.** VAD never sees user-stopped, so `next_silence` never fires. Fallback timer (planned): deliver after 10 s regardless.
- **`when_asked` that never matches.** The result sits forever. No TTL yet; results accumulate. Planned: expire after N minutes.
