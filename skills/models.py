from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Skill:
    slug: str
    name: str
    description: str = ""
    system_prompt: str = ""
    voice: str = "af_heart"
    lang: str = "a"
    tools: list[str] = field(default_factory=list)
    max_tokens: int = 200
    temperature: float = 0.7
    llm_url: Optional[str] = None
    model: Optional[str] = None
