# protoVoice Skills

Drop `.md` files here to add new voice agent modes. Each file becomes a selectable mode in the UI.

Files starting with `_` are ignored by the loader.

## Frontmatter Fields

```yaml
---
name: My Skill          # Display name in the mode dropdown (required)
slug: my-skill          # URL-safe identifier, defaults to filename stem
description: ...        # Short description (optional)
voice: af_heart         # Kokoro voice name (default: env KOKORO_VOICE)
lang: a                 # Kokoro lang code: a=American, b=British, j=Japanese (default: env KOKORO_LANG)
tools: []               # Reserved for future agent tool filtering
max_tokens: 200         # Max LLM output tokens (default: 200)
temperature: 0.7        # LLM temperature 0.0–1.0 (default: 0.7)
llm_url: null           # Override LLM endpoint URL (default: env LLM_URL)
model: null             # Override served model name (default: env LLM_SERVED_NAME)
---
```

## Body

Everything below the closing `---` becomes the system prompt for this mode.

Keep prompts voice-optimized:
- Instruct the model to respond in 1-3 spoken sentences
- No markdown, no lists, no formatting
- Conversational and natural

## Kokoro Voices

| Voice | Lang | Style |
|-------|------|-------|
| af_heart | a | Warm American female |
| af_bella | a | Bright American female |
| af_sarah | a | Clear American female |
| am_adam | a | American male |
| am_michael | a | Deep American male |
| bf_emma | b | British female |
| bm_george | b | British male |

Custom cloned voices appear here once created via `voices.py`.
