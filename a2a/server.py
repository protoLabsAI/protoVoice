"""A2A inbound server — JSON-RPC 2.0 `message/send` handler + agent card.

Decoupled from the voice pipeline: inbound A2A requests run a standalone
text-only pass through the same LLM service with the same tool registry,
and return a synchronous text artifact. No WebRTC involved.

Also hosts `/a2a/callback` — the URL we register on outbound async
dispatches (M4 `a2a_dispatch`) so other agents can push results back to
us. When a callback lands, we route the result through the
DeliveryController so it speaks in any active voice session (at
next-silence by default).
"""

from __future__ import annotations

import logging
import os
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from agent.delivery import DeliveryController, DeliveryPolicy, Priority

logger = logging.getLogger(__name__)

A2A_AUTH_TOKEN = os.environ.get("A2A_AUTH_TOKEN", "")  # shared secret for inbound auth
AGENT_NAME = os.environ.get("AGENT_NAME", "protovoice")
AGENT_VERSION = os.environ.get("AGENT_VERSION", "0.1.0")


def _extract_user_text(params: dict) -> str:
    """Parse the `message.parts` list, return concatenated text parts."""
    parts = params.get("message", {}).get("parts", []) or []
    texts: list[str] = []
    for p in parts:
        kind = p.get("kind") or p.get("type")
        if kind == "text" and p.get("text"):
            texts.append(p["text"])
    return "\n".join(texts).strip()


def build_agent_card(host: str, *, skills: list[dict] | None = None) -> dict:
    """The `.well-known/agent-card.json` body. protoWorkstacean's loader
    refreshes this on each restart."""
    schemes: dict = {"apiKey": {"type": "apiKey", "in": "header", "name": "X-API-Key"}}
    if A2A_AUTH_TOKEN:
        schemes["bearer"] = {"type": "http", "scheme": "bearer"}
    return {
        "name": AGENT_NAME,
        "description": (
            "protoVoice — full-duplex voice agent with web search, calculator, "
            "datetime, and A2A dispatch. Inbound A2A returns text; voice "
            "is browser-only."
        ),
        "url": f"http://{host}/a2a",
        "version": AGENT_VERSION,
        "provider": {
            "organization": "protoLabsAI",
            "url": "https://github.com/protoLabsAI",
        },
        "capabilities": {
            "streaming": False,
            "pushNotifications": False,
            "stateTransitionHistory": False,
        },
        "defaultInputModes": ["text/plain"],
        "defaultOutputModes": ["text/markdown"],
        "skills": skills or [
            {
                "id": "chat",
                "name": "Chat",
                "description": "Free-form conversation with web search, calculator, and A2A dispatch.",
                "tags": ["voice", "chat"],
                "examples": ["what's the weather in Tokyo?", "what time is it?"],
            },
        ],
        "securitySchemes": schemes,
        "security": [{"apiKey": []}],
    }


def _auth_ok(request: Request) -> bool:
    """Accept either X-API-Key or Authorization: Bearer with the shared
    secret. If no secret is configured, accept anonymous."""
    if not A2A_AUTH_TOKEN:
        return True
    if request.headers.get("X-API-Key", "") == A2A_AUTH_TOKEN:
        return True
    bearer = request.headers.get("Authorization", "")
    if bearer.startswith("Bearer ") and bearer[7:] == A2A_AUTH_TOKEN:
        return True
    return False


def _jsonrpc_error(rpc_id: Any, code: int, message: str, status: int = 400) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"jsonrpc": "2.0", "id": rpc_id, "error": {"code": code, "message": message}},
    )


TextAgent = Callable[[str, str], Awaitable[str]]
DeliveryProvider = Callable[[], DeliveryController | None]
SkillSlugProvider = Callable[[], str]


