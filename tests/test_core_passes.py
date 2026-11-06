"""The line pass and confirmation step against a scripted oracle."""

from types import SimpleNamespace

from patchmin.core import MinimizeResult, confirm_result, refine_lines
from patchmin.diff import parse_patch, render_patch
from patchmin.oracle import Outcome, RunResult


class ScriptedOracle:
    """Passes the texts in ``passing``; every other text times out."""

    jobs = 1

    def __init__(self, passing: set[str]) -> None:
        self.passing = passing
        self.runs = 0
        self.cache = SimpleNamespace(hits=0)

    def run(self, text: str) -> RunResult:
        self.runs += 1
        if text in self.passing:
            return RunResult(Outcome.PASS, 0.0, 0)
        return RunResult(Outcome.UNRESOLVED, 0.0, None, reason="timeout")

    def confirm(self, text: str, times: int) -> list[RunResult]:
        raise AssertionError("confirm must not run")


EDIT = "--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-x = 1\n+x = 2\n"
DELETE = "--- a/gone.py\n+++ /dev/null\n@@ -1,2 +0,0 @@\n-a = 1\n-b = 2\n"


def _result(**over) -> MinimizeResult:
    fields = dict(
        needed=[0], verdicts=[], minimal_patch="", runs=0, cached_hits=0, flaky_runs=0,
        seconds=0.0,
    )
    fields.update(over)
    return MinimizeResult(**fields)


def test_line_pass_adds_its_unresolved_counts_to_the_hunk_pass():
    patch = parse_patch(EDIT)
    for before, after in [({}, {"timeout": 2}), ({"timeout": 1}, {"timeout": 3}),
                          ({"apply": 1}, {"apply": 1, "timeout": 2})]:
        oracle = ScriptedOracle({render_patch(patch)})
        result = refine_lines(patch, _result(unresolved=dict(before)), oracle)
        # Keeping only the "-" line or only the "+" line: two distinct texts.
        assert oracle.runs == 2
        assert result.unresolved == after


def test_line_pass_skips_a_whole_file_deletion():
    patch = parse_patch(DELETE)
    oracle = ScriptedOracle(set())
    result = refine_lines(patch, _result(), oracle)
    assert oracle.runs == 0
    assert [lv.needed for lv in result.line_verdicts] == [True, True]
    assert result.minimal_patch == render_patch(patch)


def test_confirm_does_not_run_after_an_error():
    oracle = ScriptedOracle(set())
    result = confirm_result(_result(needed=[], error="full patch fails"), oracle, 2)
    assert result.confirm_outcomes == []
    assert result.confirmed is None
