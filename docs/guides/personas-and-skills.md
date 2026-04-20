# Personas & Skills

A **skill** is a named voice persona — system prompt, TTS voice, LLM tuning parameters. The default persona is defined by `config/SOUL.md`; alternative personas live in `config/skills/*.yaml`.

## The dropdown

On the main page, pick a skill from the dropdown and click **Start**. The dropdown takes effect at the *next* connection — not mid-session, because we'd have to tear down and rebuild the Pipecat pipeline to swap the TTS voice cleanly. Snapshot-at-connect matches how users reason about it anyway.

## Writing a skill YAML

```yaml
# config/skills/tutor.yaml
slug: tutor
name: Tutor
description: |
  A patient tutor who answers questions with follow-up nudges.

system_prompt: |
  You are a friendly tutor. Answer questions in one or two sentences,
  then ask one gentle follow-up to check the user's understanding.
  Never lecture. Don't use markdown — you're speaking aloud.

tts_backend: kokoro       # 'kokoro' or 'fish'
voice: af_nicole          # Kokoro voice id OR Fish reference_id
lang: a                   # kokoro language code (optional)
temperature: 0.6
max_tokens: 120
filler_verbosity: brief   # overrides session verbosity on connect (optional)
```

### `system_prompt` vs `system_prompt_file`

Inline prompts stay in-YAML. For longer prompts, point at a file:

```yaml
system_prompt_file: tutor.md
```

The path is relative to the YAML file.

### Tool restriction

```yaml
tools: [calculator, get_datetime, web_search]
```

When set, the skill's session only sees the listed tools — the LLM literally can't call anything else because the others aren't in its `ToolsSchema`. Empty list (default) = expose every registered tool.

Useful for keeping personas in lane: a kitchen-savvy `chef` skill doesn't need `a2a_dispatch` or `slow_research`; a customer-support skill might want only `web_search` + a `lookup_order` tool you've added.

Unknown tool names in the list are logged as warnings and ignored. If your list filters down to zero tools, protoVoice refuses to leave the agent toolless and falls back to exposing all (with a warning).

## The default persona — `SOUL.md`

`config/SOUL.md` is the default skill. Plain markdown, no frontmatter. Edit freely — changes take effect on server restart. The default ships with a chief-of-staff-ish tone and directives about when to dispatch to other agents.

## TTS voice choice per skill

- `tts_backend: kokoro` with `voice: af_heart` (or any Kokoro preset) — low-latency, fixed voice. Good for skills where character matters less than speed.
- `tts_backend: fish` with `voice: <reference_id>` — high-quality, cloneable. The reference must already be saved on the Fish server (see [Clone a Voice](./clone-a-voice)).

The skill's `tts_backend` overrides the session-level `TTS_BACKEND` env for that connection. So a `chef` skill can speak in Kokoro even when the session default is Fish, and vice versa.

## Switching via the API

```bash
curl -X POST http://localhost:7867/api/skills \
  -H "Content-Type: application/json" \
  -d '{"slug":"chef"}'
# → {"active":"chef"}
```

## Programmatic iteration

```bash
curl http://localhost:7867/api/skills | jq .
```

Returns the active slug and the full skill list with names + descriptions.
