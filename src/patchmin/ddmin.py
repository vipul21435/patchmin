"""Delta debugging (ddmin) over an ordered list of items.

The algorithm follows Zeller and Hildebrandt, "Simplifying and Isolating
Failure-Inducing Input" (IEEE TSE 2002). ``interesting(subset)`` must return
True when the subset still has the property of interest (here: the target
tests pass). The result is 1-minimal: removing any single item makes the
property disappear.
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

    ``batch`` is the number of subsets the caller can run at once. When the
    chunks and their complements of a round fit in one batch, they are sent
    together, so idle workers try the complements while the chunks run. The
    choice is still the first interesting chunk, then the first interesting
    complement, so the result does not change.
    """
    trace = trace if trace is not None else DdminTrace()
    current = list(items)

    def test(subset: list[T]) -> bool:
        trace.tests += 1
        result = bool(interesting(subset))
        trace.tried.append((len(subset), result))
        return result

    def first(subsets: list[list[T]]) -> int | None:
        if evaluate is None:
            for i, subset in enumerate(subsets):
                if test(subset):
                    return i
            return None
        results = [bool(r) for r in evaluate(subsets)]
        trace.tests += len(subsets)
        trace.tried.extend((len(s), r) for s, r in zip(subsets, results, strict=True))
        return next((i for i, r in enumerate(results) if r), None)

    n = 2
    while len(current) >= 2:
        chunks = _split(current, n)
        # With two chunks each complement is the other chunk, already tried.
        complements: list[list[T]] = []
        if len(chunks) > 2:
            complements = [[x for x in current if x not in chunk] for chunk in chunks]
            complements = [c for c in complements if c]
        together = evaluate is not None and 0 < len(chunks) + len(complements) <= batch
        if together and complements:
            results = [bool(r) for r in evaluate(chunks + complements)]
            trace.tests += len(results)
            both = chunks + complements
            trace.tried.extend((len(s), r) for s, r in zip(both, results, strict=True))
            hit = next((i for i, r in enumerate(results[: len(chunks)]) if r), None)
            if hit is not None:
                current = chunks[hit]
                n = 2
                continue
            hit = next((i for i, r in enumerate(results[len(chunks) :]) if r), None)
            if hit is not None:
                current = complements[hit]
                n = max(n - 1, 2)
                continue
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
    return current
