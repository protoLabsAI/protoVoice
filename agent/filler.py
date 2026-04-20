"""Verbosity-driven filler phrases for speak-while-thinking.

Verbosity levels (escalating chattiness):

    silent   — no filler at all; the agent is mute until the real response
    brief    — one short acknowledgement per tool dispatch
    narrated — brief + periodic progress if the tool takes long
    chatty   — narrated + more expressive phrasing

The picker is stateless — just returns a phrase. Future iterations can track
per-session phrase history to avoid repetition.
"""

from __future__ import annotations

import os
import random
from dataclasses import dataclass
from enum import Enum


class Verbosity(str, Enum):
    SILENT = "silent"
    BRIEF = "brief"
    NARRATED = "narrated"
    CHATTY = "chatty"


DEFAULT_VERBOSITY = Verbosity(os.environ.get("VERBOSITY", Verbosity.BRIEF.value).lower())


_BRIEF_FILLERS = [
    "One sec.",
    "Let me check.",
    "Hmm, looking.",
    "Hold on.",
    "Give me a moment.",
]

_NARRATED_FILLERS = [
    "Let me look that up.",
    "One moment — checking on that now.",
    "Give me a second, I'm pulling that up.",
    "Hold on, I'll find that for you.",
]

_CHATTY_FILLERS = [
    "Good question — let me dig in on that.",
    "Hang on, I'm checking a few sources.",
    "One sec, I want to make sure I get this right.",
    "Let me pull that up real quick for you.",
    "Just a moment — I'll go find the answer.",
]

_NARRATED_PROGRESS = [
    "Still looking.",
    "Working on it.",
    "Almost there.",
]

_CHATTY_PROGRESS = [
    "Still digging — give me a moment.",
    "Found some info, sorting through it.",
    "Almost there, just verifying.",
]


@dataclass
class Settings:
    """Runtime filler behaviour for a session."""

    verbosity: Verbosity = DEFAULT_VERBOSITY
    # Delay before emitting a narrated-progress "still working" frame.
    progress_after_secs: float = 3.0
    # Interval between subsequent progress frames.
    progress_interval_secs: float = 4.0


def opening_filler(s: Settings) -> str | None:
    """Return the phrase to speak when a tool dispatch begins, or None."""
    if s.verbosity == Verbosity.SILENT:
        return None
    if s.verbosity == Verbosity.BRIEF:
        return random.choice(_BRIEF_FILLERS)
    if s.verbosity == Verbosity.NARRATED:
        return random.choice(_NARRATED_FILLERS)
    return random.choice(_CHATTY_FILLERS)


def progress_filler(s: Settings) -> str | None:
    """Return a periodic progress phrase, or None if the verbosity is below
    the narrated threshold."""
    if s.verbosity in (Verbosity.SILENT, Verbosity.BRIEF):
        return None
    if s.verbosity == Verbosity.NARRATED:
        return random.choice(_NARRATED_PROGRESS)
    return random.choice(_CHATTY_PROGRESS)
