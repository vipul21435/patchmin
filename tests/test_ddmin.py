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


def _random_oracle(n: int, seed: int):
    """An arbitrary, non-monotone oracle: each subset is interesting or not
    at random, except that the full set is and the empty set is not."""
    full = frozenset(range(n))

    def interesting(s):
        key = frozenset(s)
        if key == full:
            return True
        if not key:
            return False
        return random.Random(hash((seed, tuple(sorted(key))))).random() < 0.3

    return interesting


def _assert_one_minimal(result, items, interesting):
    assert interesting(result)
    assert result == [x for x in items if x in set(result)], "order and membership kept"
    for x in result:
        assert not interesting([y for y in result if y != x]), (result, x)


def test_one_minimal_on_arbitrary_oracles():
    rng = random.Random(11)
    for trial in range(300):
        n = rng.randint(1, 16)
        items = list(range(n))
        interesting = _random_oracle(n, trial)
        _assert_one_minimal(ddmin(items, interesting), items, interesting)


def test_one_minimal_on_disjunctive_oracles():
    # Interesting when any of several needed sets is present: several
    # 1-minimal answers exist and ddmin must return one of them.
    rng = random.Random(5)
    for _ in range(200):
        n = rng.randint(2, 18)
        options = [frozenset(rng.sample(range(n), rng.randint(1, min(n, 3)))) for _ in range(3)]

        def interesting(s, options=options):
            have = set(s)
            return any(o <= have for o in options)

        items = list(range(n))
        result = ddmin(items, interesting)
        _assert_one_minimal(result, items, interesting)
        assert frozenset(result) in options


def test_test_count_within_quadratic_bound():
    # Proposition 12 of Zeller and Hildebrandt (2002): at most n^2 + 3n tests.
    rng = random.Random(3)
    for trial in range(200):
        n = rng.randint(1, 24)
        interesting = _random_oracle(n, 1000 + trial)
        trace = DdminTrace()
        ddmin(list(range(n)), interesting, trace)
        assert trace.tests <= n * n + 3 * n


def test_parallel_evaluate_gives_the_sequential_result():
    rng = random.Random(9)
    for trial in range(150):
        n = rng.randint(1, 16)
        interesting = _random_oracle(n, 2000 + trial)
        calls: list[int] = []

        def evaluate(subsets, interesting=interesting, calls=calls):
            calls.append(len(subsets))
            return [interesting(s) for s in subsets]

        items = list(range(n))
        seq = ddmin(items, interesting)
        par_trace = DdminTrace()
        par = ddmin(items, interesting, par_trace, evaluate=evaluate)
        assert par == seq
        assert par_trace.tests == sum(calls)


def test_single_item_is_returned_without_a_test():
    trace = DdminTrace()
    assert ddmin(["only"], lambda s: True, trace) == ["only"]
    assert trace.tests == 0


def test_no_complement_trials_with_two_chunks():
    seen: list[tuple] = []

    def interesting(s):
        seen.append(tuple(s))
        return set(s) == {0, 1}

    assert ddmin([0, 1], interesting) == [0, 1]
    assert sorted(seen) == [(0,), (1,)]


def test_batched_complements_give_the_sequential_result():
    rng = random.Random(21)
    for trial in range(150):
        n = rng.randint(1, 16)
        interesting = _random_oracle(n, 3000 + trial)
        batches: list[int] = []

        def evaluate(subsets, interesting=interesting, batches=batches):
            batches.append(len(subsets))
            return [interesting(s) for s in subsets]

        items = list(range(n))
        seq = ddmin(items, interesting)
        for batch in (4, 8, 64):
            batches.clear()
            assert ddmin(items, interesting, evaluate=evaluate, batch=batch) == seq
            assert all(size <= max(batch, n) for size in batches)


def test_batch_sends_chunks_and_complements_together():
    sizes: list[int] = []

    def evaluate(subsets):
        sizes.append(len(subsets))
        return [set(s) == {0, 1, 2, 3, 4, 5, 6, 7} for s in subsets]

    # Nothing smaller passes: rounds of 2 chunks, then 4 chunks + 4 complements, then 8 + 8.
    ddmin(list(range(8)), lambda s: len(s) == 8, evaluate=evaluate, batch=16)
    assert sizes == [2, 8, 16]
    # With 6 workers: 4 chunks + 2 complements, then the other 2 complements;
    # 8 chunks leave no free worker, then the 8 complements.
    sizes.clear()
    ddmin(list(range(8)), lambda s: len(s) == 8, evaluate=evaluate, batch=6)
    assert sizes == [2, 6, 2, 8, 8]
