"""Tool registry for the voice agent.

Currently registered:

  calculator     — safe AST eval of arithmetic expressions (sync)
  get_datetime   — current date/time in a configurable timezone (sync)
  web_search     — DuckDuckGo via `ddgs`, top-5 snippets (sync)
  delegate_to    — single dispatch tool covering A2A agents AND OpenAI-
                   compat endpoints. Targets are configured in
                   config/delegates.yaml. Replaces the old deep_research
                   and a2a_dispatch tools.
  slow_research  — long-running investigation, async delivery (M3)

Sync tools block the LLM loop until they return — filler + progress
fires via the on_function_calls_started hook in app.py. Async tools
(`cancel_on_interruption=False`) return control immediately and deliver
via the DeliveryController.
"""

from __future__ import annotations

import ast
import asyncio
import logging
import operator
import os
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.adapters.schemas.tools_schema import ToolsSchema
from pipecat.services.llm_service import FunctionCallParams, LLMService

from .delegates import DelegateError, DelegateRegistry, dispatch as delegate_dispatch
from .delivery import DeliveryController
from .filler import Latency

logger = logging.getLogger(__name__)

# Tunables — let us stress-test filler + progress + delivery independently.
SLOW_RESEARCH_SECS = float(os.environ.get("SLOW_RESEARCH_SECS", "20"))
DEFAULT_TZ = os.environ.get("TZ", "America/New_York")

# Tool names registered with cancel_on_interruption=False — async path.
ASYNC_TOOL_NAMES: frozenset[str] = frozenset({"slow_research"})

# Expected-latency hint per tool.
TOOL_LATENCY: dict[str, Latency] = {
    "calculator":     Latency.FAST,
    "get_datetime":   Latency.FAST,
    "web_search":     Latency.MEDIUM,
    "delegate_to":    Latency.MEDIUM,
    "slow_research":  Latency.SLOW,
}


def latency_for(tool_name: str) -> Latency:
    return TOOL_LATENCY.get(tool_name, Latency.MEDIUM)


# ---------------------------------------------------------------------------
# Schemas — sync tools have static schemas; delegate_to is built per-registry
# ---------------------------------------------------------------------------

CALCULATOR_SCHEMA = FunctionSchema(
    name="calculator",
    description=(
        "Evaluate a basic arithmetic expression. Use ONLY for "
        "calculations the user has explicitly asked you to do."
    ),
    properties={
        "expression": {
            "type": "string",
            "description": "Arithmetic expression, e.g. '15 * 1.2 + 3'",
        },
    },
    required=["expression"],
)

DATETIME_SCHEMA = FunctionSchema(
    name="get_datetime",
    description="Return the current date and time.",
    properties={},
    required=[],
)

WEB_SEARCH_SCHEMA = FunctionSchema(
    name="web_search",
    description=(
        "Search the web for current information. Use when the user asks "
        "about news, recent events, or facts you're not confident about. "
        "Returns short snippets from the top results."
    ),
    properties={
        "query": {"type": "string", "description": "Search query"},
    },
    required=["query"],
)

SLOW_RESEARCH_SCHEMA = FunctionSchema(
    name="slow_research",
    description=(
        "Kick off a long-running investigation (30s+). Use when the user "
        "doesn't need an immediate answer — they can keep chatting while "
        "the agent will speak the result when it's ready."
    ),
    properties={
        "query": {"type": "string", "description": "The question to investigate"},
    },
    required=["query"],
)


def _delegate_to_schema(registry: DelegateRegistry) -> FunctionSchema:
    """Built dynamically — `target` is enum-restricted to known delegates,
    and the description enumerates what each delegate is good for so the
    LLM can pick correctly."""
    items = registry.all()
    target_lines = "\n".join(f"  - {d.name}: {d.description}" for d in items)
    return FunctionSchema(
        name="delegate_to",
        description=(
            "Hand off a question to a specialized backend — another agent "
            "in the fleet, or a heavier reasoning model. Use when the "
            "user's question genuinely requires depth, current info, or "
            "another specialist's expertise.\n\n"
            f"Available targets:\n{target_lines}\n\n"
            "Pass `target` (one of the names above) and `query` (the "
            "question, phrased as you'd ask a person)."
        ),
        properties={
            "target": {
                "type": "string",
                "enum": [d.name for d in items],
                "description": "Which delegate to ask",
            },
            "query": {
                "type": "string",
                "description": "The question to ask",
            },
        },
        required=["target", "query"],
    )


# ---------------------------------------------------------------------------
# Sync tool implementations
# ---------------------------------------------------------------------------

