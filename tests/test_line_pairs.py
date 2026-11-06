"""The line pass keeps a dropped ``-`` line next to its ``+`` partner."""

import itertools
import subprocess
import sys

from conftest import make_repo

from patchmin.cli import main
from patchmin.diff import change_lines, hunk_with_lines, parse_patch, render_patch, with_hunk


def test_replacement_block_needs_only_the_changed_pair(tmp_path, capsys):
    fx = make_repo(
        tmp_path,
        {"m.py": "def f():\n    a = 1\n    return a\n"},
        {"m.py": "def f():\n    a = 2\n    return a  # now two\n"},
    )
    check = f'{sys.executable} -c "import m; assert m.f() == 2"'
    code = main(
        ["--repo", str(fx["repo"]), "--base", fx["base"], "--fix", fx["fix"], "--lines",
         "--test-cmd", check]
    )
    out = capsys.readouterr().out
    assert code == 0, out
    assert "1 of 4 change lines needed" in out
    assert "not needed  hunk 0   line 4   +    return a  # now two" in out


def _diff(tmp_path, old: bytes, new: bytes) -> tuple:
    d = tmp_path / "d"
    d.mkdir()
    (d / "f").write_bytes(old)
    subprocess.run(["git", "init", "-q", str(d)], check=True)
    subprocess.run(["git", "-C", str(d), "add", "f"], check=True)
    (d / "f").write_bytes(new)
    text = subprocess.run(
        ["git", "-C", str(d), "diff", "--no-color"], capture_output=True, text=True, check=True
    ).stdout
    (d / "f").write_bytes(old)
    return d, parse_patch(text)


def _expected(old_lines, new_lines, minus_keep, plus_keep, noeol) -> bytes:
    """The file the pairing rule promises: pair k is the old line unless its
    removal is kept, then the new line if its addition is kept."""
    out = []
    for k in range(max(len(old_lines), len(new_lines))):
        if k < len(old_lines) and k not in minus_keep:
            out.append(old_lines[k])
        if k < len(new_lines) and k in plus_keep:
            out.append(new_lines[k])
    text = "\n".join(out)
    if out and out[-1] not in noeol:
        text += "\n"
    return text.encode()


def test_every_line_subset_applies_and_gives_the_paired_file(tmp_path):
    # Context "a", then three lines replaced by two, last lines without a newline.
    old = ["a", "b", "c", "last"]
    new = ["a", "B", "last2"]
    d, patch = _diff(tmp_path, "\n".join(old).encode(), "\n".join(new).encode())
    (hunk,) = patch.hunks
    positions = change_lines(hunk)
    minus = [i for i in positions if hunk.lines[i].startswith("-")]
    plus = [i for i in positions if hunk.lines[i].startswith("+")]
    for r in range(1, len(positions) + 1):
        for keep in itertools.combinations(positions, r):
            text = render_patch(with_hunk(patch, hunk_with_lines(hunk, set(keep))))
            subprocess.run(["git", "-C", str(d), "checkout", "-q", "--", "f"], check=True)
            proc = subprocess.run(
                ["git", "-C", str(d), "apply", "-"], input=text.encode(), capture_output=True
            )
            assert proc.returncode == 0, (keep, text, proc.stderr)
            got = (d / "f").read_bytes()
            want = _expected(
                old[1:], new[1:],
                {k for k, i in enumerate(minus) if i in keep},
                {k for k, i in enumerate(plus) if i in keep},
                {"last", "last2"},
            )
            assert got == b"a\n" + want, (keep, text)