def register_a2a_routes(
    app: FastAPI,
    *,
    text_agent: TextAgent,
    delivery_provider: DeliveryProvider | None = None,
    skill_slug_provider: SkillSlugProvider | None = None,
) -> None:
    """Mount /a2a, /a2a/callback, and /.well-known/agent-card.json.

    `text_agent(message, session_id)` runs a one-shot inbound turn.

    `delivery_provider` returns the currently-active session's
    DeliveryController or None. Sessions come and go, so we resolve at
    callback time instead of capturing a stale reference.

    `skill_slug_provider` returns the current active skill slug so we
    can stash orphan deliveries under the right key when there's no
    live session. Falls back to 'default' if not supplied.
    """

    @app.get("/.well-known/agent.json", include_in_schema=False)
    @app.get("/.well-known/agent-card.json", include_in_schema=False)
    async def _agent_card(request: Request):
        host = request.headers.get("host", f"{AGENT_NAME}:7866")
        return JSONResponse(
            content=build_agent_card(host),
            headers={"Cache-Control": "public, max-age=60"},
        )

    @app.post("/a2a")
    async def _a2a(request: Request):
        if not _auth_ok(request):
            return _jsonrpc_error(None, -32600, "Unauthorized", status=401)
        try:
            body = await request.json()
        except Exception:
            return _jsonrpc_error(None, -32700, "Parse error", status=400)
        rpc_id = body.get("id")
        method = body.get("method")

        if method != "message/send":
            return _jsonrpc_error(
                rpc_id, -32601, f"Unknown method: {method!r}", status=400
            )

        params = body.get("params", {}) or {}
        user_text = _extract_user_text(params)
        if not user_text:
            return _jsonrpc_error(rpc_id, -32602, "No text part in message.parts")

        context_id = params.get("contextId") or str(uuid.uuid4())
        session_id = f"a2a:{context_id}"
        logger.info(f'[a2a/in] {context_id[:8]}… "{user_text[:80]}"')
        try:
            reply = await text_agent(user_text, session_id)
        except Exception as e:
            logger.exception("[a2a/in] text_agent raised")
            return _jsonrpc_error(rpc_id, -32000, f"agent error: {e}", status=500)

        return JSONResponse(
            content={
                "jsonrpc": "2.0",
                "id": rpc_id,
                "result": {
                    "id": str(uuid.uuid4()),
                    "contextId": context_id,
                    "status": {"state": "completed"},
                    "artifacts": [
                        {
                            "artifactId": str(uuid.uuid4()),
                            "parts": [{"kind": "text", "text": reply}],
                        }
                    ],
                },
            }
        )

    @app.post("/a2a/callback")
    async def _a2a_callback(request: Request):
        """Receive push-notification results from agents we dispatched to.

        Expected shape matches what our own outbound client would receive —
        a JSON body with either a `result.artifacts[].parts[].text` or a
        top-level `text`. We're permissive so this works across fleets.
        """
        try:
            body = await request.json()
        except Exception:
            return JSONResponse(status_code=400, content={"error": "invalid json"})

        # Be permissive about the shape; log what we got.
        text = _extract_text_from_any(body)
        caller = body.get("from") or body.get("agent") or "unknown"
        logger.info(f"[a2a/callback] from={caller} text_len={len(text)}")

        if not text:
            return {"ok": True, "delivered": False, "reason": "no text"}
        delivery = delivery_provider() if delivery_provider else None
        if delivery is None:
            # No live session — stash for the next connect instead of
            # dropping. Replay via drain_stashed_deliveries on_client_connected.
            from agent.session_store import stash_delivery
            slug = skill_slug_provider() if skill_slug_provider else "default"
            stash_delivery(slug, {
                "phrase": f"{caller} says — {text}",
                "policy": "next_silence",
                "priority": "time_sensitive",
                "keywords": [],
            })
            logger.info(f"[a2a/callback] no active session — stashed under {slug!r}")
            return {"ok": True, "delivered": False, "stashed": True}

        # Attribution is handled by DeliveryController — no need to wrap
        # with "heads up" since "{caller} says —" does that natively.
        await delivery.deliver(text, priority=Priority.TIME_SENSITIVE, source=caller)
        return {"ok": True, "delivered": True}


def _extract_text_from_any(body: dict) -> str:
    """Hunt for text in a few common shapes (A2A task result, plain
    message, or nested envelope)."""
    if isinstance(body.get("text"), str):
        return body["text"]
    result = body.get("result") or body
    artifacts = result.get("artifacts") or []
    for art in artifacts:
        for part in art.get("parts") or []:
            kind = part.get("kind") or part.get("type")
            if kind == "text" and part.get("text"):
                return part["text"]
    msg = body.get("message") or (result.get("message") if isinstance(result, dict) else None)
    if isinstance(msg, dict):
        for part in msg.get("parts") or []:
            kind = part.get("kind") or part.get("type")
            if kind == "text" and part.get("text"):
                return part["text"]
    return ""
