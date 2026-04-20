# Two-Model Split

protoVoice is architected around a two-model split, even though the current M2 scaffold runs everything through one vLLM instance.

## The idea

- **Small router model** — tiny, fast, local. Handles chitchat, trivial answers, and routing decisions (including tool dispatch).
- **Thinker model** — heavyweight, either local (Qwen 35B / 122B) or via the LiteLLM gateway (Claude Opus, GPT-4, etc.). Handles real reasoning when the router says so.

Router lives in the voice turn loop. Thinker runs behind the `deep_research` tool (or future equivalents).

## Why split

**TTFA.** The router needs to respond in < 200 ms. A 4 B model on a local GPU does 150-300 tok/s; a 35 B model does ~50 tok/s; a gateway call adds 200-500 ms of network. The router is in the critical path; the thinker is not.

**Cost.** Routing most turns to a tiny local model burns zero API tokens. Only the actual hard questions hit the gateway.

**Latency isolation.** The thinker can take 2-30 s without blocking the conversation, because it runs behind an async tool call with a filler.

## Current state (M2)

For validation, the router and thinker are the same model — whatever `LLM_URL` serves. This works but doesn't exercise the split. M3+ will bring up two endpoints:

- `LLM_URL` → small local vLLM (router)
- `THINKER_URL` → gateway (thinker)

`deep_research` dispatches to `THINKER_URL`. Other tools reuse it.

## Router prompt discipline

The router gets a system prompt that says "if the user's question requires external info, call the `deep_research` tool; otherwise answer directly." Tight prompts matter because a small model's instruction-following is fragile.

## Thinker latency management

The thinker can take 30+ s. During that time:

- Filler on dispatch (M2 ✓)
- Periodic narrated progress (M2 ✓)
- Optional result-push interruption (M3 +)
- Eventually: streaming thinker output through the router for "partial answer" narration — still theoretical

## Why not one model

One good model at every turn is simpler and generally better quality. It's also 3-5x more expensive and 2-10x slower at steady state. For a voice agent where the user is listening *live*, the latency tax is unacceptable for most turns.

If gateway latency drops below 300 ms and local inference becomes cheap enough, the split collapses. Not there yet.

## References

- The pattern originates from protoUI (`protoLabsAI/protoUI`), which used local Qwen 4B + Opus via LiteLLM. We're porting the same split with pipecat's native tool-call plumbing replacing protoUI's hand-rolled threading.
