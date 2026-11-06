"""Minimise a patch to the hunks that make the target tests pass."""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from .ddmin import DdminTrace, ddmin
from .diff import (
    Hunk,
    Patch,
    change_lines,
    classify_hunk,
    hunk_with_lines,
    matches_any,
    parse_patch,
    render_patch,
    with_hunk,
)
from .oracle import Outcome, RunResult, WorktreeOracle


@dataclass
class HunkVerdict:
    index: int
    label: str
    needed: bool
    added: int
    removed: int
    heading: str
    kind: str = "code"
    pinned: bool = False

    @property
    def status(self) -> str:
        if self.pinned:
            return "pinned"
        return "needed" if self.needed else "not needed"


@dataclass
class LineVerdict:
    hunk: int
    position: int
    text: str
    needed: bool


@dataclass
class MinimizeResult:
    needed: list[int]
    verdicts: list[HunkVerdict]
    minimal_patch: str
    runs: int
    cached_hits: int
    flaky_runs: int
    seconds: float
    trace: DdminTrace = field(default_factory=DdminTrace)
    error: str | None = None
    line_verdicts: list[LineVerdict] = field(default_factory=list)
    pinned: list[int] = field(default_factory=list)
    unresolved: dict[str, int] = field(default_factory=dict)
    """Distinct subsets left unresolved, by reason (``apply``, ``timeout``, ``flaky``)."""

    def to_dict(self) -> dict:
        return {
            "needed": self.needed,
            "pinned": self.pinned,
            "hunks": [dict(v.__dict__, status=v.status) for v in self.verdicts],
            "runs": self.runs,
            "cached_hits": self.cached_hits,
            "flaky_runs": self.flaky_runs,
            "unresolved": self.unresolved,
            "seconds": round(self.seconds, 3),
            "tests": self.trace.tests,
            "lines": [v.__dict__ for v in self.line_verdicts],
            "error": self.error,
        }


def _verdicts(hunks: list[Hunk], needed: set[int], pinned: set[int]) -> list[HunkVerdict]:
    return [
        HunkVerdict(
            index=h.index,
            label=h.label(),
            needed=h.index in needed or h.index in pinned,
            added=h.added,
            removed=h.removed,
            heading=h.heading.strip(),
            kind=classify_hunk(h),
            pinned=h.index in pinned,
        )
        for h in hunks
    ]


def pinned_hunks(patch: Patch, patterns: list[str]) -> set[int]:
    """Indices of the hunks whose path matches one of ``patterns``."""
    if not patterns:
        return set()
    return {h.index for h in patch.hunks if matches_any(h.new_path or h.old_path, patterns)}


class _Search:
    """Shared state for the hunk and line passes."""

    def __init__(self, patch: Patch, oracle: WorktreeOracle, pinned: set[int]) -> None:
        self.patch = patch
        self.oracle = oracle
        self.pinned = pinned
        self.parallel = oracle.jobs > 1
        self.unresolved: dict[str, set[str]] = {}

    @property
    def flaky(self) -> int:
        return len(self.unresolved.get("flaky", ()))

    def counts(self) -> dict[str, int]:
        return {reason: len(texts) for reason, texts in sorted(self.unresolved.items())}

    def _note(self, text: str, run: RunResult) -> bool:
        if run.reason:
            self.unresolved.setdefault(run.reason, set()).add(text)
        return run.outcome is Outcome.PASS

    def passes(self, text: str) -> bool:
        return self._note(text, self.oracle.run(text))

    def passes_many(self, texts: list[str]) -> list[bool]:
        runs = self.oracle.run_many(texts)
        return [self._note(t, r) for t, r in zip(texts, runs, strict=True)]

    def search(self, items: list, render, trace: DdminTrace) -> list:
        """ddmin over ``items``; ``render(subset)`` gives the patch text."""

        def interesting(subset: list) -> bool:
            return self.passes(render(subset))

        def evaluate(subsets: list[list]) -> list[bool]:
            return self.passes_many([render(s) for s in subsets])

        return ddmin(items, interesting, trace, evaluate=evaluate if self.parallel else None)


