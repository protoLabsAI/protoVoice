import ast
import json
import logging
import operator
import random
import threading
from datetime import datetime
from typing import Generator

logger = logging.getLogger(__name__)

MAX_ITERATIONS = 5

TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search the web for current information, facts, or recent news.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query string"}
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "calculator",
            "description": "Evaluate a mathematical expression. Use for arithmetic, percentages, conversions.",
            "parameters": {
                "type": "object",
                "properties": {
                    "expression": {
                        "type": "string",
                        "description": "Math expression to evaluate, e.g. '(15 * 1.2) + 3'",
                    }
                },
                "required": ["expression"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_datetime",
            "description": "Get the current date and time.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]

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
        # Format cleanly: no trailing .0 for whole numbers
        if isinstance(result, float) and result.is_integer():
            return str(int(result))
        return str(result)
    except Exception as e:
        return f"Calculation error: {e}"


def _execute_tool(name: str, args: dict) -> str:
    if name == "web_search":
        return _web_search(args.get("query", ""))
    if name == "calculator":
        return _calculator(args.get("expression", ""))
    if name == "get_datetime":
        return datetime.now().strftime("%A, %B %d, %Y at %I:%M %p")
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
) -> Generator[tuple[str, str], None, None]:
    """
    Generator yielding typed events:
      ("phrase", text)               — speak immediately (thinking phrase)
      ("token", text)                — full final response text, pipe through chunker
      ("history", (user, assistant)) — commit to history after response
    """
    from .llm import llm_complete

    messages = [{"role": "system", "content": system_prompt}]
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
                tools=TOOL_SCHEMAS,
                api_key=api_key,
            )
        except Exception as e:
            logger.error(f"[ReAct] LLM error: {e}")
            yield ("token", "Sorry, I ran into an error.")
            return

        tool_calls = msg.get("tool_calls") or []

        if not tool_calls:
            content = (msg.get("content") or "").strip()
            if content:
                full_response = content
                yield ("token", content)
            break

        # Append assistant message with tool calls before executing
        messages.append(msg)

        for tc in tool_calls:
            if cancel.is_set():
                return
            fn = tc.get("function", {})
            name = fn.get("name", "")
            try:
                args = json.loads(fn.get("arguments", "{}"))
            except json.JSONDecodeError:
                args = {}

            phrases = THINKING_PHRASES.get(name, [])
            if phrases:
                yield ("phrase", random.choice(phrases))

            result = _execute_tool(name, args)
            logger.info(f"[ReAct] {name}({args!r}) → {result[:120]!r}")

            messages.append({
                "role": "tool",
                "tool_call_id": tc.get("id", ""),
                "content": result,
            })

    if full_response:
        yield ("history", (user_text, full_response))
