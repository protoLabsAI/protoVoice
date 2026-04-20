"""Registry of known A2A agents this voice agent can dispatch to.

YAML schema matches protoWorkstacean's `workspace/agents.yaml` so the same
file is portable across the fleet.

Minimal example (`config/agents.yaml`):

    agents:
      - name: ava
        url: http://ava-host:3008/a2a
        auth:
          scheme: apiKey
          credentialsEnv: AVA_API_KEY

Supported auth schemes:

  - `apiKey`   — sent as `X-API-Key` header, value from `credentialsEnv`
  - `bearer`   — sent as `Authorization: Bearer <value>`, value from `credentialsEnv`
  - none       — omit the `auth` block entirely

Missing credentials are logged as a warning; the entry is still usable for
unauthenticated endpoints.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

# POSIX-style env substitution for values that reference runtime hosts.
# Matches $VAR, ${VAR}, and ${VAR:-default}.
_ENV_RE = re.compile(r"\$\{?([A-Z_][A-Z0-9_]*)(?::-([^}]*))?\}?")


def _expand_env(value: str) -> str:
    def repl(m: re.Match) -> str:
        name, default = m.group(1), m.group(2) or ""
        return os.environ.get(name, default)
    return _ENV_RE.sub(repl, value)

logger = logging.getLogger(__name__)


@dataclass
class AgentEntry:
    name: str
    url: str
    auth_scheme: str | None = None
    credential: str | None = None
    headers: dict[str, str] = field(default_factory=dict)
    skills: list[dict[str, Any]] = field(default_factory=list)

    def auth_headers(self) -> dict[str, str]:
        """Return the HTTP headers required to authenticate one request."""
        h: dict[str, str] = dict(self.headers)
        if not self.auth_scheme or not self.credential:
            return h
        if self.auth_scheme == "apiKey":
            h["X-API-Key"] = self.credential
        elif self.auth_scheme == "bearer":
            h["Authorization"] = f"Bearer {self.credential}"
        else:
            logger.warning(
                f"[a2a] unknown auth scheme {self.auth_scheme!r} for agent {self.name}"
            )
        return h


def _parse_entry(raw: dict) -> AgentEntry | None:
    name = raw.get("name")
    url = raw.get("url")
    if not name or not url:
        logger.warning(f"[a2a] skipping entry missing name/url: {raw!r}")
        return None
    # Expand env references in URL so docker-compose can swap hosts
    # without editing the YAML.
    url = _expand_env(str(url))

    # Two accepted shapes for auth: nested `auth:` block (preferred) or
    # legacy shorthand `apiKeyEnv:`.
    auth_scheme: str | None = None
    cred: str | None = None
    auth = raw.get("auth")
    if isinstance(auth, dict):
        auth_scheme = auth.get("scheme")
        env_name = auth.get("credentialsEnv")
        if env_name:
            cred = os.environ.get(env_name)
            if not cred:
                logger.warning(
                    f"[a2a] agent {name}: auth env {env_name!r} unset "
                    "(requests will be unauthenticated)"
                )
    elif raw.get("apiKeyEnv"):
        env_name = raw["apiKeyEnv"]
        auth_scheme = "apiKey"
        cred = os.environ.get(env_name)
        if not cred:
            logger.warning(
                f"[a2a] agent {name}: apiKeyEnv {env_name!r} unset "
                "(requests will be unauthenticated)"
            )

    return AgentEntry(
        name=name,
        url=url,
        auth_scheme=auth_scheme,
        credential=cred,
        headers=dict(raw.get("headers", {})),
        skills=list(raw.get("skills", [])),
    )


class AgentRegistry:
    """Load and index agent entries from a YAML file."""

    def __init__(self, path: str | Path | None = None):
        self._path = Path(path) if path else None
        self._entries: dict[str, AgentEntry] = {}
        if self._path and self._path.exists():
            self._load()

    def _load(self) -> None:
        try:
            data = yaml.safe_load(self._path.read_text()) or {}
        except Exception as e:
            logger.error(f"[a2a] failed to read {self._path}: {e}")
            return
        for raw in data.get("agents", []) or []:
            entry = _parse_entry(raw)
            if entry:
                self._entries[entry.name] = entry
        logger.info(
            f"[a2a] registry loaded {len(self._entries)} agents: "
            f"{list(self._entries.keys())}"
        )

    def get(self, name: str) -> AgentEntry | None:
        return self._entries.get(name)

    def names(self) -> list[str]:
        return list(self._entries.keys())

    def __bool__(self) -> bool:
        return bool(self._entries)
