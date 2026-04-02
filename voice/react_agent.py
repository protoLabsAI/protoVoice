import ast
import json
import logging
import operator
import random
import re
import threading
from datetime import datetime
from typing import Generator
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

logger = logging.getLogger(__name__)

MAX_ITERATIONS = 5

# Prompt-based tool descriptions — no native function calling required.
REACT_TOOL_PROMPT = (
    "You have access to the following tools. To use one, output exactly:\n"
    "ACTION: <tool_name>\n"
    "INPUT: <json>\n\n"
    "Available tools:\n"
    "  web_search   — {\"query\": \"...\"} — search the web for current info or news\n"
    "  calculator   — {\"expression\": \"...\"} — evaluate a math expression, e.g. \"15 * 1.2\"\n"
    "  get_datetime — {} — get the current date and time\n\n"
    "Only use a tool when you actually need external information. "
    "When you have enough to answer, respond directly in spoken sentences — no ACTION block."
)

_ACTION_RE = re.compile(
    r"ACTION:\s*(\w+)\s*\nINPUT:\s*(\{[^}]*\})", re.DOTALL
)

THINKING_PHRASES = {
    "web_search": [
        "Let me search for that.",
        "One moment while I look that up.",
        "Searching now.",
    ],
    "calculator": [
        "Let me calculate that.",
        "Working that out.",
    ],
    "get_datetime": [],
}

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


def _safe_eval(node):
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


def _web_search(query: str) -> str:
    try:
        from duckduckgo_search import DDGS
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=3))
        if not results:
            return "No results found."
        return "\n\n".join(f"{r['title']}: {r['body']}" for r in results)
    except Exception as e:
        return f"Search failed: {e}"


def _calculator(expression: str) -> str:
    try:
        tree = ast.parse(expression.strip(), mode="eval")
        result = _safe_eval(tree.body)
        if isinstance(result, float) and result.is_integer():
            return str(int(result))
        return str(result)
    except Exception as e:
        return f"Calculation error: {e}"


def _execute_tool(name: str, args: dict, timezone: str = "UTC") -> str:
    if name == "web_search":
        return _web_search(args.get("query", ""))
    if name == "calculator":
        return _calculator(args.get("expression", ""))
    if name == "get_datetime":
        try:
            tz = ZoneInfo(timezone)
        except (ZoneInfoNotFoundError, Exception):
            tz = ZoneInfo("UTC")
        return datetime.now(tz=tz).strftime("%A, %B %d, %Y at %I:%M %p %Z")
    return f"Unknown tool: {name}"


def react_loop(
    user_text: str,
    history: list[dict],
    system_prompt: str,
    llm_url: str,
    model: str,
    max_tokens: int,
    temperature: float,
    cancel: threading.Event,
    api_key: str = "",
    timezone: str = "UTC",
) -> Generator[tuple[str, str], None, None]:
    """
    Generator yielding typed events:
      ("phrase", text)               — speak immediately (thinking phrase)
      ("token", text)                — full final response text, pipe through chunker
      ("history", (user, assistant)) — commit to history after response
    """
    from .llm import llm_complete

    react_system = system_prompt + "\n\n" + REACT_TOOL_PROMPT
    messages = [{"role": "system", "content": react_system}]
    messages.extend(history)
    messages.append({"role": "user", "content": user_text})

    full_response = ""

    for _ in range(MAX_ITERATIONS):
        if cancel.is_set():
            return

        try:
            msg = llm_complete(
                messages,
                llm_url=llm_url,
                model=model,
                max_tokens=max_tokens,
                temperature=temperature,
                api_key=api_key,
            )
        except Exception as e:
            logger.error(f"[ReAct] LLM error: {e}")
            yield ("token", "Sorry, I ran into an error.")
            return

        content = (msg.get("content") or "").strip()
        match = _ACTION_RE.search(content)

        if not match:
            # Final spoken response
            if content:
                full_response = content
                yield ("token", content)
            break

        name = match.group(1).strip()
        try:
            args = json.loads(match.group(2))
        except json.JSONDecodeError:
            args = {}

        phrases = THINKING_PHRASES.get(name, [])
        if phrases:
            yield ("phrase", random.choice(phrases))

        result = _execute_tool(name, args, timezone)
        logger.info(f"[ReAct] {name}({args!r}) → {result[:120]!r}")

        messages.append({"role": "assistant", "content": content})
        messages.append({"role": "user", "content": f"RESULT: {result}"})

    if full_response:
        yield ("history", (user_text, full_response))
