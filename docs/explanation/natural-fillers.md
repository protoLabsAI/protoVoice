# Natural-Sounding Fillers

protoVoice's filler — the "thinking out loud" speech the agent produces while a tool runs — is **generated per turn by a small local LLM**, with backend-aware prosody and topic grounding. There is no phrase pool, no `random.choice`, no canned strings.

This page explains why, and how the design borrows from production agents that sound the most natural.

## The problem with phrase pools

Our v1 was the obvious thing: 5-10 hand-written phrases per verbosity level, picked at random per dispatch. It works for the first session. By minute three you've heard "let me check" four times. The brain pattern-matches and the agent registers as "scripted."

Even with anti-repetition (LRU over recent picks), the surface stays canned because:

- All fillers sound like *announcements*: "Let me look that up." (where? for what?)
- They never reference what the user actually asked.
- TTS reads them with the same energy as the real answer — there's no prosodic difference between "thinking" and "answering."

## What production agents actually do

Cross-referenced from the [research brief](https://github.com/protoLabsAI/protoVoice/discussions) (Sesame, OpenAI Realtime API, LiveKit, Vapi, ElevenLabs, Hume, Fish):

- **Sesame Maya/Miles** generates disfluencies and breaths *end-to-end* from conversation history. No filler track — the model just sounds like it's thinking because it was trained on people who do.
- **OpenAI Realtime** instructs the model to emit ONE short "thinking" line *before* every tool call, and forbids repeating any sentence in a session.
- **ElevenLabs Conv. AI** auto-inserts context-aware fillers ("Hmm…", "I see…") computed from the last ~4 messages. Once per turn, never stacked.
- **Vapi** treats backchannels, fillers, and disfluencies as three separate orthogonal toggles.
- **LiveKit** uses inline SSML breaks: `"Yeah, um <break time='300ms'/> so..."` — the 300 ms pause after "um" is the load-bearing detail.
- **Fish Audio S2-Pro** supports 15,000+ inline tags (`[softly]`, `[pause]`, `[hmm]`, `[thinking]`, `[whisper]`) that modulate prosody on subsequent words.

The convergent pattern: **filler is generated, short, topic-grounded, prosody-modulated, never repeated**.

## How protoVoice does it

The `FillerGenerator` (`agent/filler.py`) is a thin wrapper around a small local LLM. On each tool dispatch:

1. **Latency tier check** — each tool registers `FAST` / `MEDIUM` / `SLOW`. FAST tools (calculator, datetime) skip filler entirely; the answer arrives sooner than any filler could.
2. **Prompt assembly** — the generator takes the user's last utterance, the tool name, the tool arguments, the active TTS backend, and the recent-fillers buffer.
3. **Backend-aware style block** — Fish path gets a "use [softly] [pause] [hmm] [thinking] tags sparingly, examples..." instruction. Kokoro path gets the opposite: "plain text only, bracketed tags get spoken as literal sounds."
4. **Verbosity-shaped length** — `BRIEF` says "2-4 words, low energy"; `NARRATED` says "3-8 words, ground in topic"; `CHATTY` allows up to 12 with light commentary.
5. **One-shot LLM call** — `temperature=0.9`, `max_tokens=30`, hard timeout 2.5 s. If it fails or times out, no filler fires; the pipeline keeps going.
6. **Recency buffer** — the last 6 fillers are replayed back to the prompt as "AVOID repeating these." Anti-repetition by construction.
7. **Off-critical-path** — generation runs as a fire-and-forget asyncio task, queued via `task.queue_frame(TTSSpeakFrame(...))` when ready. The tool dispatch isn't blocked by the generator.

## Latency-tier semantics

```
FAST   <500ms tools (calculator, get_datetime)
       Filler is silent. Answer is faster than any filler could be.

MEDIUM 0.5-3s tools (web_search, deep_research, a2a_dispatch)
       One opening filler. No progress.

SLOW   3s+ tools (slow_research, long delegations)
       Opening filler + periodic generated progress lines (also unique
       per call, also recency-aware).
```

A quick `15 * 1.2 + 3` should NOT produce a filler. A 4-second research call should produce ONE opening but no progress chorus. A 30-second slow tool gets opening + a progress line every ~4 s.

## Backend-aware sample output

Same tool, same query — different backend:

```
fish:    [hmm] [pause] let me look up the history of hot dogs for you
fish:    [um] [pause] [thinking] diving into hot dog history
kokoro:  okay, let me dig into that for you
kokoro:  hmm, let me pull up some details on hot dogs
```

The Fish version reads as actual hesitation. The Kokoro version reads as a friendly acknowledgement. Both grounded in "hot dogs." Neither is on a phrase list.

## Tunables (env)

| Variable | Default | Purpose |
|:---|:---|:---|
| `VERBOSITY` | `brief` | `silent` / `brief` / `narrated` / `chatty` (also session-mutable via `/api/verbosity`) |

A future revision will expose `FILLER_TEMPERATURE`, `FILLER_TIMEOUT_SECS`, `FILLER_RECENCY_WINDOW`, and `FILLER_MODEL` if the defaults need tuning per persona. For M8 they're hardcoded to working values.

## When generation fails

The pipeline never blocks on filler. Specifically:

- LLM timeout (>2.5s) → drop the filler, log a warning, keep going.
- LLM error → same.
- Empty response → same.
- Late arrival (real response already speaking) → pipecat queues it; it speaks after the response finishes, slightly out of place. Acceptable.

The agent is always answerable; filler is a perceptual nicety, not a correctness path.

## What's next

- **M9 — Backchannels:** brief listener acks ("mm-hmm", "yeah") fired DURING long user utterances, not after. Different trigger, similar generative approach.
- **Persona-tuned filler temperature:** chef-mode might want chattier; researcher-mode terser.
- **Skill-overridable verbosity:** already wired via `filler_verbosity` in skill YAMLs (M5).

## Sources behind the design

- [LiveKit — Prompting voice agents to sound more realistic](https://livekit.com/blog/prompting-voice-agents-to-sound-more-realistic/)
- [OpenAI Cookbook — Realtime Prompting Guide](https://developers.openai.com/cookbook/examples/realtime_prompting_guide)
- [ElevenLabs — Conversation flow / soft-timeout fillers](https://elevenlabs.io/docs/eleven-agents/customization/conversation-flow)
- [Vapi — Prompting guide (filler/backchannel/disfluency)](https://docs.vapi.ai/prompting-guide)
- [Sesame — Crossing the uncanny valley of voice](https://www.sesame.com/research/crossing_the_uncanny_valley_of_voice)
- [Fish Audio S2 — Fine-grained voice control at the word level](https://fish.audio/blog/fish-audio-s2-fine-grained-ai-voice-control-at-the-word-level/)
- [Kwindla on Pipecat `TTSSpeakFrame` for tool-call filler](https://x.com/kwindla/status/1939800741155414236)
- [Pipecat function-calling example](https://github.com/pipecat-ai/pipecat/blob/main/examples/foundational/14-function-calling.py)
- [Retell AI — How to build a good voice agent](https://www.retellai.com/blog/how-to-build-a-good-voice-agent)
