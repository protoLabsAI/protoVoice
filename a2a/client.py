"""A2A outbound client — JSON-RPC 2.0 `message/send` dispatch.

Synchronous (single HTTP request, waits for response). For truly long-
running delegations we'll want push-notification callbacks — that's M6.
For M4, the caller wraps us in an async tool so the voice pipeline keeps
running during the call.
"""

from __future__ import annotations

import logging
import uuid

import httpx

from .registry import AgentEntry

logger = logging.getLogger(__name__)


class A2ADispatchError(RuntimeError):
    """Raised when the remote agent returns an error or the response is
    malformed. Caught by the tool wrapper so it becomes a spoken error."""


async def dispatch_message(
    agent: AgentEntry,
    user_text: str,
    *,
    context_id: str | None = None,
    timeout: float = 60.0,
) -> str:
    """Send `user_text` to `agent` via A2A `message/send`, return the
    assistant text from the first artifact's first text part.

    Raises A2ADispatchError on non-2xx, JSON-RPC error, or missing text.
    """
    rpc_id = str(uuid.uuid4())
    context_id = context_id or str(uuid.uuid4())
    payload = {
        "jsonrpc": "2.0",
        "id": rpc_id,
        "method": "message/send",
        "params": {
            "contextId": context_id,
            "message": {
                "role": "user",
                "parts": [{"kind": "text", "text": user_text}],
            },
        },
    }
    headers = {"Content-Type": "application/json"}
    headers.update(agent.auth_headers())

    logger.info(f"[a2a] dispatch → {agent.name} @ {agent.url}")
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(agent.url, json=payload, headers=headers)

    if resp.status_code != 200:
        raise A2ADispatchError(
            f"{agent.name}: HTTP {resp.status_code} — {resp.text[:200]}"
        )

    try:
        body = resp.json()
    except Exception as e:
        raise A2ADispatchError(f"{agent.name}: non-JSON response ({e})") from e

    if "error" in body:
        raise A2ADispatchError(f"{agent.name}: {body['error']}")

    result = body.get("result") or {}
    artifacts = result.get("artifacts") or []
    for art in artifacts:
        for part in art.get("parts") or []:
            kind = part.get("kind") or part.get("type")
            if kind == "text" and part.get("text"):
                return part["text"]
    raise A2ADispatchError(f"{agent.name}: response had no text artifact")
