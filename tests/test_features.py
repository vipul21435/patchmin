import subprocess
import sys
from pathlib import Path

import pytest

from patchmin.cli import main
from patchmin.core import minimize_text
from patchmin.diff import Hunk, classify_hunk, matches_any
from patchmin.oracle import WorktreeOracle


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    ).stdout.strip()


def _diff(fx) -> str:
    return _git(fx["repo"], "diff", fx["base"], fx["fix"]) + "\n"


@pytest.fixture
def repo_with_test(tmp_path: Path) -> dict:
    """A fix commit that changes the code, a README and adds the test that
    checks it. Without the test file the check has nothing to run."""
    repo = tmp_path / "r"
    (repo / "tests").mkdir(parents=True)
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "fixture@example.invalid")
    _git(repo, "config", "user.name", "Fixture")
    (repo / "lib.py").write_text("def double(x):\n    return x + 2\n")
    (repo / "README.md").write_text("# lib\n\nAdds two.\n")
    (repo / "tests" / "check_lib.py").write_text("import lib\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "base")
    base = _git(repo, "rev-parse", "HEAD")
    (repo / "lib.py").write_text("def double(x):\n    return x * 2\n")
    (repo / "README.md").write_text("# lib\n\nDoubles.\n")
    (repo / "tests" / "check_lib.py").write_text("import lib\nassert lib.double(5) == 10\n")
    _git(repo, "commit", "-qam", "fix")
    fix = _git(repo, "rev-parse", "HEAD")
    run = "import sys, runpy; sys.path.insert(0, '.'); runpy.run_path('tests/check_lib.py')"
    cmd = f'{sys.executable} -c "{run}"'
    return {"repo": repo, "base": base, "fix": fix, "test_cmd": cmd}


def test_parallel_jobs_give_the_sequential_result(fixture_repo):
    text = _diff(fixture_repo)
    args = (fixture_repo["repo"], fixture_repo["base"], fixture_repo["test_cmd"])
    with WorktreeOracle(*args) as one:
        seq = minimize_text(text, one, lines=True)
    with WorktreeOracle(*args, jobs=3) as three:
        par = minimize_text(text, three, lines=True)
        assert len(three._worktrees) == 3
    assert par.needed == seq.needed == [1]
    assert [v.needed for v in par.line_verdicts] == [v.needed for v in seq.line_verdicts]
    assert par.minimal_patch == seq.minimal_patch


def test_worktrees_are_removed_on_close(fixture_repo):
    args = (fixture_repo["repo"], fixture_repo["base"], fixture_repo["test_cmd"])
    with WorktreeOracle(*args, jobs=2) as oracle:
        paths = list(oracle._worktrees)
        assert all(p.exists() for p in paths)
        root = paths[0].parent
    assert not any(p.exists() for p in paths)
    assert not root.exists()
    assert len(_git(fixture_repo["repo"], "worktree", "list").splitlines()) == 1


def test_a_given_work_dir_is_kept_on_close(fixture_repo, tmp_path):
    args = (fixture_repo["repo"], fixture_repo["base"], fixture_repo["test_cmd"])
    work = tmp_path / "work"
    with WorktreeOracle(*args, work_dir=work) as oracle:
        assert oracle._worktrees[0].parent == work
    assert work.is_dir() and not any(work.iterdir())


def test_pinned_test_hunks_are_always_applied(repo_with_test):
    fx = repo_with_test
    with WorktreeOracle(fx["repo"], fx["base"], fx["test_cmd"]) as oracle:
        result = minimize_text(_diff(fx), oracle, pin=["tests/*"])
    assert result.error is None
    by_kind = {v.kind: v for v in result.verdicts}
    assert by_kind["test"].status == "pinned"
    assert by_kind["docs"].status == "not needed"
    assert by_kind["code"].status == "needed"
    assert result.pinned == [by_kind["test"].index]
    assert result.needed == [by_kind["code"].index]
    assert "assert lib.double(5) == 10" in result.minimal_patch
    assert "Doubles" not in result.minimal_patch


def test_without_pin_the_test_hunk_is_searched(repo_with_test):
    fx = repo_with_test
    with WorktreeOracle(fx["repo"], fx["base"], fx["test_cmd"]) as oracle:
        result = minimize_text(_diff(fx), oracle)
    # The base test file only imports lib, so it passes with no patch at all.
    assert result.error == "target tests already pass without the patch"


def test_every_hunk_pinned_is_an_error(repo_with_test):
    fx = repo_with_test
    with WorktreeOracle(fx["repo"], fx["base"], fx["test_cmd"]) as oracle:
        result = minimize_text(_diff(fx), oracle, pin=["*"])
    assert result.error == "every hunk is pinned; nothing to search"


def test_pinned_alone_passing_is_reported(repo_with_test):
    fx = repo_with_test
    with WorktreeOracle(fx["repo"], fx["base"], fx["test_cmd"]) as oracle:
        result = minimize_text(_diff(fx), oracle, pin=["lib.py"])
    assert result.error == "target tests already pass with the pinned hunks alone"


def test_cli_pin_and_jobs(repo_with_test, capsys):
    fx = repo_with_test
    code = main([
        "--repo", str(fx["repo"]), "--base", fx["base"], "--fix", fx["fix"],
        "--test-cmd", fx["test_cmd"], "--pin", "tests/*", "--jobs", "2",
    ])
    out = capsys.readouterr().out
    assert code == 0
    assert "1 of 2 hunks needed, 1 pinned" in out
    assert "pinned      hunk" in out


def _hunk(path: str, lines: list[str]) -> Hunk:
    return Hunk(0, path, path, 1, 1, 1, 1, lines)


@pytest.mark.parametrize(
    ("path", "lines", "kind"),
    [
        ("README.md", ["-a", "+b"], "docs"),
        ("docs/api.txt", ["-a", "+b"], "docs"),
        ("doc/index.html", ["-a", "+b"], "docs"),
        ("tests/test_x.py", ["-a", "+b"], "test"),
        ("pkg/test_x.py", ["-a", "+b"], "test"),
        ("pkg/x_test.py", ["-a", "+b"], "test"),
        ("pkg/x.py", ["-x = 1", "+x  =  1"], "whitespace"),
        ("pkg/x.py", ["-# old", "+# new"], "comment"),
        ("pkg/x.py", [" def f():", '     """Doc."""', "-    return 1", "+    return 2"], "code"),
        ("pkg/x.py", [' """', '-old words', '+new words', ' """'], "comment"),
        ("pkg/x.py", ['-    """Old."""', '+    """New."""'], "comment"),
        ("pkg/x.py", ["-x = 1", "+x = 2"], "code"),
        # Starts inside a docstring: the bare fence closes it, the change is code.
        ("pkg/x.py", ["     [[1, 2]]", '     """', "-    yield x", "+    yield x + 1"], "code"),
        ("pkg/x.py", ["     text.", '     """', "     if n:", "-        a = 1", "+        a = 2"],
         "code"),
        ("pkg/x.py", ["-    more text", "+    other text", '     """'], "comment"),
        ("pkg/x.py", [" ", '     """', "     if n:", "-        a = 1", "+        a = 2"], "code"),
        ("pkg/x.py", [" def f():", '     """', "-    old", "+    new", '     """'], "comment"),
        ("pkg/x.py", ["-# note", "+x = 2"], "code"),
        ("pkg/x.py", ["-    total = a + b", "-    return total", "+    s = a + b", "+    return s"],
         "rename"),
        ("pkg/x.py", ["-def f(xs):", "+def f(items):"], "rename"),
        ("pkg/x.py", ["-    return a - b", "+    return a + b"], "code"),
        ("pkg/x.py", ["-    x = 1", "+    y = 2"], "code"),
        ("pkg/x.py", ["-    f(a, b)", "+    f(b, b)"], "code"),
        ("pkg/x.py", ["-    return a", "+    return 0 - a"], "code"),
    ],
)
def test_classify_hunk(path, lines, kind):
    assert classify_hunk(_hunk(path, lines)) == kind


def test_matches_any():
    assert matches_any("tests/test_a.py", ["tests/*"])
    assert matches_any("pkg/tests/test_a.py", ["tests"])
    assert matches_any("pkg/tests/test_a.py", ["test_*.py"])
    assert matches_any("docs/x/y.rst", ["docs/"])
    assert not matches_any("pkg/a.py", ["tests/*", "*.md"])


@pytest.fixture
def close_changes(tmp_path: Path) -> dict:
    """Two changes one line apart: git diff -U3 merges them into one hunk."""
    repo = tmp_path / "c"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "fixture@example.invalid")
    _git(repo, "config", "user.name", "Fixture")
    (repo / "m.py").write_text("# old note\nX = 1\nY = 0\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "base")
    base = _git(repo, "rev-parse", "HEAD")
    (repo / "m.py").write_text("# new note\nX = 1\nY = 2\n")
    _git(repo, "commit", "-qam", "fix")
    fix = _git(repo, "rev-parse", "HEAD")
    cmd = f'{sys.executable} -c "import m; assert m.Y == 2"'
    return {"repo": repo, "base": base, "fix": fix, "test_cmd": cmd}


@pytest.mark.parametrize(("context", "summary"), [(3, "1 of 1 hunks"), (0, "1 of 2 hunks")])
def test_context_controls_hunk_size(close_changes, tmp_path, capsys, context, summary):
    fx = close_changes
    out = tmp_path / "min.diff"
    code = main([
        "--repo", str(fx["repo"]), "--base", fx["base"], "--fix", fx["fix"],
        "--test-cmd", fx["test_cmd"], "--context", str(context), "--out", str(out),
    ])
    text = capsys.readouterr().out
    assert code == 0, text
    assert f"{summary} needed" in text
    assert "minimal patch confirmed: pass" in text
    if context == 0:
        assert "new note" not in out.read_text()
