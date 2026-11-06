import random

from patchmin.ddmin import DdminTrace, ddmin


def test_single_needed_item():
    items = list(range(8))
    result = ddmin(items, lambda s: 5 in s)
    assert result == [5]


def test_two_needed_items_are_kept_in_order():
    items = list(range(10))
    result = ddmin(items, lambda s: 2 in s and 7 in s)
    assert result == [2, 7]


def test_all_needed():
    items = list(range(4))
    result = ddmin(items, lambda s: set(s) == {0, 1, 2, 3})
    assert result == items


def test_trace_counts_every_call():
    trace = DdminTrace()
    ddmin(list(range(6)), lambda s: 3 in s, trace)
    assert trace.tests == len(trace.tried) > 0


def test_random_subsets_are_one_minimal():
    rng = random.Random(7)
    for _ in range(40):
        n = rng.randint(1, 20)
        needed = set(rng.sample(range(n), rng.randint(1, min(n, 4))))

        def interesting(s, needed=needed):
            return needed <= set(s)

        result = ddmin(list(range(n)), interesting)
        assert set(result) == needed
        for x in result:
            assert not interesting([y for y in result if y != x])
