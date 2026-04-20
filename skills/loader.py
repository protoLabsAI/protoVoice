"""Discover + load voice skills from YAML files.

Layout:

    config/
      SOUL.md                 ← default persona's system prompt
      skills/
        chef.yaml             ← alternate persona
        tutor.yaml            ← alternate persona

Each skill YAML maps to `skills.models.Skill`. The `system_prompt` field
can be inline or a reference to a file (`system_prompt_file: path.md`).
"""

from __future__ import annotations

import logging
from pathlib import Path

import yaml

from .models import DEFAULT_SOUL_SLUG, Skill

logger = logging.getLogger(__name__)


def load_default_soul(
    config_dir: str | Path = "config",
    *,
    voice: str | None = None,
    tts_backend: str = "fish",
) -> Skill:
    """The 'default' skill — SOUL.md for its system prompt, env-default TTS."""
    path = Path(config_dir) / "SOUL.md"
    prompt = path.read_text().strip() if path.exists() else (
        "You are a helpful voice assistant. Keep responses concise — "
        "1-3 sentences max. Be conversational. No markdown."
    )
    return Skill(
        slug=DEFAULT_SOUL_SLUG,
        name="Default",
        system_prompt=prompt,
        tts_backend=tts_backend,
        voice=voice,
    )


def load_skill_file(path: Path) -> Skill | None:
    try:
        data = yaml.safe_load(path.read_text()) or {}
    except Exception as e:
        logger.warning(f"[skills] failed to read {path}: {e}")
        return None

    slug = data.get("slug") or path.stem
    name = data.get("name") or slug.replace("-", " ").replace("_", " ").title()

    # system_prompt can be inline or a file reference.
    prompt = data.get("system_prompt", "").strip()
    prompt_file = data.get("system_prompt_file")
    if prompt_file:
        pf = path.parent / prompt_file
        if pf.exists():
            prompt = pf.read_text().strip()
        else:
            logger.warning(f"[skills] {slug}: system_prompt_file {pf} missing")

    if not prompt:
        logger.warning(f"[skills] {slug}: no system_prompt; skipping")
        return None

    return Skill(
        slug=slug,
        name=name,
        system_prompt=prompt,
        tts_backend=(data.get("tts_backend") or "fish").lower(),
        voice=data.get("voice"),
        lang=data.get("lang"),
        temperature=float(data.get("temperature", 0.7)),
        max_tokens=int(data.get("max_tokens", 150)),
        description=data.get("description", "").strip(),
        filler_verbosity=data.get("filler_verbosity"),
        tools=list(data.get("tools", [])),
    )


def load_skills(config_dir: str | Path = "config") -> dict[str, Skill]:
    """Return {slug: Skill} including the default."""
    config_dir = Path(config_dir)
    out: dict[str, Skill] = {}

    out[DEFAULT_SOUL_SLUG] = load_default_soul(config_dir)

    skills_dir = config_dir / "skills"
    if skills_dir.exists():
        for f in sorted(skills_dir.glob("*.yaml")):
            skill = load_skill_file(f)
            if skill:
                out[skill.slug] = skill

    logger.info(f"[skills] loaded {len(out)} skills: {list(out.keys())}")
    return out


def write_voice_clone_skill(
    slug: str,
    name: str,
    reference_id: str,
    *,
    description: str = "",
    config_dir: str | Path = "config",
) -> Path:
    """Write a skill YAML for a newly-cloned Fish voice.

    The generated skill reuses SOUL.md as its system prompt so the new
    voice picks up the default persona. Edit the YAML later to customize.
    """
    cfg = Path(config_dir)
    skills_dir = cfg / "skills"
    skills_dir.mkdir(parents=True, exist_ok=True)
    path = skills_dir / f"{slug}.yaml"
    doc = {
        "slug": slug,
        "name": name,
        "description": description,
        "system_prompt_file": "../SOUL.md",
        "tts_backend": "fish",
        "voice": reference_id,
    }
    path.write_text(yaml.safe_dump(doc, sort_keys=False))
    logger.info(f"[skills] wrote {path}")
    return path
