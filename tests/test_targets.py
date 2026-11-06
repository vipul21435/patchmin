import subprocess
import sys
from pathlib import Path

import pytest

from patchmin.cli import main
from patchmin.core import pinned_hunks
from patchmin.diff import parse_patch
from patchmin.oracle import WorktreeOracle
from patchmin.targets import find_targets, node_id, parse_junit, pinned_test_files

JUNIT = """<?xml version="1.0" encoding="utf-8"?>
<testsuites><testsuite name="pytest">
<testcase classname="tests.test_a" name="test_ok" file="tests/test_a.py" line="1"/>
<testcase classname="tests.test_a" name="test_bad" file="tests/test_a.py" line="3">
  <failure message="assert 1 == 2">x</failure></testcase>
<testcase classname="tests.test_a.TestK" name="test_m" file="tests/test_a.py" line="9">
  <error message="boom">y</error></testcase>
<testcase classname="tests.test_a" name="test_skip" file="tests/test_a.py" line="5">
  <skipped message="no"/></testcase>
<testcase classname="tests.test_a" name="test_sub" file="tests/test_a.py" line="7">
  <failure message="subtest">z</failure></testcase>
<testcase classname="tests.test_a" name="test_sub" file="tests/test_a.py" line="7"/>
</testsuite></testsuites>
"""


def test_parse_junit_statuses():
    got = parse_junit(JUNIT)
    assert got == {
        "tests/test_a.py::test_ok": "passed",
        "tests/test_a.py::test_bad": "failed",
        "tests/test_a.py::TestK::test_m": "failed",
        "tests/test_a.py::test_skip": "skipped",
        "tests/test_a.py::test_sub": "failed",
    }


def test_node_id_without_matching_module():
    assert node_id("t.py", "other.Thing", "test_x") == "t.py::test_x"
    assert node_id("pkg/t.py", "pkg.t.C", "test_x[1]") == "pkg/t.py::C::test_x[1]"


LIB = "def double(x):\n    return x * 2\n\n\ndef half(x):\n    return x / 2\n"


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    ).stdout.strip()


@pytest.fixture
def pytest_repo(tmp_path: Path) -> dict:
    repo = tmp_path / "p"
    (repo / "tests").mkdir(parents=True)
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "fixture@example.invalid")
    _git(repo, "config", "user.name", "Fixture")
    (repo / "lib.py").write_text(LIB.replace("x * 2", "x + 2"))
    (repo / "README.md").write_text("# lib\n")
    (repo / "tests" / "test_lib.py").write_text(
        "import lib\n\n\ndef test_half():\n    assert lib.half(4) == 2\n"
    )
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "base")
    base = _git(repo, "rev-parse", "HEAD")
    (repo / "lib.py").write_text(LIB)
    (repo / "README.md").write_text("# lib\n\nDoubles and halves.\n")
    (repo / "tests" / "test_lib.py").write_text(
        "import lib\n\n\ndef test_half():\n    assert lib.half(4) == 2\n\n\n"
        "def test_double():\n    assert lib.double(5) == 10\n\n\n"
        "class TestMore:\n    def test_zero(self):\n        assert lib.double(0) == 0\n\n"
        "    def test_three(self):\n        assert lib.double(3) == 6\n"
    )
    _git(repo, "commit", "-qam", "fix")
    fix = _git(repo, "rev-parse", "HEAD")
    cmd = f"{sys.executable} -m pytest -q -p no:cacheprovider"
    return {"repo": repo, "base": base, "fix": fix, "test_cmd": cmd}


def test_find_targets_returns_fail_to_pass(pytest_repo):
    fx = pytest_repo
    patch = parse_patch(_git(fx["repo"], "diff", fx["base"], fx["fix"]) + "\n")
    pinned = pinned_hunks(patch, ["tests/*"])
    assert pinned_test_files(patch, pinned) == ["tests/test_lib.py"]
    with WorktreeOracle(fx["repo"], fx["base"], fx["test_cmd"]) as oracle:
        report = find_targets(patch, pinned, oracle)
    assert report.error is None
    assert report.targets == [
        "tests/test_lib.py::TestMore::test_three",
        "tests/test_lib.py::TestMore::test_zero",
        "tests/test_lib.py::test_double",
    ]
    assert report.before["tests/test_lib.py::test_half"] == "passed"
    assert report.broken == []


def test_find_targets_reports_a_patch_that_does_not_apply(pytest_repo):
    fx = pytest_repo
    patch = parse_patch("--- a/lib.py\n+++ b/lib.py\n@@ -1 +1 @@\n-nope\n+x\n")
    with WorktreeOracle(fx["repo"], fx["base"], fx["test_cmd"]) as oracle:
        report = find_targets(patch, {0}, oracle, paths=[])
    assert report.targets == []
    assert report.error == "test run before the fix failed: apply"


def test_cli_find_targets(pytest_repo, capsys):
    fx = pytest_repo
    code = main([
        "--repo", str(fx["repo"]), "--base", fx["base"], "--fix", fx["fix"],
        "--test-cmd", fx["test_cmd"], "--pin", "tests/*", "--find-targets",
    ])
    out = capsys.readouterr().out
    assert code == 0, out
    assert "targets (3 fail before the fix, pass after):" in out
    assert "1 of 2 hunks needed, 1 pinned" in out
    assert "minimal patch confirmed: pass" in out


def test_cli_find_targets_without_pin(pytest_repo, capsys):
    # Without --pin the new tests do not exist before the fix, count as
    # failing there, and the test file's hunk is searched like the others.
    fx = pytest_repo
    code = main([
        "--repo", str(fx["repo"]), "--base", fx["base"], "--fix", fx["fix"],
        "--test-cmd", fx["test_cmd"], "--find-targets",
    ])
    out = capsys.readouterr().out
    assert code == 0, out
    assert "targets (3 fail before the fix, pass after):" in out
    assert "2 of 3 hunks needed" in out


def test_cli_find_targets_when_nothing_changes(pytest_repo, capsys):
    fx = pytest_repo
    code = main([
        "--repo", str(fx["repo"]), "--base", fx["base"], "--fix", fx["fix"],
        "--test-cmd", fx["test_cmd"], "--pin", "*.md", "--pin", "tests/*", "--pin", "lib.py",
        "--find-targets",
    ])
    out = capsys.readouterr().out
    assert code == 1
    assert out.startswith("error: no test fails before the fix and passes after")


def test_cli_warns_about_tests_the_fix_breaks(pytest_repo, capsys):
    fx = pytest_repo
    repo = fx["repo"]
    # A second commit on top of the fix breaks half(); diff base..HEAD has both.
    (repo / "lib.py").write_text(LIB.replace("x / 2", "x / 3"))
    _git(repo, "commit", "-qam", "break half")
    code = main([
        "--repo", str(repo), "--base", fx["base"], "--fix", "HEAD",
        "--test-cmd", fx["test_cmd"], "--pin", "tests/*", "--find-targets",
    ])
    out = capsys.readouterr().out
    assert code == 0, out
    assert "warning: 1 tests pass before the fix and fail after" in out