_ALLOWED_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
    ast.Mod: operator.mod,
    ast.FloorDiv: operator.floordiv,
    ast.USub: operator.neg,
}


def _safe_eval(node: ast.AST) -> float:
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp):
        op = _ALLOWED_OPS.get(type(node.op))
        if op is None:
            raise ValueError(f"Unsupported operator: {type(node.op).__name__}")
        return op(_safe_eval(node.left), _safe_eval(node.right))
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        return -_safe_eval(node.operand)
    raise ValueError(f"Unsupported expression: {ast.dump(node)}")


async def calculator_handler(params: FunctionCallParams) -> None:
    expr = params.arguments.get("expression", "")
    try:
        tree = ast.parse(expr.strip(), mode="eval")
        val = _safe_eval(tree.body)
        if isinstance(val, float) and val.is_integer():
            val = int(val)
        result = f"{expr} equals {val}."
    except Exception as e:
        result = f"I couldn't calculate that: {e}."
    await params.result_callback(result)


async def datetime_handler(params: FunctionCallParams) -> None:
    try:
        tz = ZoneInfo(DEFAULT_TZ)
    except (ZoneInfoNotFoundError, Exception):
        tz = ZoneInfo("UTC")
    result = datetime.now(tz=tz).strftime(
        "It's %A, %B %d, %Y at %I:%M %p %Z."
    )
    await params.result_callback(result)


async def web_search_handler(params: FunctionCallParams) -> None:
    query = params.arguments.get("query", "").strip()
    if not query:
        await params.result_callback("No search query provided.")
        return

    def _search() -> str:
        from ddgs import DDGS
        with DDGS() as d:
            results = list(d.text(query, max_results=5))
        if not results:
            return "No results found."
        lines = []
        for r in results:
            title = r.get("title") or ""
            body = r.get("body") or ""
            lines.append(f"{title}: {body}")
        return " ".join(lines)

    try:
        text = await asyncio.to_thread(_search)
    except Exception as e:
        logger.warning(f"[web_search] failed: {e}")
        text = "The search failed — I couldn't reach DuckDuckGo."
    await params.result_callback(text[:2000])  # keep context manageable


def _delegate_to_handler(
    registry: DelegateRegistry,
    *,
    delivery: "DeliveryController | None" = None,
    push_notification_url: str | None = None,
    push_notification_token: str | None = None,
):
    async def _handler(params: FunctionCallParams) -> None:
        target = (params.arguments.get("target") or "").strip()
        query = (params.arguments.get("query") or "").strip()
        if not target or not query:
            await params.result_callback(
                "I need both a target and a question to delegate."
            )
            return
        delegate = registry.get(target)
        if not delegate:
            available = ", ".join(registry.names()) or "(none)"
            await params.result_callback(
                f"I don't know a delegate named '{target}'. Available: {available}."
            )
            return
        logger.info(f"[delegate_to] target={target} type={delegate.type} query={query!r}")

        # Stream progress narration back through the voice pipeline when
        # available. Only wired for A2A delegates (OpenAI delegates don't
        # stream status updates the same way).
        progress_cb = None
        if delivery is not None and delegate.type == "a2a":
            async def _progress(msg: str) -> None:
                await delivery.speak_now(msg, source=target)
            progress_cb = _progress

        try:
            result = await delegate_dispatch(
                delegate, query,
                progress_callback=progress_cb,
                push_notification_url=push_notification_url,
                push_notification_token=push_notification_token,
            )
            await params.result_callback(result)
        except DelegateError as e:
            await params.result_callback(f"Couldn't reach {target}: {e}")
        except Exception as e:
            logger.exception(f"[delegate_to] unexpected error: {e}")
            await params.result_callback(f"Delegation to {target} errored: {e}")

    return _handler


# ---------------------------------------------------------------------------
# Async tool — kept for M3 validation
# ---------------------------------------------------------------------------

def _slow_research_handler(_controller: DeliveryController):
    """Async tool — the LLM acknowledges via its inline preamble (M10
    TOOL USE prompt block). DO NOT call result_callback in the foreground;
    pipecat would treat that as the finished result and the LLM would
    fabricate follow-ups about the topic."""
    async def _handler(params: FunctionCallParams) -> None:
        query = params.arguments.get("query", "")
        logger.info(f"[slow_research] starting: {query!r} (sleep {SLOW_RESEARCH_SECS}s)")

        async def _background() -> None:
            await asyncio.sleep(SLOW_RESEARCH_SECS)
            result = (
                f"Investigation into '{query}' complete. "
                "This is a synthetic placeholder — real long-form research "
                "lands when this tool gets a real backend."
            )
            try:
                await params.result_callback(result)
                logger.info(f"[slow_research] result_callback fired ({len(result)} chars)")
            except Exception as e:
                logger.exception(f"[slow_research] result_callback failed: {e}")

        asyncio.create_task(_background())

    return _handler


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

