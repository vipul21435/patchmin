"""File entries without hunks: renames, empty files, mode changes, binaries."""

import subprocess
import sys

from conftest import make_repo

from patchmin.cli import main
from patchmin.diff import parse_patch, render_patch


def _run(fx, check, *extra):
    return main(
        [
            "--repo", str(fx["repo"]), "--base", fx["base"], "--fix", fx["fix"],
            "--test-cmd", f'{sys.executable} -c "{check}"', *extra,
        ]
    )


def _rename_repo(tmp_path, helpers_text):
    repo_files = {"util.py": "VALUE = 1\n" + "".join(f"# line {i}\n" for i in range(8))}
    fx = make_repo(
        tmp_path,
        {**repo_files, "app.py": "import util\nVALUE = util.VALUE\n"},
        {"app.py": "import helpers\nVALUE = helpers.VALUE\n"},
    )
    repo = fx["repo"]
    subprocess.run(["git", "-C", str(repo), "reset", "-q", fx["base"]], check=True)
    subprocess.run(["git", "-C", str(repo), "checkout", "-q", "--", "."], check=True)
    subprocess.run(["git", "-C", str(repo), "mv", "util.py", "helpers.py"], check=True)
    (repo / "helpers.py").write_text(helpers_text(repo_files["util.py"]))
    (repo / "app.py").write_text("import helpers\nVALUE = helpers.VALUE\n")
    subprocess.run(["git", "-C", str(repo), "commit", "-qam", "rename"], check=True)
    fix = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True,
        check=True,
    ).stdout.strip()
    return {**fx, "fix": fix}


CHECK = "import sys, app; assert 'helpers' in sys.modules and app.VALUE == 1"


def test_pure_rename_from_fix_is_searched(tmp_path, capsys):
    fx = _rename_repo(tmp_path, lambda text: text)
    code = _run(fx, CHECK)
    out = capsys.readouterr().out
    assert code == 0, out
    # app.py and the new helpers.py are needed; deleting util.py is not.
    assert "2 of 3 hunks needed" in out
    assert "not needed  hunk 2" in out


def test_rename_with_edit_does_not_mark_the_edit_needed(tmp_path, capsys):
    fx = _rename_repo(tmp_path, lambda text: '"""Helpers."""\n' + text)
    check = "import app, helpers; assert app.helpers is helpers and helpers.VALUE == 1"
    code = _run(fx, check, "--lines")
    out = capsys.readouterr().out
    assert code == 0, out
    assert "2 of 3 hunks needed" in out
    assert 'not needed  hunk 1   line 0   +"""Helpers."""' in out
    assert "needed      hunk 1   line 1   +VALUE = 1" in out


def test_rename_in_patch_file_stops_with_an_error(tmp_path, capsys):
    fx = _rename_repo(tmp_path, lambda text: '"""Helpers."""\n' + text)
    diff = subprocess.run(
        ["git", "-C", str(fx["repo"]), "diff", "-M", fx["base"], fx["fix"]],
        capture_output=True, text=True, check=True,
    ).stdout
    assert "rename from util.py" in diff
    patch_file = tmp_path / "fix.diff"
    patch_file.write_text(diff)
    code = main(
        [
            "--repo", str(fx["repo"]), "--base", fx["base"], "--patch", str(patch_file),
            "--test-cmd", f'{sys.executable} -c "{CHECK}"',
        ]
    )
    err = capsys.readouterr().err
    assert code == 1
    assert "renames or copies util.py" in err and "--no-renames" in err


def test_new_empty_file_is_kept_in_every_trial(tmp_path, capsys):
    fx = make_repo(
        tmp_path,
        {"calc.py": "def add(a, b):\n    return a - b\n"},
        {"calc.py": "def add(a, b):\n    return a + b\n", "marker": ""},
    )
    check = "import os, calc; assert os.path.exists('marker') and calc.add(2, 3) == 5"
    code = _run(fx, check)
    out = capsys.readouterr().out
    assert code == 0, out
    assert "1 of 1 hunks needed" in out


def test_header_only_entries_render_in_every_subset():
    text = (
        "diff --git a/run.sh b/run.sh\n"
        "old mode 100644\n"
        "new mode 100755\n"
        "diff --git a/empty.txt b/empty.txt\n"
        "new file mode 100644\n"
        "index 0000000..e69de29\n"
        "diff --git a/a.py b/a.py\n"
        "old mode 100644\n"
        "new mode 100755\n"
        "index 1111111..2222222\n"
        "--- a/a.py\n"
        "+++ b/a.py\n"
        "@@ -1 +1 @@\n"
        "-x = 1\n"
        "+x = 2\n"
    )
    patch = parse_patch(text)
    assert [(f.new_path, f.header_only) for f in patch.files] == [
        ("run.sh", True), ("empty.txt", True), ("a.py", False),
    ]
    assert patch.unsupported() is None
    empty = render_patch(patch, set())
    assert empty == (
        "diff --git a/run.sh b/run.sh\nold mode 100644\nnew mode 100755\n"
        "diff --git a/empty.txt b/empty.txt\nnew file mode 100644\nindex 0000000..e69de29\n"
        "diff --git a/a.py b/a.py\nold mode 100644\nnew mode 100755\n"
    )
    assert render_patch(patch) == text


def test_binary_change_is_unsupported():
    text = (
        "diff --git a/logo.png b/logo.png\n"
        "index 1111111..2222222 100644\n"
        "Binary files a/logo.png and b/logo.png differ\n"
    )
    patch = parse_patch(text)
    assert patch.unsupported() == (
        "the patch has a binary change to logo.png, which is not supported"
    )
