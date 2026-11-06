"""Patch formats other than plain ``git diff`` output."""

import re
import subprocess
import sys

from conftest import make_repo

from patchmin.cli import main
from patchmin.diff import parse_patch, render_patch

PLAIN = """\
diff -ru a/x.py b/x.py
--- a/x.py\t2026-01-01 00:00:00
+++ b/x.py\t2026-01-01 00:00:01
@@ -1,3 +1,3 @@
 a
-b
+B
 c
diff -ru a/y.py b/y.py
--- a/y.py\t2026-01-01 00:00:00
+++ b/y.py\t2026-01-01 00:00:01
@@ -1,3 +1,3 @@
 p
-q
+Q
 r
"""

FORMAT_PATCH = """\
From 0000000000000000000000000000000000000000 Mon Sep 17 00:00:00 2001
From: Fixture <fixture@example.invalid>
Date: Thu, 1 Jan 2026 00:00:00 +0000
Subject: [PATCH] after

---
 y.py | 2 +-
 1 file changed, 1 insertion(+), 1 deletion(-)

diff --git a/y.py b/y.py
index 1111111..2222222 100644
--- a/y.py
+++ b/y.py
@@ -1,3 +1,3 @@
 p
-q
+Q
 r
-- {}
2.40.0

""".format(" ")


def test_plain_multi_file_diff_splits_files():
    patch = parse_patch(PLAIN)
    assert [f.new_path for f in patch.files] == ["x.py", "y.py"]
    assert [(h.new_path, h.added, h.removed) for h in patch.hunks] == [
        ("x.py", 1, 1),
        ("y.py", 1, 1),
    ]
    assert "+++ b/y.py" not in render_patch(patch, {0})


def test_format_patch_signature_is_not_a_removed_line():
    patch = parse_patch(FORMAT_PATCH)
    (hunk,) = patch.hunks
    assert (hunk.added, hunk.removed) == (1, 1)
    assert hunk.lines[-1] == " r"


def test_hunk_counts_end_a_hunk_before_trailing_text():
    text = "--- a/x\n+++ b/x\n@@ -1,2 +1,2 @@\n-a\n+A\n b\n-- \nnot part of the hunk\n"
    (hunk,) = parse_patch(text).hunks
    assert hunk.lines == ["-a", "+A", " b"]


def _repo(tmp_path):
    return make_repo(
        tmp_path,
        {"x.py": "a\nb\nc\n", "y.py": "p\nq\nr\n"},
        {"x.py": "a\nB\nc\n", "y.py": "p\nQ\nr\n"},
    )


def _run(fx, patch_file, capsys):
    code = main(
        [
            "--repo", str(fx["repo"]), "--base", fx["base"], "--patch", str(patch_file),
            "--lines", "--test-cmd", f"{sys.executable} -c \"assert 'Q' in open('y.py').read()\"",
        ]
    )
    return code, capsys.readouterr().out


def test_cli_on_plain_diff_patch(tmp_path, capsys):
    fx = _repo(tmp_path)
    patch_file = tmp_path / "plain.patch"
    patch_file.write_text(PLAIN)
    code, out = _run(fx, patch_file, capsys)
    assert code == 0, out
    assert re.search(r"^not needed  hunk 0 .* x\.py:1", out, re.M)
    assert re.search(r"^needed      hunk 1 .* y\.py:1", out, re.M)
    assert "minimal patch confirmed: pass" in out


def test_cli_on_format_patch_file(tmp_path, capsys):
    fx = _repo(tmp_path)
    text = subprocess.run(
        ["git", "-C", str(fx["repo"]), "format-patch", "-1", "--stdout", fx["fix"]],
        capture_output=True, text=True, check=True,
    ).stdout
    assert "\n-- \n" in text
    patch_file = tmp_path / "fp.patch"
    patch_file.write_text(text)
    code, out = _run(fx, patch_file, capsys)
    assert code == 0, out
    assert "1 of 2 hunks needed" in out
    assert "1 of 2 change lines needed" in out
