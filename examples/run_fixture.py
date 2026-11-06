"""Build a tiny git repo with a four-hunk fix and minimise it with patchmin.

Run with: uv run python examples/run_fixture.py
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

from patchmin.cli import main

BUGGY = '''"""Small arithmetic helpers."""

PAD = [
    1,
    2,
    3,
    4,
    5,
    6,
    7,
]

def add(a, b):
    # adds two numbers
    return a - b

PAD2 = [
    1,
    2,
    3,
    4,
    5,
    6,
    7,
]

def mul(a, b):
    """Multiply."""
    return a * b

PAD3 = [
    1,
    2,
    3,
    4,
    5,
    6,
    7,
]

def neg(a):
    return -a
'''

FIXED = (
    BUGGY.replace("helpers.", "helpers, documented.")
    .replace("# adds two numbers", "# adds two numbers, now correctly")
    .replace("a - b", "a + b")
    .replace('"""Multiply."""', '"""Multiply two numbers."""')
    .replace("return -a", "return 0 - a")
)

CHECK = "import calc; assert calc.add(2, 3) == 5 and calc.mul(2, 3) == 6 and calc.neg(4) == -4"


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    ).stdout.strip()


def build_repo(root: Path) -> tuple[str, str]:
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.email", "example@example.invalid")
    git(root, "config", "user.name", "Example")
    (root / "calc.py").write_text(BUGGY)
    git(root, "add", "calc.py")
    git(root, "commit", "-qm", "buggy")
    base = git(root, "rev-parse", "HEAD")
    (root / "calc.py").write_text(FIXED)
    git(root, "commit", "-qam", "fix")
    return base, git(root, "rev-parse", "HEAD")


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="patchmin-example-") as tmp:
        repo = Path(tmp) / "repo"
        repo.mkdir()
        base, fix = build_repo(repo)
        argv = [
            "--repo", str(repo),
            "--base", base,
            "--fix", fix,
            "--test-cmd", f'{sys.executable} -c "{CHECK}"',
        ]
        print("$ patchmin --repo <repo> --base <base> --fix <fix> --lines \\")
        print("      --test-cmd 'python -c ...'")
        sys.exit(main(argv + ["--lines"]))
