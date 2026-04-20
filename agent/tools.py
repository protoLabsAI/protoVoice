"""Tool registry for the voice agent.

Currently registered:

  calculator     — safe AST eval of arithmetic expressions (sync)
  get_datetime   — current date/time in a configurable timezone (sync)
  web_search     — DuckDuckGo via `ddgs`, top-5 snippets (sync)
  deep_research  — quick lookup; delegates to ava via A2A when configured,
                   otherwise a synthetic placeholder (sync)
  a2a_dispatch   — send a message to another protoLabs agent (sync)
  slow_research  — long-running investigation, async delivery (M3)

Sync tools block the LLM loop until they return — filler + progress fires
via the on_function_calls_started hook in app.py. Async tools
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

from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.adapters.schemas.tools_schema import ToolsSchema
from pipecat.services.llm_service import FunctionCallParams, LLMService

import httpx

from a2a.client import A2ADispatchError, dispatch_message
from a2a.registry import AgentRegistry

from .delivery import DeliveryController, DeliveryPolicy

logger = logging.getLogger(__name__)

# Tool names registered with cancel_on_interruption=False — async path.
# app.py reads this to suppress the progress-filler loop, which otherwise
# leaks forever because the on_finish hook only fires on sync tools.
ASYNC_TOOL_NAMES: frozenset[str] = frozenset({"slow_research"})

# Tunables — let us stress-test filler + progress + delivery independently.
FAKE_RESEARCH_SECS = float(os.environ.get("FAKE_RESEARCH_SECS", "4"))
SLOW_RESEARCH_SECS = float(os.environ.get("SLOW_RESEARCH_SECS", "20"))
DEFAULT_TZ = os.environ.get("TZ", "America/New_York")


# ---------------------------------------------------------------------------
# Schemas
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

DEEP_RESEARCH_SCHEMA = FunctionSchema(
    name="deep_research",
    description=(
        "Answer a question that requires research — looks it up through "
        "the orchestrator agent (which has its own tools and subagents). "
        "Returns quickly; speak the result as a normal answer."
    ),
    properties={
        "query": {"type": "string", "description": "The question or topic"},
    },
    required=["query"],
)

A2A_DISPATCH_SCHEMA = FunctionSchema(
    name="a2a_dispatch",
    description=(
        "Send a message to another agent in the protoLabs fleet and return "
        "their response. Use when a specific agent would be better suited "
        "than general research."
    ),
    properties={
        "agent": {
            "type": "string",
            "description": "Target agent name (e.g. 'ava')",
        },
        "message": {
            "type": "string",
            "description": "The message to send — phrase it as you would speak to the agent.",
        },
    },
    required=["agent", "message"],
)

SLOW_RESEARCH_SCHEMA = FunctionSchema(
    name="slow_research",
    description=(
        "Kick off a long-running investigation (30s+). Use when the user "
        "doesn't need an immediate answer — they can keep chatting while "
        "the answer is prepared, and the agent will speak the result when "
        "it's ready."
    ),
    properties={
        "query": {"type": "string", "description": "The question to investigate"},
    },
    required=["query"],
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


def _deep_research_handler(registry: AgentRegistry):
    """Deep research → delegate to ava via A2A if reachable.
    Falls back to a synthetic placeholder on any connection failure so the
    tool never surfaces a raw error to the LLM (which would break the turn).
    """

    async def _handler(params: FunctionCallParams) -> None:
        query = params.arguments.get("query", "").strip()
        ava = registry.get("ava")
        if ava:
            try:
                logger.info(f"[deep_research] delegating to ava: {query!r}")
                result = await dispatch_message(ava, query)
                await params.result_callback(result)
                return
            except (httpx.ConnectError, httpx.ConnectTimeout, OSError) as e:
                logger.warning(
                    f"[deep_research] ava unreachable ({type(e).__name__}: {e}); "
                    "degrading to synthetic"
                )
            except A2ADispatchError as e:
                logger.warning(f"[deep_research] ava dispatch error: {e}")
                await params.result_callback(
                    f"I tried to ask our orchestrator but got an error: {e}"
                )
                return
            except Exception as e:
                logger.exception(f"[deep_research] unexpected error: {e}")
                await params.result_callback(
                    f"Something went wrong asking our orchestrator: {e}"
                )
                return
        # Synthetic fallback — ava not configured or unreachable.
        logger.info(f"[deep_research] synthetic fallback: {query!r}")
        await asyncio.sleep(FAKE_RESEARCH_SECS)
        await params.result_callback(
            f"I couldn't reach our research orchestrator, so here's a "
            f"placeholder answer about '{query}' — set AVA_URL + AVA_API_KEY "
            f"for real research."
        )

    return _handler


def _a2a_dispatch_handler(registry: AgentRegistry):
    async def _handler(params: FunctionCallParams) -> None:
        name = (params.arguments.get("agent") or "").strip().lower()
        message = (params.arguments.get("message") or "").strip()
        if not name or not message:
            await params.result_callback(
                "I need both an agent name and a message to dispatch."
            )
            return
        agent = registry.get(name)
        if not agent:
            available = ", ".join(registry.names()) or "(none)"
            await params.result_callback(
                f"I don't know an agent named '{name}'. Available: {available}."
            )
            return
        try:
            result = await dispatch_message(agent, message)
            await params.result_callback(result)
        except (httpx.ConnectError, httpx.ConnectTimeout, OSError) as e:
            await params.result_callback(
                f"I couldn't reach {name} ({type(e).__name__}). "
                "The agent might be offline or the URL is wrong."
            )
        except A2ADispatchError as e:
            await params.result_callback(f"Dispatch to {name} failed: {e}")
        except Exception as e:
            logger.exception(f"[a2a_dispatch] unexpected error: {e}")
            await params.result_callback(f"Dispatch to {name} errored: {e}")

    return _handler


# ---------------------------------------------------------------------------
# Async tool — kept for M3 validation
# ---------------------------------------------------------------------------

def _slow_research_handler(controller: DeliveryController):
    async def _handler(params: FunctionCallParams) -> None:
        query = params.arguments.get("query", "")
        logger.info(f"[slow_research] starting: {query!r} (sleep {SLOW_RESEARCH_SECS}s)")
        await params.result_callback(
            f"Sure — I'll look into {query} and let you know. You can keep talking."
        )

        async def _background() -> None:
            await asyncio.sleep(SLOW_RESEARCH_SECS)
            phrase = (
                f"Okay, I found what you asked about {query}. "
                f"This is still a synthetic placeholder — real long-form research lands later."
            )
            keywords = tuple(w for w in query.split() if len(w) > 3)
            await controller.deliver(
                phrase,
                policy=DeliveryPolicy.NEXT_SILENCE,
                keywords=keywords,
            )
            logger.info(f"[slow_research] delivered ({len(phrase)} chars)")

        asyncio.create_task(_background())

    return _handler


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def register_tools(
    llm: LLMService,
    *,
    on_finish=None,
    delivery: DeliveryController | None = None,
    registry: AgentRegistry | None = None,
) -> ToolsSchema:
    """Attach handlers + return the schema for the LLMContext."""

    registry = registry or AgentRegistry()  # empty is fine

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
    llm.register_function(
        "deep_research",
        _wrap_sync(_deep_research_handler(registry)),
        cancel_on_interruption=True,
    )
    llm.register_function(
        "a2a_dispatch",
        _wrap_sync(_a2a_dispatch_handler(registry)),
        cancel_on_interruption=True,
    )

    standard = [
        CALCULATOR_SCHEMA,
        DATETIME_SCHEMA,
        WEB_SEARCH_SCHEMA,
        DEEP_RESEARCH_SCHEMA,
        A2A_DISPATCH_SCHEMA,
    ]

    if delivery is not None:
        llm.register_function(
            "slow_research",
            _slow_research_handler(delivery),
            cancel_on_interruption=False,
        )
        standard.append(SLOW_RESEARCH_SCHEMA)

    return ToolsSchema(standard_tools=standard)
