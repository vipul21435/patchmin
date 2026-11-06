"""Every subset of hunks, and every subset of lines in a hunk, renders to a
diff that ``git apply --check`` accepts on the base commit."""

import itertools
import subprocess

import pytest

from patchmin.diff import (
    change_lines,
    hunk_with_lines,
    lines_searchable,
    parse_patch,
    render_patch,
    with_hunk,
)

A_BASE = [f"line {i}" for i in range(1, 41)]


def _a_fixed() -> list[str]:
    lines = list(A_BASE)
    lines[1] = "line 2 changed"  # replace near the top
    lines[9:12] = ["inserted a", "inserted b"] + lines[9:12]  # pure addition
    lines[22:25] = []  # pure deletion
    lines[30:33] = ["line 31 x", "line 32 y", "new between", "line 33 z"]  # mixed
    lines.append("tail added")  # addition at the end of the file
    return lines


B_BASE = ["first", "second", "third"]
C_NEW = ["brand", "new", "file"]


def _git(repo, *args, **kw):
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True, **kw
    ).stdout


@pytest.fixture
def multi_repo(tmp_path):
    repo = tmp_path / "multi"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "fixture@example.invalid")
    _git(repo, "config", "user.name", "Fixture")
    (repo / "a.txt").write_text("\n".join(A_BASE) + "\n")
    (repo / "b.txt").write_text("\n".join(B_BASE) + "\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "base")
    base = _git(repo, "rev-parse", "HEAD").strip()
    (repo / "a.txt").write_text("\n".join(_a_fixed()) + "\n")
    (repo / "b.txt").unlink()
    (repo / "c.txt").write_text("\n".join(C_NEW))  # no newline at the end
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "fix")
    fix = _git(repo, "rev-parse", "HEAD").strip()
    _git(repo, "checkout", "-q", base)
    return repo, base, fix


def _applies(repo, text: str, zero: bool) -> tuple[bool, str]:
    if not text:
        return True, ""
    flags = ["--unidiff-zero"] if zero else []
    proc = subprocess.run(
        ["git", "-C", str(repo), "apply", "--check", *flags, "-"],
        input=text,
        capture_output=True,
        text=True,
    )
    return proc.returncode == 0, proc.stderr


def _subsets(n: int, smallest: int = 0):
    for r in range(smallest, n + 1):
        yield from itertools.combinations(range(n), r)


@pytest.mark.parametrize("context", [3, 0])
def test_every_hunk_subset_applies(multi_repo, context):
    repo, base, fix = multi_repo
    patch = parse_patch(_git(repo, "diff", f"-U{context}", base, fix))
    hunks = patch.hunks
    assert {f.new_path for f in patch.files} >= {"a.txt", "c.txt"}
    assert len(hunks) >= 5
    for keep in _subsets(len(hunks)):
        ok, err = _applies(repo, render_patch(patch, set(keep)), zero=context == 0)
        assert ok, (context, keep, err)


@pytest.mark.parametrize("context", [3, 0])
def test_every_line_subset_of_every_hunk_applies(multi_repo, context):
    repo, base, fix = multi_repo
    patch = parse_patch(_git(repo, "diff", f"-U{context}", base, fix))
    everything = {h.index for h in patch.hunks}
    for hunk in patch.hunks:
        positions = change_lines(hunk)
        if not lines_searchable(hunk):
            # The line pass keeps a whole-file deletion whole; see below.
            assert hunk.new_path == "/dev/null"
            continue
        if len(positions) > 7:
            continue
        for keep in _subsets(len(positions), smallest=1):
            # ddmin never asks for the empty subset of a hunk's lines.
            chosen = {positions[i] for i in keep}
            # The line pass keeps the other hunks whole, as refine_lines does.
            text = render_patch(with_hunk(patch, hunk_with_lines(hunk, chosen)), everything)
            ok, err = _applies(repo, text, zero=context == 0)
            assert ok, (context, hunk.index, sorted(chosen), err)


def test_part_of_a_file_deletion_does_not_apply(multi_repo):
    # Why the line pass leaves whole-file deletions alone.
    repo, base, fix = multi_repo
    patch = parse_patch(_git(repo, "diff", base, fix))
    (deletion,) = [h for h in patch.hunks if h.new_path == "/dev/null"]
    first = change_lines(deletion)[0]
    text = render_patch(with_hunk(patch, hunk_with_lines(deletion, {first})), {deletion.index})
    ok, err = _applies(repo, text, zero=False)
    assert not ok and "still has contents" in err


def test_line_pass_keeps_a_needed_file_deletion_whole(multi_repo):
    import sys

    from patchmin.core import minimize_text
    from patchmin.oracle import WorktreeOracle

    repo, base, fix = multi_repo
    cmd = f"{sys.executable} -I -c \"import os; assert not os.path.exists('b.txt')\""
    text = _git(repo, "diff", base, fix)
    with WorktreeOracle(repo, base, cmd) as oracle:
        result = minimize_text(text, oracle, lines=True)
    (deletion,) = [h for h in parse_patch(text).hunks if h.new_path == "/dev/null"]
    assert result.needed == [deletion.index]
    assert [lv.needed for lv in result.line_verdicts] == [True] * deletion.removed
    assert "apply" not in result.unresolved
