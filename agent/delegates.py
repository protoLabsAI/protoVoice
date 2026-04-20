"""Unified delegate registry.

The agent has ONE tool — `delegate_to(target, query)` — for handing off
heavy questions to:

  - **A2A agents** in the protoLabs fleet (ava, quinn, etc.)
  - **OpenAI-compatible LLM endpoints** (gateway / cloud / self-hosted)

Each delegate has a name + human description; the LLM picks based on
the descriptions baked into the tool's schema. New delegates are added
by editing `config/delegates.yaml` — no code change needed.

Schema (config/delegates.yaml):

    delegates:
      - name: ava
        description: "Chief of staff — sitreps, planning, fleet delegation."
        type: a2a
        url: ${AVA_URL:-http://ava:3008/a2a}
        auth: { scheme: apiKey, credentialsEnv: AVA_API_KEY }

      - name: opus
        description: "Heavy reasoning model — analysis, summarization, depth."
        type: openai
        url: http://gateway:4000/v1
        model: claude-opus-4-6
        api_key_env: LITELLM_MASTER_KEY
        system_prompt: "Answer thoroughly but concisely (2-4 sentences)."
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import yaml

from a2a.client import dispatch_message as _a2a_dispatch

logger = logging.getLogger(__name__)


# POSIX env substitution shared with the old AgentRegistry semantics.
_ENV_RE = re.compile(r"\$\{?([A-Z_][A-Z0-9_]*)(?::-([^}]*))?\}?")


def _expand_env(value: str) -> str:
    def repl(m: re.Match) -> str:
        name, default = m.group(1), m.group(2) or ""
        return os.environ.get(name, default)
    return _ENV_RE.sub(repl, value)


@dataclass
class Delegate:
    """A single dispatch target. Either an A2A agent or an OpenAI-compat
    LLM endpoint, switched on `type`."""
    name: str
    description: str
    type: str  # "a2a" | "openai"

    # Common
    url: str = ""

    # type=a2a
    auth_scheme: str | None = None
    a2a_credential: str | None = None
    a2a_headers: dict[str, str] = field(default_factory=dict)

    # type=openai
    model: str | None = None
    openai_api_key: str | None = None
    system_prompt: str | None = None
    max_tokens: int = 400
    temperature: float = 0.4

    def auth_headers(self) -> dict[str, str]:
        """A2A auth header dict. Empty for openai delegates."""
        h: dict[str, str] = dict(self.a2a_headers)
        if self.type != "a2a" or not self.auth_scheme or not self.a2a_credential:
            return h
        if self.auth_scheme == "apiKey":
            h["X-API-Key"] = self.a2a_credential
        elif self.auth_scheme == "bearer":
            h["Authorization"] = f"Bearer {self.a2a_credential}"
        else:
            logger.warning(
                f"[delegates] unknown auth scheme {self.auth_scheme!r} for {self.name}"
            )
        return h


def _parse_entry(raw: dict) -> Delegate | None:
    name = raw.get("name")
    dtype = (raw.get("type") or "").lower()
    desc = (raw.get("description") or "").strip()
    if not name or not dtype or not desc:
        logger.warning(f"[delegates] skipping entry missing name/type/description: {raw!r}")
        return None
    if dtype not in ("a2a", "openai"):
        logger.warning(f"[delegates] {name}: unknown type {dtype!r}; skipping")
        return None

    url = _expand_env(str(raw.get("url", "")))
    if not url:
        logger.warning(f"[delegates] {name}: url required; skipping")
        return None

    common = dict(name=name, description=desc, type=dtype, url=url)

    if dtype == "a2a":
        auth = raw.get("auth") or {}
        scheme = auth.get("scheme")
        cred_env = auth.get("credentialsEnv")
        cred = os.environ.get(cred_env) if cred_env else None
        if cred_env and not cred:
            logger.warning(
                f"[delegates] {name}: auth env {cred_env!r} unset (unauthenticated)"
            )
        return Delegate(
            **common,
            auth_scheme=scheme,
            a2a_credential=cred,
            a2a_headers=dict(raw.get("headers", {})),
        )

    # type=openai
    model = raw.get("model")
    if not model:
        logger.warning(f"[delegates] {name}: openai delegate requires model; skipping")
        return None
    key_env = raw.get("api_key_env")
    api_key = os.environ.get(key_env) if key_env else None
    if key_env and not api_key:
        logger.warning(
            f"[delegates] {name}: api_key_env {key_env!r} unset (sending unauthenticated)"
        )
    return Delegate(
        **common,
        model=model,
        openai_api_key=api_key or "not-needed",
        system_prompt=raw.get("system_prompt"),
        max_tokens=int(raw.get("max_tokens", 400)),
        temperature=float(raw.get("temperature", 0.4)),
    )


class DelegateRegistry:
    """Loads + indexes delegates from YAML."""

    def __init__(self, path: str | Path | None = None):
        self._path = Path(path) if path else None
        self._items: dict[str, Delegate] = {}
        if self._path and self._path.exists():
            self._load()

    def _load(self) -> None:
        try:
            data = yaml.safe_load(self._path.read_text()) or {}
        except Exception as e:
            logger.error(f"[delegates] failed to read {self._path}: {e}")
            return
        for raw in data.get("delegates", []) or []:
            entry = _parse_entry(raw)
            if entry:
                self._items[entry.name] = entry
        logger.info(
            f"[delegates] loaded {len(self._items)}: "
            f"{[(d.name, d.type) for d in self._items.values()]}"
        )

    def get(self, name: str) -> Delegate | None:
        return self._items.get(name)

    def names(self) -> list[str]:
        return list(self._items.keys())

    def all(self) -> list[Delegate]:
        return list(self._items.values())

    def __bool__(self) -> bool:
        return bool(self._items)


# ---------------------------------------------------------------------------
# Dispatch — single async fn that handles both delegate types.
# ---------------------------------------------------------------------------

class DelegateError(RuntimeError):
    """Raised on any dispatch failure. Caller speaks the message back to the user."""


async def dispatch(delegate: Delegate, query: str, *, timeout: float = 60.0) -> str:
    if delegate.type == "a2a":
        return await _dispatch_a2a(delegate, query, timeout=timeout)
    if delegate.type == "openai":
        return await _dispatch_openai(delegate, query, timeout=timeout)
    raise DelegateError(f"unknown delegate type {delegate.type!r}")


async def _dispatch_a2a(delegate: Delegate, query: str, *, timeout: float) -> str:
    """Reuse the existing A2A wire client."""
    return await _a2a_dispatch(
        url=delegate.url,
        headers=delegate.auth_headers(),
        user_text=query,
        timeout=timeout,
    )


async def _dispatch_openai(delegate: Delegate, query: str, *, timeout: float) -> str:
    """One-shot non-streaming chat completion via plain httpx.

    We deliberately avoid the OpenAI SDK here — it adds `x-stainless-*`
    fingerprint headers + a `user-agent: AsyncOpenAI/…` string that some
    proxies (workstacean's WAF being the reason we found out) block. The
    endpoint contract is simple enough that raw httpx is cleaner.
    """
    sys_prompt = delegate.system_prompt or (
        "You are a research assistant. Answer thoroughly but concisely "
        "(2-4 sentences). Plain text only — no markdown, no lists. "
        "The answer will be spoken aloud verbatim."
    )
    headers = {"Content-Type": "application/json"}
    if delegate.openai_api_key and delegate.openai_api_key != "not-needed":
        # Both standard (Authorization: Bearer) and workstacean-style
        # (X-API-Key) are accepted by the servers we target today.
        # Bearer is the OpenAI contract; sticking with that.
        headers["Authorization"] = f"Bearer {delegate.openai_api_key}"
    payload = {
        "model": delegate.model,
        "messages": [
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": query},
        ],
        "max_tokens": delegate.max_tokens,
        "temperature": delegate.temperature,
        "stream": False,
        # vLLM-hosted Qwen3.5/3.6 models emit reasoning into a separate
        # field unless this is off. Harmless for gateways that ignore it.
        "chat_template_kwargs": {"enable_thinking": False},
    }
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.post(
                f"{delegate.url.rstrip('/')}/chat/completions",
                headers=headers,
                json=payload,
            )
    except (httpx.ConnectError, httpx.ConnectTimeout, OSError) as e:
        raise DelegateError(f"{delegate.name} unreachable: {e}") from e
    except Exception as e:
        raise DelegateError(f"{delegate.name}: {e}") from e

    if r.status_code != 200:
        raise DelegateError(
            f"{delegate.name}: HTTP {r.status_code} — {r.text[:200]}"
        )
    try:
        body = r.json()
        return (body["choices"][0]["message"]["content"] or "").strip()
    except Exception as e:
        raise DelegateError(f"{delegate.name}: malformed response ({e})") from e
