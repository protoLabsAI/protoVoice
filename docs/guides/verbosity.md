# Configure Verbosity

Verbosity controls how chatty the agent is while a tool is running — the **speak-while-thinking** behaviour. It's independent of the final-answer length.

## Levels

| Level | Opening filler | Periodic progress |
|:------|:---|:---|
| `silent` | (none) | (none) |
| `brief` *(default)* | "Hold on." | (none) |
| `narrated` | "Let me look that up." | "Still looking." every few seconds |
| `chatty` | "Good question — let me dig in on that." | "Still digging — give me a moment." |

Phrases vary per call (picked at random from a small pool per level).

## Pick at startup

```bash
VERBOSITY=narrated docker compose up -d protovoice
```

## Change during a session

The UI has a dropdown. Under the hood:

```bash
curl -X POST http://localhost:7867/api/verbosity \
  -H "Content-Type: application/json" \
  -d '{"level":"chatty"}'
```

Or read the current value:

```bash
curl http://localhost:7867/api/verbosity
# → {"verbosity":"brief"}
```

Session-level, not persisted. Default comes from `VERBOSITY` env, falling back to `brief`.

## Tuning the cadence

Progress fillers are gated on two settings in `agent/filler.py`:

- `progress_after_secs` — wait this long after the tool starts before the first progress phrase. Default 3.0 s.
- `progress_interval_secs` — interval between subsequent progress phrases. Default 4.0 s.

Tools that finish before `progress_after_secs` only get the opening filler — no "still looking" noise.

## When is verbosity annoying?

`chatty` is pleasant on first use and grating by turn five. `brief` is forgettable. `narrated` is the sweet spot for tools that routinely take 3-10 s.

Under 2 s total, drop to `silent` — the filler ends up playing OVER the real answer.
