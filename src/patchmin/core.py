"""Minimise a patch to the hunks that make the target tests pass."""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from .ddmin import DdminTrace, ddmin
from .diff import Hunk, Patch, change_lines, hunk_with_lines, parse_patch, render_patch, with_hunk
from .oracle import Outcome, WorktreeOracle


@dataclass
class HunkVerdict:
    index: int
    label: str
    needed: bool
    added: int
    removed: int
    heading: str


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

    def to_dict(self) -> dict:
        return {
            "needed": self.needed,
            "hunks": [v.__dict__ for v in self.verdicts],
            "runs": self.runs,
            "cached_hits": self.cached_hits,
            "flaky_runs": self.flaky_runs,
            "seconds": round(self.seconds, 3),
            "tests": self.trace.tests,
            "lines": [v.__dict__ for v in self.line_verdicts],
            "error": self.error,
        }


def _verdicts(hunks: list[Hunk], needed: set[int]) -> list[HunkVerdict]:
    return [
        HunkVerdict(
            index=h.index,
            label=h.label(),
            needed=h.index in needed,
            added=h.added,
            removed=h.removed,
            heading=h.heading.strip(),
        )
        for h in hunks
    ]


def minimize_patch(patch: Patch, oracle: WorktreeOracle) -> MinimizeResult:
    """Run ddmin over the hunks of ``patch`` using ``oracle`` as the test.

    Before minimising, the full patch must pass and the empty patch must
    fail; otherwise the result carries an ``error`` and no verdicts.
    """
    t0 = time.monotonic()
    hunks = patch.hunks
    flaky = 0

    def check(subset: list[int]) -> bool:
        nonlocal flaky
        result = oracle.run(render_patch(patch, set(subset)))
        if result.flaky:
            flaky += 1
        return result.outcome is Outcome.PASS

    def finish(needed: list[int], trace: DdminTrace, error: str | None = None) -> MinimizeResult:
        return MinimizeResult(
            needed=sorted(needed),
            verdicts=_verdicts(hunks, set(needed)) if error is None else [],
            minimal_patch=render_patch(patch, set(needed)) if error is None else "",
            runs=oracle.runs,
            cached_hits=oracle.cache.hits,
            flaky_runs=flaky,
            seconds=time.monotonic() - t0,
            trace=trace,
            error=error,
        )

    trace = DdminTrace()
    if not hunks:
        return finish([], trace, "patch has no hunks")
    all_ids = [h.index for h in hunks]
    if not check(all_ids):
        return finish([], trace, "full patch does not make the target tests pass")
    if check([]):
        return finish([], trace, "target tests already pass without the patch")
    needed = ddmin(all_ids, check, trace)
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
    needed = set(result.needed)
    current = patch
    verdicts: list[LineVerdict] = []
    for hunk in patch.hunks:
        if hunk.index not in needed:
            continue
        positions = change_lines(hunk)

        def check(subset: list[int], hunk: Hunk = hunk, current: Patch = current) -> bool:
            candidate = with_hunk(current, hunk_with_lines(hunk, set(subset)))
            run = oracle.run(render_patch(candidate, needed))
            if run.flaky:
                result.flaky_runs += 1
            return run.outcome is Outcome.PASS

        kept = set(ddmin(positions, check, result.trace)) if len(positions) > 1 else set(positions)
        verdicts.extend(
            LineVerdict(hunk.index, i, hunk.lines[i], i in kept) for i in positions
        )
        current = with_hunk(current, hunk_with_lines(hunk, kept))
    result.line_verdicts = verdicts
    result.minimal_patch = render_patch(current, needed)
    result.runs = oracle.runs
    result.cached_hits = oracle.cache.hits
    result.seconds += time.monotonic() - t0
    return result


def minimize_text(
    patch_text: str, oracle: WorktreeOracle, lines: bool = False
) -> MinimizeResult:
    patch = parse_patch(patch_text)
    result = minimize_patch(patch, oracle)
    if lines:
        result = refine_lines(patch, result, oracle)
    return result