def minimize_patch(
    patch: Patch, oracle: WorktreeOracle, pin: list[str] | None = None
) -> MinimizeResult:
    """Run ddmin over the hunks of ``patch`` using ``oracle`` as the test.

    Hunks whose path matches a ``pin`` pattern are applied in every trial and
    left out of the search (test files, typically). Before minimising, the
    full patch must pass and the pinned hunks alone must fail; otherwise the
    result carries an ``error`` and no verdicts.
    """
    t0 = time.monotonic()
    hunks = patch.hunks
    pinned = pinned_hunks(patch, pin or [])
    state = _Search(patch, oracle, pinned)

    def render(subset: list[int]) -> str:
        return render_patch(patch, set(subset) | pinned)

    def finish(needed: list[int], trace: DdminTrace, error: str | None = None) -> MinimizeResult:
        return MinimizeResult(
            needed=sorted(needed),
            verdicts=_verdicts(hunks, set(needed), pinned) if error is None else [],
            minimal_patch=render(needed) if error is None else "",
            runs=oracle.runs,
            cached_hits=oracle.cache.hits,
            flaky_runs=state.flaky,
            seconds=time.monotonic() - t0,
            trace=trace,
            error=error,
            pinned=sorted(pinned),
            unresolved=state.counts(),
        )

    trace = DdminTrace()
    free = [h.index for h in hunks if h.index not in pinned]
    if not hunks:
        return finish([], trace, "patch has no hunks")
    if not free:
        return finish([], trace, "every hunk is pinned; nothing to search")
    if not state.passes(render(free)):
        return finish([], trace, "full patch does not make the target tests pass")
    if state.passes(render([])):
        what = "with the pinned hunks alone" if pinned else "without the patch"
        return finish([], trace, f"target tests already pass {what}")
    needed = state.search(free, render, trace)
    return finish(needed, trace)


def refine_lines(patch: Patch, result: MinimizeResult, oracle: WorktreeOracle) -> MinimizeResult:
    """Run ddmin over the change lines of every needed hunk, one hunk at a time.

    While one hunk is refined the other needed hunks stay whole. The result
    is updated in place: ``line_verdicts`` and ``minimal_patch`` are set and
    the run counts are refreshed.
    """
    if result.error is not None:
        return result
    t0 = time.monotonic()
    pinned = set(result.pinned)
    applied = set(result.needed) | pinned
    state = _Search(patch, oracle, pinned)
    current = patch
    verdicts: list[LineVerdict] = []
    for hunk in patch.hunks:
        if hunk.index not in applied or hunk.index in pinned:
            continue
        positions = change_lines(hunk)

        def render(subset: list[int], hunk: Hunk = hunk, current: Patch = current) -> str:
            return render_patch(with_hunk(current, hunk_with_lines(hunk, set(subset))), applied)

        kept = (
            set(state.search(positions, render, result.trace))
            if len(positions) > 1
            else set(positions)
        )
        verdicts.extend(
            LineVerdict(hunk.index, i, hunk.lines[i], i in kept) for i in positions
        )
        current = with_hunk(current, hunk_with_lines(hunk, kept))
    result.line_verdicts = verdicts
    result.minimal_patch = render_patch(current, applied)
    result.runs = oracle.runs
    result.cached_hits = oracle.cache.hits
    result.flaky_runs += state.flaky
    for reason, n in state.counts().items():
        result.unresolved[reason] = result.unresolved.get(reason, 0) + n
    result.seconds += time.monotonic() - t0
    return result


def minimize_text(
    patch_text: str,
    oracle: WorktreeOracle,
    lines: bool = False,
    pin: list[str] | None = None,
) -> MinimizeResult:
    patch = parse_patch(patch_text)
    result = minimize_patch(patch, oracle, pin=pin)
    if lines:
        result = refine_lines(patch, result, oracle)
    return result
