import subprocess

from patchmin.diff import classify_hunk, parse_patch, render_patch


def _diff(repo, base, fix):
    return subprocess.run(
        ["git", "-C", str(repo), "diff", base, fix], capture_output=True, text=True, check=True
    ).stdout


def test_parse_counts_hunks(fixture_repo):
    text = _diff(fixture_repo["repo"], fixture_repo["base"], fixture_repo["fix"])
    patch = parse_patch(text)
    assert len(patch.files) == 1
    assert [h.index for h in patch.hunks] == [0, 1, 2, 3]
    assert patch.files[0].new_path == "calc.py"
    assert all(h.added == h.removed >= 1 for h in patch.hunks)


def test_render_full_patch_round_trips(fixture_repo):
    text = _diff(fixture_repo["repo"], fixture_repo["base"], fixture_repo["fix"])
    patch = parse_patch(text)
    assert render_patch(patch) == text


def test_render_subset_applies_with_git(fixture_repo, tmp_path):
    repo = fixture_repo["repo"]
    text = _diff(repo, fixture_repo["base"], fixture_repo["fix"])
    patch = parse_patch(text)
    for keep in ({1}, {0, 3}, {2}, {1, 2, 3}):
        subset = render_patch(patch, keep)
        subprocess.run(["git", "-C", str(repo), "checkout", "-q", fixture_repo["base"]], check=True)
        proc = subprocess.run(
            ["git", "-C", str(repo), "apply", "--check", "-"],
            input=subset,
            capture_output=True,
            text=True,
        )
        assert proc.returncode == 0, (keep, proc.stderr)


def test_render_empty_subset_is_empty(fixture_repo):
    text = _diff(fixture_repo["repo"], fixture_repo["base"], fixture_repo["fix"])
    assert render_patch(parse_patch(text), set()) == ""


def test_parse_handles_no_newline_marker_and_new_file():
    text = (
        "diff --git a/new.txt b/new.txt\n"
        "new file mode 100644\n"
        "--- /dev/null\n"
        "+++ b/new.txt\n"
        "@@ -0,0 +1 @@\n"
        "+hello\n"
        "\\ No newline at end of file\n"
    )
    patch = parse_patch(text)
    assert patch.files[0].old_path == "/dev/null"
    assert patch.hunks[0].old_count == 0
    assert render_patch(patch) == text


def test_hunk_with_lines_turns_dropped_removals_into_context():
    from patchmin.diff import Hunk, change_lines, hunk_with_lines

    h = Hunk(0, "f", "f", 1, 3, 1, 3, [" a", "-b", "+B", "-c", "+C", " d"])
    assert change_lines(h) == [1, 2, 3, 4]
    sub = hunk_with_lines(h, {3, 4})
    assert sub.lines == [" a", " b", "-c", "+C", " d"]
    assert (sub.old_count, sub.new_count) == (4, 4)


def test_deleted_files_are_labelled_by_their_old_path():
    from patchmin.core import pinned_hunks

    text = (
        "--- a/tests/test_old.py\n+++ /dev/null\n@@ -1,2 +0,0 @@\n-def test_x():\n-    pass\n"
        "--- a/NOTES.md\n+++ /dev/null\n@@ -1 +0,0 @@\n-Some notes.\n"
    )
    patch = parse_patch(text)
    old_test, notes = patch.hunks
    assert old_test.label() == "tests/test_old.py:1" and notes.label() == "NOTES.md:1"
    assert classify_hunk(old_test) == "test" and classify_hunk(notes) == "docs"
    assert pinned_hunks(patch, ["tests/*"]) == {0}
