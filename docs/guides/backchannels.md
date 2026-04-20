# Backchannels

Backchannels are the brief listener-acks ("mm-hmm", "yeah", "right") humans drop into someone else's turn to signal "I'm tracking, keep going." They're different from filler:

| | Filler | Backchannel |
|:---|:---|:---|
| Fires when | Tool dispatch (agent stalling for compute) | User is mid-utterance (agent listening) |
| Speaker | Agent thinking | Agent acknowledging |
| Length | A few words | One or two tokens |
| Volume | Normal | Quiet (Fish: `[whisper] [softly]`) |

## How it works

`BackchannelController` (`agent/backchannel.py`) sits in the pipeline next to `DeliveryController` and watches `UserStartedSpeakingFrame` / `UserStoppedSpeakingFrame`:

1. User starts speaking → start a timer.
2. After `BACKCHANNEL_FIRST_SECS` (default 5s), generate one backchannel via the same `FillerGenerator` and queue it.
3. Continue every `BACKCHANNEL_INTERVAL_SECS` (default 6s) until the user stops.
4. User stops → cancel the timer.

Most user turns are <5s and never trigger a backchannel. Long monologues get a polite "mm-hmm" partway through.

## Backend rendering

Same backend-aware approach as filler:

| Backend | Sample backchannel |
|:---|:---|
| Fish | `[softly] uh-huh` / `[softly] mhm` / `[softly] yeah` |
| Kokoro | `right` / `hmm` / `uh` |

The Fish wrapper (`[softly]` or `[whisper] [softly]` if missing) keeps the backchannel quiet enough that it doesn't compete with the user's voice.

## Tunables (env)

| Variable | Default | Purpose |
|:---|:---|:---|
| `BACKCHANNEL_FIRST_SECS` | `5.0` | Seconds into a user turn before the first backchannel |
| `BACKCHANNEL_INTERVAL_SECS` | `6.0` | Interval between subsequent backchannels |

## Suppression

- `VERBOSITY=silent` — no backchannels (or any filler) fire.
- A user turn shorter than `BACKCHANNEL_FIRST_SECS` — no backchannel; timer cancels on stop.
- LLM generator timeout/error — the backchannel is dropped (logged as warning); the next interval tries again.

## Why generative

The same reasons as [filler](/explanation/natural-fillers): a fixed pool of "mm-hmms" pattern-matches to "scripted" within a session. Generative + recency-aware = unique each time, never the same shape twice in close succession.

## Caveats

- **Pipecat may suppress backchannels** if the bot is mid-TTS when the user starts speaking — the InterruptionFrame broadcast clears in-flight audio. In normal conversation flow (user listening → user starts speaking → backchannel), the bot ISN'T speaking so the audio plays cleanly.
- **Volume mismatch** — if you find Fish backchannels too loud, edit the wrap in `agent/filler.FillerGenerator.backchannel()` from `[softly]` to `[whisper] [softly]`. Whisper is much quieter but loses some intelligibility — try in your room first.
- **Latency to first backchannel matters** — the LLM generator takes ~50-200ms. If you set `BACKCHANNEL_FIRST_SECS=2`, generation may finish AFTER the user has already paused. The 5s default leaves plenty of headroom.

## Disabling

Set `BACKCHANNEL_FIRST_SECS` to a value larger than your longest user utterance (e.g. `999`) — the timer never fires. Or set `VERBOSITY=silent` to mute filler + backchannel together.
