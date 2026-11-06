"""Use patchmin from Python on the fixture repository of run_fixture.py.

Run with: uv run python examples/python_api.py
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

from run_fixture import CHECK, build_repo

from patchmin import RunCache, WorktreeOracle, minimize_text

with tempfile.TemporaryDirectory(prefix="patchmin-api-") as tmp:
    repo = Path(tmp) / "repo"
    repo.mkdir()
    base, fix = build_repo(repo)
    diff = subprocess.run(
        ["git", "-C", str(repo), "diff", base, fix], capture_output=True, text=True, check=True
    ).stdout
    test_cmd = f'{sys.executable} -c "{CHECK}"'
    with WorktreeOracle(repo, base, test_cmd, cache=RunCache(), jobs=2) as oracle:
        result = minimize_text(diff, oracle, lines=True, confirm=1)
    print("needed hunks:", result.needed)
    print("kinds:", [(v.index, v.kind, v.status) for v in result.verdicts])
    print("runs:", result.runs, "cache hits:", result.cached_hits, "confirmed:", result.confirmed)
    print(result.minimal_patch, end="")
