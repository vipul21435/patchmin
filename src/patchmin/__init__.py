"""Find the part of a fix that makes the target tests pass."""

from .cache import RunCache
from .core import MinimizeResult, minimize_patch, minimize_text, refine_lines
from .ddmin import DdminTrace, ddmin
from .diff import Patch, parse_patch, render_patch
from .oracle import Outcome, RunResult, WorktreeOracle
from .targets import find_targets

__version__ = "0.1.0"

__all__ = [
    "DdminTrace",
    "MinimizeResult",
    "Outcome",
    "Patch",
    "RunCache",
    "RunResult",
    "WorktreeOracle",
    "ddmin",
    "find_targets",
    "minimize_patch",
    "minimize_text",
    "parse_patch",
    "refine_lines",
    "render_patch",
]
