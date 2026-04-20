# Configure Verbosity

Verbosity controls how chatty the agent is **while a tool is running** — the speak-while-thinking ("filler") track. It's independent of how long the final answer is.

::: tip M8 update
Filler is now generated per-turn by the local LLM, with backend-aware prosody and topic grounding. There is no phrase pool. See [Natural-Sounding Fillers](/explanation/natural-fillers) for the design.
:::

## Levels

Each level shapes the generator prompt, not a static phrase list:

| Level | What gets generated |
|:------|:---|
| `silent` | (no filler at all — generator is bypassed) |
| `brief` *(default)* | 2-4 words, low energy, half-thinking |
| `narrated` | 3-8 words, warm, grounded in the user's topic |
| `chatty` | up to 12 words, slightly expressive, light commentary |

Each filler is unique, references the user's actual topic, and uses backend-appropriate prosody (Fish gets `[hmm]` `[pause]` `[softly]` etc.; Kokoro gets plain text).

## Latency tiers gate when filler fires

Each tool registers an expected latency tier. The generator stays silent for FAST tools because the answer arrives sooner than any filler could:

| Tier | Filler behaviour |
|:---|:---|
| `FAST` (calculator, get_datetime) | Silent. No opening, no progress. |
| `MEDIUM` (web_search, deep_research, a2a_dispatch) | One opening filler. No progress. |
| `SLOW` (slow_research, long delegations) | Opening + periodic generated progress (every ~4s). |

This kills the "let me dig in!" you used to hear on `15 * 1.2 + 3`.

## Pick at startup

```bash
VERBOSITY=narrated docker compose up -d protovoice
```

## Change during a session

The UI dropdown switches at runtime. Under the hood:

```bash
curl -X POST http://localhost:7867/api/verbosity \
  -H "Content-Type: application/json" \
  -d '{"level":"chatty"}'
```

Read current:

```bash
curl http://localhost:7867/api/verbosity
# → {"verbosity":"brief"}
```

Session-level, not persisted. Default comes from `VERBOSITY` env, falling back to `brief`.

## Tuning the cadence

Progress fillers (SLOW tools only) are gated on:

- `progress_after_secs` — wait this long after the tool starts before the first progress phrase. Default 3.0 s. Changeable in `agent/filler.Settings`.
- `progress_interval_secs` — interval between subsequent progress phrases. Default 4.0 s.

Short-lived tools that finish before `progress_after_secs` only get the opening — no "still looking" stutter.

## Sample output

Same query ("what's the weather in Tokyo?"), different verbosity + backend:

```
brief    + fish:    "[hmm] checking Tokyo's weather"
brief    + kokoro:  "one sec, on it"
narrated + fish:    "[softly] [pause] alright, pulling Tokyo's forecast now"
narrated + kokoro:  "okay, looking up the weather in Tokyo for you"
chatty   + fish:    "[thinking] hmm, Tokyo weather — let me get the latest"
chatty   + kokoro:  "good question — checking the current Tokyo conditions now"
```

Each is generated fresh per call. None come from a list.

## When the generator fails

The pipeline never blocks on filler. If the LLM times out (>2.5s) or errors, no filler fires that turn — the tool keeps running and the real answer plays normally. You'll see a `WARNING [filler:gen]` in the logs.

## Persona override

Skill YAMLs can override session verbosity per persona:

```yaml
slug: chef
filler_verbosity: brief   # Chef Bruno is terse — he's busy
```

See [Personas & Skills](./personas-and-skills).
