"""The diff made for --fix must not depend on the user's git config."""

import sys

from conftest import make_repo

from patchmin.cli import main


def test_fix_diff_ignores_noprefix_and_external_diff(tmp_path, monkeypatch, capsys):
    fx = make_repo(
        tmp_path,
        {"calc.py": "def add(a, b):\n    return a - b\n", "notes.txt": "a\n"},
        {"calc.py": "def add(a, b):\n    return a + b\n", "notes.txt": "b\n"},
    )
    config = tmp_path / "gitconfig"
    config.write_text(
        "[diff]\n\tnoprefix = true\n\texternal = false\n\tmnemonicPrefix = true\n"
    )
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    check = "import calc; assert calc.add(2, 3) == 5"
    code = main(
        [
            "--repo", str(fx["repo"]), "--base", fx["base"], "--fix", fx["fix"],
            "--test-cmd", f'{sys.executable} -c "{check}"',
        ]
    )
    captured = capsys.readouterr()
    assert code == 0, captured.err
    assert "1 of 2 hunks needed" in captured.out
