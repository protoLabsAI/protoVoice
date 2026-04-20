# Pipeline Shape

The full Pipecat pipeline as it exists in `app.py::run_bot`.

## Order

```python
Pipeline([
    transport.input(),     # SmallWebRTCInputTransport — mic RTP → AudioRawFrame
    stt,                   # LocalWhisperSTT — HF Whisper large-v3-turbo
    user_agg,              # LLMUserAggregator — VAD turn-taking + context build
    backchannel,           # BackchannelController — "mm-hmm" during long user turns (M9)
    delivery,              # DeliveryController — drains push-results (M3+)
    llm,                   # OpenAILLMService — vLLM / external
    tts,                   # FishAudioTTS or LocalKokoroTTS
    transport.output(),    # SmallWebRTCOutputTransport — TTS → RTP → speaker
    assistant_agg,         # LLMAssistantAggregator — records agent turns into context
    memory,                # MemoryManager — sliding window + summary (M5)
])
```

- **BackchannelController** watches `UserStartedSpeakingFrame` / `UserStoppedSpeakingFrame` to fire brief listener-acks during long user utterances. See [Backchannels](/guides/backchannels).
- **DeliveryController** watches the same VAD frames + `TranscriptionFrame` to decide when to drain queued push results. See [Delivery Policies](/guides/delivery-policies).
- **MemoryManager** watches `LLMFullResponseEndFrame` for turn boundaries and triggers async pruning + summarization. See [Memory](/reference/memory).

## Pre-tool acknowledgements

Pipecat's `OpenAILLMService` streams text deltas to TTS *before* running function calls — this is what makes inline pre-tool preambles work. The model emits `"hmm, let me check"` as `delta.content` tokens, those tokens flow through TTS to the user, then the function call executes, then post-tool tokens flow back through TTS as the answer. One LLM call, one stream, no race conditions.

The TOOL USE block in the system prompt (built by `agent/filler.tool_use_block`) instructs the model to do this on every tool call. See [Natural-Sounding Fillers](/explanation/natural-fillers).

## Key frame types

Upstream (user → agent):

- `InputAudioRawFrame` — 20 ms PCM chunks from the mic
- `UserStartedSpeakingFrame` / `UserStoppedSpeakingFrame` — from VAD
- `InterruptionFrame` — broadcast when VAD sees the user start mid-bot-response
- `TranscriptionFrame` — final STT output, flows into the user aggregator

Downstream (agent → user):

- `LLMRunFrame` — optional "kick off the LLM" signal (we don't use it; user turns trigger the run automatically)
- `LLMFullResponseStartFrame` — LLM response opening marker
- `LLMTextFrame` — per-token text from the LLM stream
- `TTSTextFrame` — aggregated sentence headed to TTS
- `TTSSpeakFrame` — "speak this text now" (our duplex primitive for filler)
- `TTSAudioRawFrame` — int16 PCM output from the TTS service
- `OutputAudioRawFrame` — resampled to the transport's SR; the browser plays this
- `LLMFullResponseEndFrame` — LLM response close marker

Control:

- `StartFrame` — pipeline init (sets sample rates on services)
- `EndFrame` — graceful shutdown (uninterruptible)
- `CancelFrame` — hard stop

## Latency budget (steady-state)

| Stage | Typical |
|:---|:---:|
| STT (Whisper large-v3-turbo, 8 s audio) | 55 ms |
| LLM TTFB (Qwen 4B local / 35B local) | 100-200 ms |
| LLM first sentence | +200-500 ms |
| TTS TTFA (Fish streaming PCM) | 400-800 ms |
| TTS TTFA (Kokoro) | ~50 ms |
| Transport output + browser playback | ~30 ms |

End-to-end TTFA: **~600-1500 ms** with Fish, **~200-400 ms** with Kokoro.

## Adding a processor

Drop it into the `Pipeline([...])` list at the right position. For example, a debugging frame tracer between LLM and TTS:

```python
class Tracer(FrameProcessor):
    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)
        logger.info(f"{type(frame).__name__}")
        await self.push_frame(frame, direction)

pipeline = Pipeline([... llm, Tracer(), tts, ...])
```

Processors see every frame. Remember to `await super().process_frame(...)` first and `await self.push_frame(...)` at the end, or the pipeline stalls.

## Tools + function calling

`agent/tools.register_tools(llm, on_finish=...)` registers handlers on the LLM service. Tool schemas attach to the `LLMContext`:

```python
context = LLMContext(messages, tools=tools_schema)
```

When the LLM emits a tool call, pipecat runs the handler, takes the result, re-enters the LLM for the final spoken response. `on_function_calls_started` / `on_function_calls_cancelled` are the lifecycle hooks. There is no `on_function_calls_finished` — cancel long-running work from inside the handler itself.
