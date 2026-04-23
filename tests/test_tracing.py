"""Langfuse v4 tracing helpers — call-shape contract.

The real Langfuse SDK is deliberately stubbed so these tests run
without the dependency installed and stay fast. They pin the
call shapes (method names + kwargs) we emit against v4 — if
Langfuse renames something upstream, these fail first.
"""

from __future__ import annotations

import asyncio
import sys
import types
from unittest.mock import MagicMock

import pytest


# ---------------------------------------------------------------------------
# Stub the `langfuse` package so `from langfuse import Langfuse` resolves to
# a MagicMock we control. This runs before `agent.tracing` imports it lazily.
# ---------------------------------------------------------------------------


@pytest.fixture
def tracing_enabled(monkeypatch):
    """Install a fake langfuse module + env vars, reload agent.tracing, yield
    (module, fake_client, fake_root). The fake_client is what Langfuse()
    returns; fake_root is what start_observation() returns."""
    fake_root = MagicMock(name="root_span")
    fake_root.id = "obs_abc"
    fake_root.trace_id = "trace_xyz"

    fake_nested = MagicMock(name="nested_span")
    fake_nested.id = "obs_nested"
    fake_nested.trace_id = "trace_xyz"
    fake_root.start_observation.return_value = fake_nested

    fake_client = MagicMock(name="langfuse_client")
    fake_client.start_observation.return_value = fake_root

    fake_module = types.ModuleType("langfuse")
    fake_module.Langfuse = MagicMock(return_value=fake_client)

    class _TraceContext:
        def __init__(self, trace_id=None, parent_span_id=None):
            self.trace_id = trace_id
            self.parent_span_id = parent_span_id

    fake_types = types.ModuleType("langfuse.types")
    fake_types.TraceContext = _TraceContext
    fake_module.types = fake_types

    monkeypatch.setitem(sys.modules, "langfuse", fake_module)
    monkeypatch.setitem(sys.modules, "langfuse.types", fake_types)

    monkeypatch.setenv("LANGFUSE_HOST", "http://fake")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-test")

    # Reload so the module-level `_ENABLED` reads our patched env.
    if "agent.tracing" in sys.modules:
        del sys.modules["agent.tracing"]
    import agent.tracing as tracing

    yield tracing, fake_client, fake_root, _TraceContext

    del sys.modules["agent.tracing"]


@pytest.fixture
def tracing_disabled(monkeypatch):
    """LANGFUSE_* env unset → module fails open, every helper is a no-op."""
    for k in ("LANGFUSE_HOST", "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY"):
        monkeypatch.delenv(k, raising=False)

    if "agent.tracing" in sys.modules:
        del sys.modules["agent.tracing"]
    import agent.tracing as tracing

    yield tracing

    del sys.modules["agent.tracing"]


# ---------------------------------------------------------------------------
# enabled() flag
# ---------------------------------------------------------------------------


def test_enabled_true_when_env_set(tracing_enabled):
    tracing, _, _, _ = tracing_enabled
    assert tracing.enabled() is True


def test_enabled_false_when_env_unset(tracing_disabled):
    assert tracing_disabled.enabled() is False


# ---------------------------------------------------------------------------
# start_turn_trace — v4 shape
# ---------------------------------------------------------------------------


def test_start_turn_trace_calls_start_observation_with_span_type(tracing_enabled):
    tracing, client, root, _ = tracing_enabled

    handle = tracing.start_turn_trace(
        session_id="sess-1",
        name="user_turn",
        input="hello",
        user_id="u1",
        metadata={"k": "v"},
    )

    client.start_observation.assert_called_once()
    kwargs = client.start_observation.call_args.kwargs
    assert kwargs["name"] == "user_turn"
    assert kwargs["as_type"] == "span"
    assert kwargs["input"] == "hello"
    assert kwargs["metadata"] == {"k": "v"}
    assert handle is root


