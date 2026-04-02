import json
import logging
import re
import threading

import httpx

logger = logging.getLogger(__name__)

_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)

# Endpoints that rejected chat_template_kwargs (e.g. real OpenAI/Anthropic gateways)
_no_thinking_ctrl: set[str] = set()

SUMMARY_PROMPT = (
    "Summarize this conversation so far in 2-3 sentences. "
    "Focus on key topics discussed and any important facts mentioned."
)


def _headers(api_key: str) -> dict:
    if api_key:
        return {"Authorization": f"Bearer {api_key}"}
    return {}


def stream_llm_tokens(
    text: str,
    history: list[dict],
    cancel: threading.Event,
    system_prompt: str,
    llm_url: str,
    model: str,
    max_tokens: int = 150,
    temperature: float = 0.7,
    api_key: str = "",
):
    messages = [{"role": "system", "content": system_prompt}]
    messages.extend(history)
    messages.append({"role": "user", "content": text})

    base: dict = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "stream": True,
    }

    for attempt in range(2):
        payload = dict(base)
        if llm_url not in _no_thinking_ctrl:
            payload["chat_template_kwargs"] = {"enable_thinking": False}

        try:
            retry = False
            with httpx.Client(timeout=60.0) as client:
                with client.stream(
                    "POST", f"{llm_url}/chat/completions",
                    headers=_headers(api_key),
                    json=payload,
                ) as response:
                    if response.status_code == 400 and "chat_template_kwargs" in payload:
                        _no_thinking_ctrl.add(llm_url)
                        logger.info(f"[LLM] {llm_url} doesn't support chat_template_kwargs, retrying")
                        retry = True
                    else:
                        response.raise_for_status()
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
                                content = _THINK_RE.sub("", content)
                                if content:
                                    yield content
            if not retry:
                return
        except Exception as e:
            if not cancel.is_set():
                logger.error(f"LLM stream error: {e}")
                yield "Sorry, I couldn't process that."
            return


def llm_complete(
    messages: list[dict],
    llm_url: str,
    model: str,
    max_tokens: int = 500,
    temperature: float = 0.7,
    api_key: str = "",
) -> dict:
    """Non-streaming completion. Returns the assistant message dict."""
    base: dict = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }

    for attempt in range(2):
        payload = dict(base)
        if llm_url not in _no_thinking_ctrl:
            payload["chat_template_kwargs"] = {"enable_thinking": False}

        r = httpx.post(
            f"{llm_url}/chat/completions",
            headers=_headers(api_key),
            json=payload,
            timeout=60.0,
        )
        if r.status_code == 400 and "chat_template_kwargs" in payload:
            _no_thinking_ctrl.add(llm_url)
            logger.info(f"[LLM] {llm_url} doesn't support chat_template_kwargs, retrying")
            continue
        r.raise_for_status()
        return r.json()["choices"][0]["message"]

    raise RuntimeError("llm_complete failed after retry")


def llm_summarize(history: list[dict], llm_url: str, model: str, api_key: str = "") -> str:
    messages = [
        {"role": "system", "content": SUMMARY_PROMPT},
        {"role": "user", "content": "\n".join(
            f"{m['role']}: {m['content']}" for m in history
        )},
    ]
    try:
        base = {
            "model": model,
            "messages": messages,
            "max_tokens": 100,
            "temperature": 0.3,
        }
        for attempt in range(2):
            payload = dict(base)
            if llm_url not in _no_thinking_ctrl:
                payload["chat_template_kwargs"] = {"enable_thinking": False}
            r = httpx.post(
                f"{llm_url}/chat/completions",
                headers=_headers(api_key),
                json=payload,
                timeout=15.0,
            )
            if r.status_code == 400 and "chat_template_kwargs" in payload:
                _no_thinking_ctrl.add(llm_url)
                continue
            r.raise_for_status()
            return (r.json()["choices"][0]["message"].get("content") or "").strip()
    except Exception:
        return ""
    return ""
