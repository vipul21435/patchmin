import sys
import time

from patchmin.cache import RunCache
from patchmin.oracle import Outcome, WorktreeOracle, run_command


def test_run_command_returns_code_and_output(tmp_path):
    script = "print('hi'); raise SystemExit(3)"
    code, out = run_command([sys.executable, "-c", script], tmp_path, 10)
    assert code == 3
    assert "hi" in out


def test_timeout_kills_child_processes(tmp_path):
    marker = tmp_path / "late"
    # The parent starts a child that would write the marker after one second,
    # then waits on it. Both must be gone when the timeout fires.
    child = f"import time, pathlib; time.sleep(1.0); pathlib.Path({str(marker)!r}).write_text('x')"
    parent = (
        "import subprocess, sys; "
        f"subprocess.run([sys.executable, '-c', {child!r}])"
    )
    t0 = time.monotonic()
    code, _ = run_command([sys.executable, "-c", parent], tmp_path, 0.3)
    assert code is None
    assert time.monotonic() - t0 < 1.0
    time.sleep(1.2)
    assert not marker.exists()


def test_timeout_reason_is_recorded_and_cached(fixture_repo, tmp_path):
    cmd = f'{sys.executable} -I -c "import time; time.sleep(5)"'
    cache = RunCache(tmp_path / "c.json")
    with WorktreeOracle(
        fixture_repo["repo"], fixture_repo["base"], cmd, timeout=0.3, cache=cache
    ) as oracle:
        first = oracle.run("")
        again = oracle.run("")
    assert (first.outcome, first.reason) == (Outcome.UNRESOLVED, "timeout")
    assert again.cached and again.reason == "timeout"
    assert oracle.runs == 1


def test_longer_timeout_ignores_a_cached_timeout(fixture_repo, tmp_path):
    path = tmp_path / "c.json"
    cmd = f'{sys.executable} -I -c "import time; time.sleep(0.6)"'
    args = (fixture_repo["repo"], fixture_repo["base"], cmd)
    with WorktreeOracle(*args, timeout=0.2, cache=RunCache(path)) as short:
        assert short.run("").reason == "timeout"
    with WorktreeOracle(*args, timeout=10, cache=RunCache(path)) as longer:
        result = longer.run("")
    assert result.outcome is Outcome.PASS and not result.cached
    assert longer.runs == 1


def test_apply_failure_reason(fixture_repo):
    bad = "--- a/calc.py\n+++ b/calc.py\n@@ -1 +1 @@\n-nothing like this\n+x\n"
    with WorktreeOracle(
        fixture_repo["repo"], fixture_repo["base"], fixture_repo["test_cmd"]
    ) as oracle:
        result = oracle.run(bad)
    assert (result.outcome, result.reason) == (Outcome.UNRESOLVED, "apply")


def test_stable_outcome_survives_reruns(fixture_repo):
    with WorktreeOracle(
        fixture_repo["repo"], fixture_repo["base"], fixture_repo["test_cmd"], reruns=2
    ) as oracle:
        result = oracle.run("")
    assert result.outcome is Outcome.FAIL and not result.flaky
    assert oracle.runs == 3


def test_run_many_runs_duplicates_once_and_keeps_order(fixture_repo):
    with WorktreeOracle(
        fixture_repo["repo"], fixture_repo["base"], fixture_repo["test_cmd"], jobs=2
    ) as oracle:
        results = oracle.run_many(["", "", ""])
        assert oracle.runs == 1
        assert [r.outcome for r in results] == [Outcome.FAIL] * 3
        assert oracle.run_many([""])[0].cached
