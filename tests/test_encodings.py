"""Line endings and bytes that str.splitlines or UTF-8 text mode would mangle."""

import subprocess
import sys

import pytest
from conftest import make_repo

from patchmin.cli import main
from patchmin.diff import parse_patch, render_patch
from patchmin.oracle import run_command

CHECK = f'{sys.executable} -c "import m; assert m.x == 2"'


def _main(fx, *extra, check=CHECK):
    return main(
        ["--repo", str(fx["repo"]), "--base", fx["base"], "--fix", fx["fix"],
         "--test-cmd", check, *extra]
    )


@pytest.mark.parametrize(
    ("before", "after"),
    [
        (b"x = 1\r\ny = 1\r\n", b"x = 2\r\ny = 2\r\n"),
        (b"x = 1\n\x0c\ny = 1\n", b"x = 2\n\x0c\ny = 2\n"),
        ('s = "a b"\nx = 1\n'.encode(), 's = "a b"\nx = 2\n'.encode()),
        (b"x = 1\n\x1c\x1d\x1e\xc2\x85\n", b"x = 2\n\x1c\x1d\x1e\xc2\x85\n"),
    ],
    ids=["crlf", "form-feed", "line-separator", "other-separators"],
)
def test_line_breaks_inside_lines_survive(tmp_path, capsys, before, after):
    if before.startswith(b"x = 1\n\x1c"):
        # Not valid Python; keep m importable by moving the bytes to a data file.
        fx = make_repo(
            tmp_path, {"m.py": "x = 1\n", "d.txt": before}, {"m.py": "x = 2\n", "d.txt": after}
        )
    else:
        fx = make_repo(tmp_path, {"m.py": before}, {"m.py": after})
    code = _main(fx, "--lines")
    out = capsys.readouterr().out
    assert code == 0, out
    assert "minimal patch confirmed: pass" in out


def test_non_utf8_file_in_the_fix(tmp_path, capsys):
    fx = make_repo(
        tmp_path,
        {"m.py": "x = 1\n", "data.txt": b"caf\xe9\n"},
        {"m.py": "x = 2\n", "data.txt": b"caf\xe9!\n"},
    )
    out_file = tmp_path / "min.diff"
    check = (
        f"{sys.executable} -c \"import m; "
        f"assert m.x == 2 and open('data.txt', 'rb').read() == b'caf\\xe9!\\n'\""
    )
    code = _main(
        fx, "--lines", "--out", str(out_file), "--markdown", str(tmp_path / "r.md"), check=check
    )
    out = capsys.readouterr().out
    assert code == 0, out
    assert "2 of 2 hunks needed" in out
    assert b"caf\xe9!" in out_file.read_bytes()
    subprocess.run(["git", "-C", str(fx["repo"]), "checkout", "-q", fx["base"]], check=True)
    check = subprocess.run(
        ["git", "-C", str(fx["repo"]), "apply", "--check", str(out_file)], capture_output=True
    )
    assert check.returncode == 0, check.stderr


def test_parse_keeps_carriage_returns():
    text = "--- a/m\n+++ b/m\n@@ -1 +1 @@\n-x\r\n+y\r\n"
    patch = parse_patch(text)
    assert patch.hunks[0].lines == ["-x\r", "+y\r"]
    assert render_patch(patch).endswith("-x\r\n+y\r\n")


def test_non_utf8_test_output_is_replaced(tmp_path):
    script = "import sys; sys.stdout.buffer.write(b'ok \\xff\\xfe'); sys.exit(1)"
    code, out = run_command([sys.executable, "-c", script], tmp_path, 10)
    assert code == 1
    assert out.startswith("ok ")
