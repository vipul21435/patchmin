"""Delta debugging (ddmin) over an ordered list of items.

The algorithm follows Zeller and Hildebrandt, "Simplifying and Isolating
Failure-Inducing Input" (IEEE TSE 2002). ``interesting(subset)`` must return
True when the subset still has the property of interest (here: the target
tests pass). The result is 1-minimal: removing any single item makes the
property disappear. The search runs over positions, so items may repeat:
equal items at different positions are distinct members of the input.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import TypeVar

T = TypeVar("T")


@dataclass
class DdminTrace:
    """Counts and the sequence of subsets tried, for reporting."""

    tests: int = 0
    tried: list[tuple[int, bool]] = field(default_factory=list)


def _split(items: Sequence[T], n: int) -> list[list[T]]:
    size = len(items)
    chunks: list[list[T]] = []
    start = 0
    for i in range(n):
        end = start + (size - start) // (n - i)
        chunks.append(list(items[start:end]))
        start = end
    return [c for c in chunks if c]


def ddmin(
    items: Sequence[T],
    interesting: Callable[[list[T]], bool],
    trace: DdminTrace | None = None,
    evaluate: Callable[[list[list[T]]], list[bool]] | None = None,
    batch: int = 0,
) -> list[T]:
    """Return a 1-minimal subsequence of ``items`` for which ``interesting`` holds.

    ``interesting(list(items))`` is assumed to be True; the caller checks that.

    ``evaluate``, when given, takes every candidate subset of one round at
    once and returns one boolean per subset; it is how a caller runs the
    candidates in parallel. The first interesting subset in order is chosen,
    so the result is the same as the sequential search; the sequential search
    stops at the first interesting subset and may run fewer tests.

    ``batch`` is the number of subsets the caller can run at once. When a
    round has fewer chunks than that, the first complements are sent in the
    same call, so idle workers try them while the chunks run. The choice is
    still the first interesting chunk, then the first interesting
    complement, so the result does not change.
    """
    trace = trace if trace is not None else DdminTrace()
    values = list(items)
    # Work on positions and hand the callbacks the items at those positions;
    # building complements from positions keeps repeated items apart.
    current = list(range(len(values)))

    def pick(subset: list[int]) -> list[T]:
        return [values[i] for i in subset]

    def run_all(subsets: list[list[int]]) -> list[bool]:
        assert evaluate is not None
        return [bool(r) for r in evaluate([pick(s) for s in subsets])]

    def test(subset: list[int]) -> bool:
        trace.tests += 1
        result = bool(interesting(pick(subset)))
        trace.tried.append((len(subset), result))
        return result

    def first(subsets: list[list[int]]) -> int | None:
        if evaluate is None:
            for i, subset in enumerate(subsets):
                if test(subset):
                    return i
            return None
        results = run_all(subsets)
        trace.tests += len(subsets)
        trace.tried.extend((len(s), r) for s, r in zip(subsets, results, strict=True))
        return next((i for i, r in enumerate(results) if r), None)

    n = 2
    while len(current) >= 2:
        chunks = _split(current, n)
        # With two chunks each complement is the other chunk, already tried.
        complements: list[list[int]] = []
        if len(chunks) > 2:
            sets = [set(chunk) for chunk in chunks]
            complements = [[x for x in current if x not in c] for c in sets]
            complements = [c for c in complements if c]
        # Free workers (batch minus the chunks) try the first complements
        # speculatively in the same call; the rest follow if needed.
        extra = complements[: max(0, batch - len(chunks))] if evaluate is not None else []
        if extra:
            sent = chunks + extra
            results = run_all(sent)
            trace.tests += len(results)
            trace.tried.extend((len(s), r) for s, r in zip(sent, results, strict=True))
            hit = next((i for i, r in enumerate(results[: len(chunks)]) if r), None)
            if hit is not None:
                current = chunks[hit]
                n = 2
                continue
            hit = next((i for i, r in enumerate(results[len(chunks) :]) if r), None)
            if hit is None and len(extra) < len(complements):
                rest = first(complements[len(extra) :])
                hit = None if rest is None else len(extra) + rest
        else:
            hit = first(chunks)
            if hit is not None:
                current = chunks[hit]
                n = 2
                continue
            hit = first(complements) if complements else None
        if hit is not None:
            current = complements[hit]
            n = max(n - 1, 2)
            continue
        if n >= len(current):
            break
        n = min(n * 2, len(current))
    return pick(current)
