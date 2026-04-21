# Duplex Design

Standard voice agents are **half-duplex**: user speaks, agent responds, turns alternate. protoVoice aims to be **full-duplex** in two specific senses.

## Speak-while-thinking

When the agent calls a tool that takes several seconds, normal agents go silent. Users interpret silence as "it didn't hear me" and often re-ask. Bad.

protoVoice emits a short filler phrase the moment a tool dispatches — "Hold on.", "Let me look that up." — and, if the tool drags on, keeps narrating progress: "Still looking.", "Almost there."

Mechanism:

1. LLM emits a tool call. Pipecat fires `on_function_calls_started`.
2. Our handler queues `TTSSpeakFrame(<opening_phrase>)` on the pipeline task. This is a special frame that Pipecat synthesizes and plays *immediately*, independent of the current LLM turn.
3. A background `_progress_loop()` coroutine fires a **two-tier cadence** (Alexa pattern): first ack after `progress_first_secs` (~2 s), second ack after an additional `progress_second_secs` (~6 s). Then stops — over-narrating past ~8 s reads as performative. Cancelled on tool completion or barge-in.
4. The tool handler is wrapped: on return (success or failure) it cancels the progress loop.

The opening phrase is gated on verbosity. `silent` emits nothing. `brief` emits a short filler. `narrated` and `chatty` emit progressively more.

## Push-interrupt

For long-running tools — an A2A call to another agent, a multi-source web search — the agent shouldn't block the entire turn. Users want to keep talking.

The duplex solution: register the tool with `cancel_on_interruption=False`. Pipecat treats it as async. The LLM returns control immediately — the user can chat about other things. When the tool completes, its result is injected back into the conversation as a "developer" message and the agent speaks the result at the next opportunity (or barges in, if the delivery policy says so).

Pipecat's native async tool path handles the plumbing. Our `TaskInbox` concept from the original design is redundant — pipecat already implements it.

M3 switches `deep_research` to `cancel_on_interruption=False` to validate this. M4 adds the delivery-policy matrix:

- **`now`** — push the result immediately, interrupting the user if necessary. Reserved for results the user is actively waiting for.
- **`next_silence`** — wait for VAD-detected silence, then speak. The default.
- **`when_asked`** — only speak if the user references the topic. Quietest option.

## Why this isn't a race

Two things could collide:

- **Filler and the real answer overlap.** Pipecat serializes `TTSSpeakFrame`s in queue order. Filler goes first, real response second. If the real response arrives mid-filler, pipecat's text aggregator holds it until the filler finishes speaking.
- **Push and barge-in collide.** If a push result arrives while the user is speaking, VAD fires `UserStartedSpeakingFrame` and broadcasts interruption. The push frame gets dropped by pipecat's interruption handler unless marked `now`, in which case we force it through by bypassing the user aggregator (planned M3 detail).

## Barge-in vs tool cancellation

By default tools register with `cancel_on_interruption=True` — the user speaking mid-tool cancels the running tool. Good for synchronous "let me check that" flows. Bad for "kicked off a 2-minute research task in the background" flows.

M4+ will let the tool schema declare `cancelable: bool`. The LLM can inspect this and choose whether to use it based on expected duration.

## Filler UX gotchas

- Fillers overlapping trivial <2 s responses is jarring. Solve by skipping the opening filler if the LLM's own response is expected to start within 1 s (tunable).
- Repeating the same phrase loop ("one sec... one sec... one sec") is grating. Phrase pool + randomization; eventually we should track recent phrases per session to avoid repeats.
- User interrupts the filler. Current behaviour: VAD fires, interruption broadcast, filler cuts off, user turn takes over. Good default.