def test_start_turn_trace_sets_trace_level_attrs(tracing_enabled):
    tracing, _, root, _ = tracing_enabled

    tracing.start_turn_trace(session_id="sess-1", user_id="u1", input="hi")

    root.update_trace.assert_called_once()
    kwargs = root.update_trace.call_args.kwargs
    assert kwargs["session_id"] == "sess-1"
    assert kwargs["user_id"] == "u1"


def test_start_turn_trace_returns_null_when_disabled(tracing_disabled):
    handle = tracing_disabled.start_turn_trace(session_id="s", input="x")
    assert handle is tracing_disabled._NULL


# ---------------------------------------------------------------------------
# continue_trace — v4 TraceContext-based re-attach
# ---------------------------------------------------------------------------


def test_continue_trace_uses_trace_context(tracing_enabled):
    tracing, client, root, TraceContext = tracing_enabled

    handle = tracing.continue_trace(trace_id="trace_xyz", session_id="sess-2")

    client.start_observation.assert_called_once()
    kwargs = client.start_observation.call_args.kwargs
    assert kwargs["as_type"] == "span"
    tc = kwargs["trace_context"]
    assert isinstance(tc, TraceContext)
    assert tc.trace_id == "trace_xyz"
    root.update_trace.assert_called_once_with(session_id="sess-2")
    assert handle is root


def test_continue_trace_returns_null_when_disabled(tracing_disabled):
    handle = tracing_disabled.continue_trace(trace_id="t", session_id="s")
    assert handle is tracing_disabled._NULL


# ---------------------------------------------------------------------------
# TurnTracer — open/close spans for LLM and tool frames
# ---------------------------------------------------------------------------


class _FakeFrame:
    """Tiny frame stand-in so we don't need to pipe pipecat into tests."""

    def __init__(self, cls_name, **attrs):
        self._cls_name = cls_name
        for k, v in attrs.items():
            setattr(self, k, v)


def _patch_frame_types(tracing, monkeypatch):
    """Replace _frame_types() with sentinel classes whose __instancecheck__
    matches _FakeFrame instances by name."""

    class _Sentinel(type):
        def __instancecheck__(cls, obj):
            return isinstance(obj, _FakeFrame) and obj._cls_name == cls.__name__

    names = [
        "UserStoppedSpeakingFrame",
        "BotStoppedSpeakingFrame",
        "TranscriptionFrame",
        "LLMFullResponseStartFrame",
        "LLMFullResponseEndFrame",
        "FunctionCallInProgressFrame",
        "FunctionCallResultFrame",
        "FunctionCallCancelFrame",
    ]
    out = {n: _Sentinel(n, (), {}) for n in names}
    monkeypatch.setattr(tracing, "_frame_types", lambda: out)


def _push(tracer, frame):
    asyncio.run(tracer.on_push_frame(types.SimpleNamespace(frame=frame)))


def test_turn_tracer_opens_llm_span_on_response_start(tracing_enabled, monkeypatch):
    tracing, _, root, _ = tracing_enabled
    _patch_frame_types(tracing, monkeypatch)

    tracer = tracing.TurnTracer(session_id="sess-1", user_id="u1")
    _push(tracer, _FakeFrame("TranscriptionFrame", text="what time is it"))
    _push(tracer, _FakeFrame("UserStoppedSpeakingFrame"))
    _push(tracer, _FakeFrame("LLMFullResponseStartFrame"))

    root.start_observation.assert_called_once()
    kwargs = root.start_observation.call_args.kwargs
    assert kwargs["name"] == "llm.response"
    assert kwargs["as_type"] == "span"
    assert kwargs["input"] == "what time is it"


