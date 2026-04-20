"""A2A outbound client — JSON-RPC 2.0 `message/send` dispatch.

Synchronous over the wire (single HTTP request, awaits response). The
caller is expected to wrap us in an async context — pipecat tools spawn
us inside a `cancel_on_interruption=False` handler so the voice pipeline
stays responsive while we wait.

`dispatch_message` takes raw url + headers. The DelegateRegistry in
`agent/delegates.py` is the canonical caller.
"""

from __future__ import annotations

import logging
import uuid

import httpx

logger = logging.getLogger(__name__)


class A2ADispatchError(RuntimeError):
    """Raised when the remote agent returns an error or the response is
    malformed. Caught by the tool wrapper so it becomes a spoken error."""


async def dispatch_message(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    user_text: str = "",
    context_id: str | None = None,
    timeout: float = 60.0,
) -> str:
    """POST `message/send` to `url`, return the assistant text from the
    first artifact's first text part.

    Raises A2ADispatchError on non-2xx, JSON-RPC error, or missing text.
    """
    if not user_text:
        raise A2ADispatchError("empty user_text")
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
    req_headers = {"Content-Type": "application/json"}
    if headers:
        req_headers.update(headers)

    logger.info(f"[a2a] dispatch → {url}")
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(url, json=payload, headers=req_headers)

    if resp.status_code != 200:
        raise A2ADispatchError(
            f"HTTP {resp.status_code} from {url} — {resp.text[:200]}"
        )

    try:
        body = resp.json()
    except Exception as e:
        raise A2ADispatchError(f"non-JSON response from {url} ({e})") from e

    if "error" in body:
        raise A2ADispatchError(f"{body['error']}")

    result = body.get("result") or {}
    artifacts = result.get("artifacts") or []
    for art in artifacts:
        for part in art.get("parts") or []:
            kind = part.get("kind") or part.get("type")
            if kind == "text" and part.get("text"):
                return part["text"]
    raise A2ADispatchError(f"response from {url} had no text artifact")
