"""Minimise a patch to the hunks that make the target tests pass."""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from .ddmin import DdminTrace, ddmin
from .diff import Hunk, Patch, parse_patch, render_patch
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

    def to_dict(self) -> dict:
        return {
            "needed": self.needed,
            "hunks": [v.__dict__ for v in self.verdicts],
            "runs": self.runs,
            "cached_hits": self.cached_hits,
            "flaky_runs": self.flaky_runs,
            "seconds": round(self.seconds, 3),
            "tests": self.trace.tests,
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


def minimize_text(patch_text: str, oracle: WorktreeOracle) -> MinimizeResult:
    return minimize_patch(parse_patch(patch_text), oracle)
