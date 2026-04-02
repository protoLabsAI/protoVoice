import json
import logging
import threading

import httpx

logger = logging.getLogger(__name__)

SUMMARY_PROMPT = (
    "Summarize this conversation so far in 2-3 sentences. "
    "Focus on key topics discussed and any important facts mentioned."
)


def stream_llm_tokens(
    text: str,
    history: list[dict],
    cancel: threading.Event,
    system_prompt: str,
    llm_url: str,
    model: str,
    max_tokens: int = 150,
    temperature: float = 0.7,
):
    messages = [{"role": "system", "content": system_prompt}]
    messages.extend(history)
    messages.append({"role": "user", "content": text})

    try:
        with httpx.Client(timeout=60.0) as client:
            with client.stream(
                "POST", f"{llm_url}/chat/completions",
                json={
                    "model": model,
                    "messages": messages,
                    "max_tokens": max_tokens,
                    "temperature": temperature,
                    "stream": True,
                    "chat_template_kwargs": {"enable_thinking": False},
                },
            ) as response:
                for line in response.iter_lines():
                    if cancel.is_set():
                        return
                    if not line.startswith("data: "):
                        continue
                    data = line[6:]
                    if data == "[DONE]":
                        break
                    chunk = json.loads(data)
                    delta = chunk["choices"][0].get("delta", {})
                    content = delta.get("content")
                    if content:
                        yield content
    except Exception as e:
        if not cancel.is_set():
            logger.error(f"LLM stream error: {e}")
            yield "Sorry, I couldn't process that."


def llm_complete(
    messages: list[dict],
    llm_url: str,
    model: str,
    max_tokens: int = 500,
    temperature: float = 0.7,
    tools: list[dict] | None = None,
) -> dict:
    """Non-streaming completion. Returns the assistant message dict."""
    payload: dict = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "chat_template_kwargs": {"enable_thinking": False},
    }
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"

    r = httpx.post(f"{llm_url}/chat/completions", json=payload, timeout=60.0)
    r.raise_for_status()
    return r.json()["choices"][0]["message"]


def llm_summarize(history: list[dict], llm_url: str, model: str) -> str:
    messages = [
        {"role": "system", "content": SUMMARY_PROMPT},
        {"role": "user", "content": "\n".join(
            f"{m['role']}: {m['content']}" for m in history
        )},
    ]
    try:
        r = httpx.post(
            f"{llm_url}/chat/completions",
            json={
                "model": model,
                "messages": messages,
                "max_tokens": 100,
                "temperature": 0.3,
                "chat_template_kwargs": {"enable_thinking": False},
            },
            timeout=15.0,
        )
        r.raise_for_status()
        return (r.json()["choices"][0]["message"].get("content") or "").strip()
    except Exception:
        return ""
