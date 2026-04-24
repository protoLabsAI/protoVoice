"""Shared pytest fixtures.

Two things need to be isolated from the real environment:

1. ``SESSION_STORE_DIR`` — persisted skill selection + summaries + orphan
   deliveries all land on disk. Tests must not collide with the real
   ``/tmp/protovoice_sessions/`` directory. Set BEFORE any app import, since
   ``session_store._DEFAULT_DIR`` is captured at module-load time.

2. ``UserStateRegistry`` — module-level singleton inside ``agent.user_state``.
   Tests share it across the whole run; we reset it between tests so
   hydrated state from one test doesn't leak into the next.
"""

from __future__ import annotations

import os
import tempfile

# Must run before pytest collects any test module that imports ``app``.
_TEST_SESSION_DIR = tempfile.mkdtemp(prefix="protovoice_tests_")
os.environ["SESSION_STORE_DIR"] = _TEST_SESSION_DIR

import pytest


@pytest.fixture(autouse=True)
def _reset_user_state_registry():
    """Fresh in-memory registry per test so hydration-from-disk is
    deterministic. Tests that want to pre-seed state write to disk via
    ``save_skill_slug`` and then trigger a ``get()``, or just set
    ``user_state_for(...).skill_slug`` directly after this reset runs."""
    from agent import user_state

    user_state._REGISTRY = user_state.UserStateRegistry()
    yield
    user_state._REGISTRY = user_state.UserStateRegistry()