def test_turn_tracer_opens_and_closes_tool_span(tracing_enabled, monkeypatch):
    tracing, _, root, _ = tracing_enabled
    _patch_frame_types(tracing, monkeypatch)

    fake_tool_span = MagicMock(name="tool_span")
    root.start_observation.return_value = fake_tool_span

    tracer = tracing.TurnTracer(session_id="sess-1", user_id="u1")
    _push(tracer, _FakeFrame("UserStoppedSpeakingFrame"))

    _push(tracer, _FakeFrame(
        "FunctionCallInProgressFrame", tool_call_id="tc-1", function_name="weather", arguments={"city": "SF"},
    ))
    root.start_observation.assert_called_once()
    kwargs = root.start_observation.call_args.kwargs
    assert kwargs["name"] == "tool.weather"
    assert kwargs["as_type"] == "span"

    _push(tracer, _FakeFrame("FunctionCallResultFrame", tool_call_id="tc-1", result="sunny"))
    fake_tool_span.end.assert_called_once()


def test_turn_tracer_closes_root_span_when_both_conditions_met(tracing_enabled, monkeypatch):
    tracing, _, root, _ = tracing_enabled
    _patch_frame_types(tracing, monkeypatch)

    tracer = tracing.TurnTracer(session_id="sess-1", user_id="u1")
    _push(tracer, _FakeFrame("UserStoppedSpeakingFrame"))
    _push(tracer, _FakeFrame("LLMFullResponseEndFrame"))
    root.end.assert_not_called()  # bot hasn't stopped yet
    _push(tracer, _FakeFrame("BotStoppedSpeakingFrame"))
    root.end.assert_called_once()


# ---------------------------------------------------------------------------
# tracing.span() @contextmanager — uses start_observation on active span
# ---------------------------------------------------------------------------


def test_span_contextmanager_uses_start_observation(tracing_enabled, monkeypatch):
    tracing, _, root, _ = tracing_enabled

    nested = MagicMock(name="nested")
    root.start_observation.return_value = nested

    # Active trace registry: pretend a turn is live.
    class _StubTracer:
        def get_current_trace(self):
            return root

    tracing.set_active_tracer(_StubTracer(), user_id="u1")

    from auth.context import current_user_id, current_session_id
    tok_u = current_user_id.set("u1")
    tok_s = current_session_id.set("sess-1")
    try:
        with tracing.span("stt.whisper", input={"sr": 16000}) as sp:
            pass
    finally:
        current_user_id.reset(tok_u)
        current_session_id.reset(tok_s)
        tracing.set_active_tracer(None, user_id="u1")

    root.start_observation.assert_called_once()
    kwargs = root.start_observation.call_args.kwargs
    assert kwargs["name"] == "stt.whisper"
    assert kwargs["as_type"] == "span"
    assert kwargs["input"] == {"sr": 16000}
    assert kwargs["metadata"]["user_id"] == "u1"
    assert kwargs["metadata"]["session_id"] == "sess-1"
    nested.end.assert_called_once()
    assert sp is nested


def test_span_contextmanager_yields_null_when_disabled(tracing_disabled):
    with tracing_disabled.span("noop") as sp:
        assert sp is tracing_disabled._NULL
        # .update / .end are no-ops
        sp.update(output="x")
        sp.end()


# ---------------------------------------------------------------------------
# flush + propagation headers
# ---------------------------------------------------------------------------


def test_flush_calls_client_flush(tracing_enabled):
    tracing, client, _, _ = tracing_enabled
    tracing.flush()
    client.flush.assert_called_once()


def test_flush_noop_when_disabled(tracing_disabled):
    # Just shouldn't raise.
    tracing_disabled.flush()


def test_propagation_headers_reads_trace_and_session_id(tracing_enabled):
    tracing, _, root, _ = tracing_enabled
    # root.trace_id = "trace_xyz" set in fixture; session_id comes from ContextVar.

    from auth.context import current_session_id
    tok = current_session_id.set("sess-1")
    try:
        headers = tracing.propagation_headers(trace=root)
    finally:
        current_session_id.reset(tok)

    assert headers["Langfuse-Trace-Id"] == "trace_xyz"
    assert headers["Langfuse-Session-Id"] == "sess-1"


def test_propagation_headers_empty_when_disabled(tracing_disabled):
    assert tracing_disabled.propagation_headers(trace=object()) == {}
