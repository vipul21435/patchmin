import json
from pathlib import Path

from patchmin.cache import RunCache
from patchmin.cli import main
from patchmin.core import minimize_text
from patchmin.oracle import Outcome, WorktreeOracle


def _diff(fixture_repo):
    import subprocess

    return subprocess.run(
        ["git", "-C", str(fixture_repo["repo"]), "diff", fixture_repo["base"], fixture_repo["fix"]],
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def test_minimizes_to_the_one_needed_hunk(fixture_repo):
    with WorktreeOracle(
        fixture_repo["repo"], fixture_repo["base"], fixture_repo["test_cmd"]
    ) as oracle:
        result = minimize_text(_diff(fixture_repo), oracle)
    assert result.error is None
    assert result.needed == [1]
    assert [v.needed for v in result.verdicts] == [False, True, False, False]
    assert "a + b" in result.minimal_patch
    assert "documented" not in result.minimal_patch
    assert result.runs == result.trace.tests + 2


def test_cache_avoids_repeat_runs(fixture_repo, tmp_path):
    cache_path = tmp_path / "cache.json"
    text = _diff(fixture_repo)
    with WorktreeOracle(
        fixture_repo["repo"], fixture_repo["base"], fixture_repo["test_cmd"],
        cache=RunCache(cache_path),
    ) as oracle:
        first = minimize_text(text, oracle)
    assert first.cached_hits == 0
    with WorktreeOracle(
        fixture_repo["repo"], fixture_repo["base"], fixture_repo["test_cmd"],
        cache=RunCache(cache_path),
    ) as oracle:
        second = minimize_text(text, oracle)
    assert second.needed == first.needed
    assert second.runs == 0
    assert second.cached_hits > 0


def test_unresolved_when_patch_does_not_apply(fixture_repo):
    bad = "--- a/calc.py\n+++ b/calc.py\n@@ -1 +1 @@\n-nothing like this\n+x\n"
    with WorktreeOracle(
        fixture_repo["repo"], fixture_repo["base"], fixture_repo["test_cmd"]
    ) as oracle:
        assert oracle.run(bad).outcome is Outcome.UNRESOLVED


def test_timeout_is_unresolved(fixture_repo):
    import sys

    cmd = f'{sys.executable} -I -c "import time; time.sleep(5)"'
    with WorktreeOracle(
        fixture_repo["repo"], fixture_repo["base"], cmd, timeout=0.3
    ) as oracle:
        assert oracle.run("").outcome is Outcome.UNRESOLVED


def test_error_when_full_patch_fails(fixture_repo):
    import sys

    cmd = f'{sys.executable} -I -c "raise SystemExit(1)"'
    with WorktreeOracle(fixture_repo["repo"], fixture_repo["base"], cmd) as oracle:
        result = minimize_text(_diff(fixture_repo), oracle)
    assert result.error == "full patch does not make the target tests pass"
    assert result.verdicts == []


def test_cli_json_and_out(fixture_repo, tmp_path, capsys):
    out = tmp_path / "min.diff"
    code = main(
        [
            "--repo", str(fixture_repo["repo"]),
            "--base", fixture_repo["base"],
            "--fix", fixture_repo["fix"],
            "--test-cmd", fixture_repo["test_cmd"],
            "--json",
            "--out", str(out),
        ]
    )
    assert code == 0
    data = json.loads(capsys.readouterr().out)
    assert data["needed"] == [1]
    assert Path(out).read_text().count("\n@@ -") == 1


def test_cli_text_output(fixture_repo, capsys):
    code = main(
        [
            "--repo", str(fixture_repo["repo"]),
            "--base", fixture_repo["base"],
            "--fix", fixture_repo["fix"],
            "--test-cmd", fixture_repo["test_cmd"],
        ]
    )
    assert code == 0
    text = capsys.readouterr().out
    assert "1 of 4 hunks needed" in text
    assert text.count("not needed") == 3
    assert all(line == line.rstrip() for line in text.splitlines())


def test_rest_cmd_keeps_hunks_the_rest_depends_on(fixture_repo):
    import sys

    # The rest of the suite insists on the new docstring, so hunk 0 is needed too.
    rest = f'{sys.executable} -c "import calc; assert \'documented\' in calc.__doc__"'
    with WorktreeOracle(
        fixture_repo["repo"], fixture_repo["base"], fixture_repo["test_cmd"], rest_cmd=rest
    ) as oracle:
        result = minimize_text(_diff(fixture_repo), oracle)
    assert result.error is None
    assert result.needed == [0, 1]


def test_reruns_flag_flaky_outcome(fixture_repo, tmp_path):
    import sys

    marker = tmp_path / "flip"
    script = tmp_path / "flaky.py"
    # Fails on the first call, passes on the second: the outcome is flaky.
    script.write_text(
        "import pathlib, sys\n"
        f"p = pathlib.Path({str(marker)!r})\n"
        "if p.exists():\n    sys.exit(0)\n"
        "p.write_text('x')\nsys.exit(1)\n"
    )
    cmd = f"{sys.executable} {script}"
    with WorktreeOracle(fixture_repo["repo"], fixture_repo["base"], cmd, reruns=1) as oracle:
        result = oracle.run("")
    assert result.flaky is True
    assert result.outcome is Outcome.UNRESOLVED


def test_module_entry_point_prints_version():
    import subprocess
    import sys

    proc = subprocess.run(
        [sys.executable, "-m", "patchmin", "--version"], capture_output=True, text=True
    )
    assert proc.returncode == 0
    assert proc.stdout.strip() == "patchmin 0.1.0"


def test_line_refinement_drops_the_comment_change(fixture_repo):
    with WorktreeOracle(
        fixture_repo["repo"], fixture_repo["base"], fixture_repo["test_cmd"]
    ) as oracle:
        result = minimize_text(_diff(fixture_repo), oracle, lines=True)
    assert result.needed == [1]
    kept = [v.text for v in result.line_verdicts if v.needed]
    dropped = [v.text for v in result.line_verdicts if not v.needed]
    assert kept == ["-    return a - b", "+    return a + b"]
    assert all("adds two numbers" in t for t in dropped) and len(dropped) == 2
    assert "now correctly" not in result.minimal_patch
    assert "# adds two numbers" in result.minimal_patch
    assert result.runs > result.trace.tests - 2 or result.cached_hits > 0


def test_confirm_runs_bypass_the_cache(fixture_repo):
    with WorktreeOracle(
        fixture_repo["repo"], fixture_repo["base"], fixture_repo["test_cmd"]
    ) as oracle:
        result = minimize_text(_diff(fixture_repo), oracle, lines=True, confirm=2)
        runs_before_confirm = result.trace.tests + 2 - oracle.cache.hits
    assert result.confirm_outcomes == ["pass", "pass"]
    assert result.confirmed is True
    assert result.runs == runs_before_confirm + 2
    assert result.to_dict()["confirmed"] is True


def test_confirm_catches_an_answer_that_stops_passing(fixture_repo, tmp_path, capsys):
    import sys

    # Behaves like the real check for the 5 calls the search makes on this
    # fixture (2 pre-checks, 3 ddmin trials), then always fails:
    # a cached pass that no longer holds when the answer is run again.
    counter = tmp_path / "count"
    script = tmp_path / "check.py"
    script.write_text(
        "import pathlib, sys\n"
        f"p = pathlib.Path({str(counter)!r})\n"
        "n = int(p.read_text()) if p.exists() else 0\n"
        "p.write_text(str(n + 1))\n"
        "if n >= 5:\n    sys.exit(1)\n"
        "sys.path.insert(0, '.')\n"
        "import calc\n"
        "sys.exit(0 if calc.add(2, 3) == 5 else 1)\n"
    )
    code = main([
        "--repo", str(fixture_repo["repo"]), "--base", fixture_repo["base"],
        "--fix", fixture_repo["fix"], "--test-cmd", f"{sys.executable} {script}",
        "--confirm", "2",
    ])
    out = capsys.readouterr().out
    assert code == 2
    assert "1 of 4 hunks needed" in out
    assert "minimal patch NOT confirmed: fail, fail" in out


def test_confirm_zero_skips(fixture_repo):
    with WorktreeOracle(
        fixture_repo["repo"], fixture_repo["base"], fixture_repo["test_cmd"]
    ) as oracle:
        result = minimize_text(_diff(fixture_repo), oracle, confirm=0)
    assert result.confirm_outcomes == [] and result.confirmed is None


def test_public_api_exports():
    import patchmin

    for name in patchmin.__all__:
        assert hasattr(patchmin, name), name
    assert patchmin.__version__ == "0.1.0"
