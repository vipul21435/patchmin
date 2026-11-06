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
) -> list[T]:
    """Return a 1-minimal subsequence of ``items`` for which ``interesting`` holds.

    ``interesting(list(items))`` is assumed to be True; the caller checks that.
    """
    trace = trace if trace is not None else DdminTrace()
    current = list(items)

    def test(subset: list[T]) -> bool:
        trace.tests += 1
        result = bool(interesting(subset))
        trace.tried.append((len(subset), result))
        return result

    n = 2
    while len(current) >= 2:
        chunks = _split(current, n)
        reduced = False
        for chunk in chunks:
            if test(chunk):
                current = chunk
                n = 2
                reduced = True
                break
        if reduced:
            continue
        if len(chunks) > 2 or len(current) > len(chunks):
            for chunk in chunks:
                complement = [x for x in current if x not in chunk]
                if complement and test(complement):
                    current = complement
                    n = max(n - 1, 2)
                    reduced = True
                    break
        if reduced:
            continue
        if n >= len(current):
            break
        n = min(n * 2, len(current))
    return current
