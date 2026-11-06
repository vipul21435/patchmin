import subprocess
import sys
from pathlib import Path

import pytest

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

PAD = [
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

PAD = [
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

FIXED = '''"""Small arithmetic helpers, documented."""

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
    # adds two numbers, now correctly
    return a + b

PAD = [
    1,
    2,
    3,
    4,
    5,
    6,
    7,
]

def mul(a, b):
    """Multiply two numbers."""
    return a * b

PAD = [
    1,
    2,
    3,
    4,
    5,
    6,
    7,
]

def neg(a):
    return 0 - a
'''

CHECK = "import calc; assert calc.add(2, 3) == 5 and calc.mul(2, 3) == 6 and calc.neg(4) == -4"


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    ).stdout.strip()


@pytest.fixture
def fixture_repo(tmp_path: Path) -> dict:
    """A git repo with a buggy commit and a fix commit of four hunks.

    Only the second hunk (``a - b`` to ``a + b``) is needed for the check
    command to pass; the others are a docstring, a comment and a refactor.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "fixture@example.invalid")
    _git(repo, "config", "user.name", "Fixture")
    (repo / "calc.py").write_text(BUGGY)
    _git(repo, "add", "calc.py")
    _git(repo, "commit", "-qm", "buggy")
    base = _git(repo, "rev-parse", "HEAD")
    (repo / "calc.py").write_text(FIXED)
    _git(repo, "commit", "-qam", "fix")
    fix = _git(repo, "rev-parse", "HEAD")
    test_cmd = f'{sys.executable} -c "{CHECK}"'
    return {"repo": repo, "base": base, "fix": fix, "test_cmd": test_cmd}