async def run_text_tool(
    name: str,
    arguments: dict,
    *,
    delegates: DelegateRegistry | None = None,
    push_notification_url: str | None = None,
    push_notification_token: str | None = None,
) -> str:
    """Invoke a tool handler in text mode (no pipecat FunctionCallParams).

    Returns the string result the handler would have passed to
    result_callback. Used by the inbound A2A ReAct loop (F6) so external
    agents can drive the same tool registry the voice path uses.

    Sync tools supported: calculator, get_datetime, web_search, delegate_to.
    Async tool (slow_research) is NOT exposed here — it requires a live
    DeliveryController + voice session to narrate back on completion.
    """
    class _P:  # duck-typed FunctionCallParams stand-in
        def __init__(self, args: dict) -> None:
            self.arguments = args
            self._out: str = ""
        async def result_callback(self, text: Any) -> None:
            self._out = "" if text is None else str(text)
    params = _P(arguments)
    if name == "calculator":
        await calculator_handler(params)
    elif name == "get_datetime":
        await datetime_handler(params)
    elif name == "web_search":
        await web_search_handler(params)
    elif name == "delegate_to" and delegates is not None:
        handler = _delegate_to_handler(
            delegates,
            delivery=None,                      # no voice session; skip progress
            push_notification_url=push_notification_url,
            push_notification_token=push_notification_token,
        )
        await handler(params)
    else:
        return f"(unknown or unavailable tool: {name})"
    return params._out


def build_text_tool_schemas(delegates: DelegateRegistry | None = None) -> list[dict]:
    """Build the OpenAI tools-parameter list for the text-mode ReAct
    loop. Mirrors the schemas register_tools registers with pipecat,
    minus slow_research (async — see run_text_tool)."""
    schemas = [CALCULATOR_SCHEMA, DATETIME_SCHEMA, WEB_SEARCH_SCHEMA]
    if delegates is not None and delegates.names():
        schemas.append(_delegate_to_schema(delegates))
    return [{"type": "function", "function": s.to_default_dict()} for s in schemas]


def register_tools(
    llm: LLMService,
    *,
    on_finish=None,
    delivery: DeliveryController | None = None,
    delegates: DelegateRegistry | None = None,
    push_notification_url: str | None = None,
    push_notification_token: str | None = None,
) -> ToolsSchema:
    """Attach handlers + return the schema for the LLMContext.

    `delegates` — when non-empty, registers `delegate_to` with a schema
    that enumerates the available targets. When empty/None, the tool is
    NOT registered (the LLM doesn't see it, so it can't try to call it).

    `push_notification_url` / `push_notification_token` — forwarded to
    A2A delegate dispatches so remote agents can call back via the
    /a2a/push endpoint (see D16/D17).
    """

    def _wrap_sync(handler):
        async def _wrapped(params: FunctionCallParams) -> None:
            try:
                await handler(params)
            finally:
                if on_finish is not None:
                    try:
                        on_finish()
                    except Exception as e:
                        logger.warning(f"on_finish hook raised: {e}")
        _wrapped.__name__ = handler.__name__
        return _wrapped

    llm.register_function("calculator", _wrap_sync(calculator_handler), cancel_on_interruption=True)
    llm.register_function("get_datetime", _wrap_sync(datetime_handler), cancel_on_interruption=True)
    llm.register_function("web_search", _wrap_sync(web_search_handler), cancel_on_interruption=True)

    standard = [CALCULATOR_SCHEMA, DATETIME_SCHEMA, WEB_SEARCH_SCHEMA]

    if delegates and delegates.names():
        llm.register_function(
            "delegate_to",
            _wrap_sync(_delegate_to_handler(
                delegates,
                delivery=delivery,
                push_notification_url=push_notification_url,
                push_notification_token=push_notification_token,
            )),
            cancel_on_interruption=True,
        )
        standard.append(_delegate_to_schema(delegates))

    if delivery is not None:
        llm.register_function(
            "slow_research",
            _slow_research_handler(delivery),
            cancel_on_interruption=False,
        )
        standard.append(SLOW_RESEARCH_SCHEMA)

    return ToolsSchema(standard_tools=standard)
