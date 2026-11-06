"""Exact traces of the granularity schedule, worked out by hand.

The rules (Zeller and Hildebrandt 2002): split into n chunks; a passing
chunk becomes the input with n reset to 2; otherwise a passing complement
becomes the input with n reduced to max(n - 1, 2); otherwise n doubles,
capped at the input size, and the search stops once n reached it.
"""

import importlib.util
import random
import statistics
from pathlib import Path

from patchmin.ddmin import DdminTrace, ddmin

F, T = False, True


def _pair(s):
    # Needs items 1 and 6, which sit in different halves and quarters.
    return {1, 6} <= set(s)


# 0..7, needs {1, 6}: two failing halves, n = 4; complement [0,1,4,5,6,7]
# passes, n = 3; complement [0,1,6,7] passes, n = 2; halves fail, n = 4;
# complement [1,6,7] passes, n = 3; complement [1,6] passes, n = 2; both
# singletons fail and n equals the size: stop.
PAIR_TRACE = [
    (4, F), (4, F),
    (2, F), (2, F), (2, F), (2, F), (6, F), (6, T),
    (2, F), (2, F), (2, F), (4, F), (4, T),
    (2, F), (2, F),
    (1, F), (1, F), (1, F), (1, F), (3, T),
    (1, F), (1, F), (1, F), (2, F), (2, F), (2, T),
    (1, F), (1, F),
]


def _small(s):
    # Not monotone on purpose: a passing chunk shows up only at n = 4.
    return {3, 5} <= set(s) and len(s) <= 4


# 0..11, needs {3, 5} in at most 4 items: halves of 6 fail, n = 4; chunk
# [3,4,5] passes, n = 2; [3] and [4,5] fail, n = 3; singletons fail,
# complement [4,5] fails, [3,5] passes, n = 2; singletons fail: stop.
SMALL_TRACE = [
    (6, F), (6, F),
    (3, F), (3, T),
    (1, F), (2, F),
    (1, F), (1, F), (1, F), (2, F), (2, T),
    (1, F), (1, F),
]

# The same search with every subset of a round sent at once and 8 workers:
# at n = 4 the four complements go with the chunks; at n = 3 all three.
SMALL_BATCH_TRACE = [
    (6, F), (6, F),
    (3, F), (3, T), (3, F), (3, F), (9, F), (9, F), (9, F), (9, F),
    (1, F), (2, F),
    (1, F), (1, F), (1, F), (2, F), (2, T), (2, F),
    (1, F), (1, F),
]


def test_complement_steps_reduce_granularity_by_one():
    trace = DdminTrace()
    assert ddmin(list(range(8)), _pair, trace) == [1, 6]
    assert trace.tried == PAIR_TRACE
    assert trace.tests == len(PAIR_TRACE)


def test_passing_chunk_resets_granularity_to_two():
    trace = DdminTrace()
    assert ddmin(list(range(12)), _small, trace) == [3, 5]
    assert trace.tried == SMALL_TRACE


def test_passing_chunk_resets_granularity_in_batched_rounds():
    trace = DdminTrace()

    def evaluate(subsets):
        return [_small(s) for s in subsets]

    result = ddmin(list(range(12)), _small, trace, evaluate=evaluate, batch=8)
    assert result == [3, 5]
    assert trace.tried == SMALL_BATCH_TRACE
    assert trace.tests == len(SMALL_BATCH_TRACE)


def test_cost_example_matches_the_readme_row():
    # The README table row for n = 8 comes from examples/ddmin_cost.py with
    # seed 1; the draws are shared across sizes, so replay them in order.
    path = Path(__file__).resolve().parents[1] / "examples" / "ddmin_cost.py"
    spec = importlib.util.spec_from_file_location("ddmin_cost", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    rng = random.Random(1)
    row = {}
    for n in module.SIZES[: module.SIZES.index(8) + 1]:
        for k in module.NEEDED:
            if k <= n:
                counts = module.trials(n, k, 200, rng)
                row[(n, k)] = (round(statistics.fmean(counts), 1), max(counts))
    assert [row[(8, k)] for k in (1, 2, 4)] == [(4.5, 6), (21.6, 30), (40.9, 58)]
