"""Trials in one worktree must not see each other's leftovers."""

import sys

from conftest import make_repo

from patchmin.cli import main


def test_stale_bytecode_does_not_leak_between_trials(tmp_path, capsys):
    # A same-size edit restored within the same second: CPython would trust a
    # __pycache__ entry left by the previous trial, since it only checks the
    # source mtime (whole seconds) and size.
    fx = make_repo(
        tmp_path,
        {
            ".gitignore": "__pycache__/\n",
            "calc.py": "def add(a, b):\n    return a - b\n",
            "other.py": "X = 1\n",
        },
        {"calc.py": "def add(a, b):\n    return a + b\n", "other.py": "X = 22\n"},
    )
    check = "import calc, other; assert calc.add(2, 3) == 5 and other.X == 22"
    code = main(
        [
            "--repo", str(fx["repo"]), "--base", fx["base"], "--fix", fx["fix"],
            "--confirm", "1", "--test-cmd", f'{sys.executable} -c "{check}"',
        ]
    )
    out = capsys.readouterr().out
    assert code == 0, out
    assert "2 of 2 hunks needed" in out


def _leftover_cmd() -> str:
    # Fails when an ignored file from an earlier trial is still there.
    script = (
        "import pathlib, sys; p = pathlib.Path('out.log'); "
        "sys.exit(1 if p.exists() else p.write_text('x') and 0)"
    )
    return f'{sys.executable} -c "{script}"'


def test_ignored_files_persist_unless_clean_ignored(tmp_path):
    from patchmin.oracle import Outcome, WorktreeOracle

    fx = make_repo(tmp_path, {".gitignore": "*.log\n", "a.txt": "1\n"}, {"a.txt": "2\n"})
    with WorktreeOracle(fx["repo"], fx["base"], _leftover_cmd()) as oracle:
        outcomes = [r.outcome for r in oracle.confirm("", 2)]
    assert outcomes == [Outcome.PASS, Outcome.FAIL]
    with WorktreeOracle(fx["repo"], fx["base"], _leftover_cmd(), clean_ignored=True) as oracle:
        outcomes = [r.outcome for r in oracle.confirm("", 2)]
    assert outcomes == [Outcome.PASS, Outcome.PASS]


def test_bytecode_written_by_the_command_is_deleted_before_the_next_trial(tmp_path, capsys):
    # The command turns bytecode writing back on, so PYTHONDONTWRITEBYTECODE
    # does not help; only deleting the ignored __pycache__ keeps the trials
    # apart.
    fx = make_repo(
        tmp_path,
        {
            ".gitignore": "__pycache__/\n",
            "calc.py": "def add(a, b):\n    return a - b\n",
            "other.py": "X = 1\n",
        },
        {"calc.py": "def add(a, b):\n    return a + b\n", "other.py": "X = 22\n"},
    )
    check = (
        "import sys; sys.dont_write_bytecode = False; import calc, other; "
        "assert calc.add(2, 3) == 5 and other.X == 22"
    )
    code = main(
        [
            "--repo", str(fx["repo"]), "--base", fx["base"], "--fix", fx["fix"],
            "--confirm", "1", "--test-cmd", f'{sys.executable} -c "{check}"',
        ]
    )
    out = capsys.readouterr().out
    assert code == 0, out
    assert "2 of 2 hunks needed" in out


def test_commands_run_without_writing_bytecode(tmp_path):
    from patchmin.oracle import Outcome, WorktreeOracle, run_env

    assert run_env()["PYTHONDONTWRITEBYTECODE"] == "1"
    fx = make_repo(tmp_path, {".gitignore": "__pycache__/\n", "calc.py": "X = 1\n"},
                   {"calc.py": "X = 2\n"})
    with WorktreeOracle(fx["repo"], fx["base"], f'{sys.executable} -c "import calc"') as oracle:
        assert oracle.run("").outcome is Outcome.PASS
        assert oracle.worktree is not None
        assert not (oracle.worktree / "__pycache__").exists()
