"""Data model for a voice persona / skill."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Skill:
    """A voice persona — system prompt + TTS voice + (optionally) LLM overrides.

    `slug` is the machine id (URL-safe). `name` is the human-readable label
    for the UI.
    """

    slug: str
    name: str
    system_prompt: str
    tts_backend: str = "fish"        # "fish" or "kokoro"
    voice: str | None = None         # Kokoro voice id OR Fish reference_id
    lang: str | None = None
    temperature: float = 0.7
    max_tokens: int = 150
    description: str = ""
    filler_verbosity: str | None = None  # per-skill override; None = keep current
    tools: list[str] = field(default_factory=list)  # names to restrict to; empty = all


DEFAULT_SOUL_SLUG = "default"
