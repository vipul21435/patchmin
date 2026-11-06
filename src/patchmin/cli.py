"""Command line entry point."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from . import __version__
from .cache import RunCache
from .core import minimize_text
from .oracle import WorktreeOracle


def _diff_between(repo: Path, base: str, fix: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), "diff", "--no-color", base, fix],
        capture_output=True,
        text=True,
        check=True,
    )
    return proc.stdout


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="patchmin",
        description="Find the hunks of a fix that make the target tests pass.",
    )
    p.add_argument("--version", action="version", version=f"patchmin {__version__}")
    p.add_argument("--repo", type=Path, default=Path("."), help="git repository (default: .)")
    p.add_argument("--base", required=True, help="commit the fix applies to")
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--fix", help="commit containing the fix; the patch is git diff BASE FIX")
    src.add_argument("--patch", type=Path, help="unified diff file to minimise")
    p.add_argument("--test-cmd", required=True, help="command that runs the tests")
    p.add_argument(
        "--target", action="append", default=[], help="target test id appended to the command"
    )
    p.add_argument("--timeout", type=float, default=600.0, help="seconds per test run")
    p.add_argument("--reruns", type=int, default=0, help="extra runs to confirm each outcome")
    p.add_argument("--cache", type=Path, default=None, help="JSON file for the run cache")
    p.add_argument("--json", action="store_true", help="print the result as JSON")
    p.add_argument("--out", type=Path, default=None, help="write the minimal patch here")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    repo = args.repo.resolve()
    if args.patch is not None:
        patch_text = args.patch.read_text()
    else:
        patch_text = _diff_between(repo, args.base, args.fix)
    cache = RunCache(args.cache)
    with WorktreeOracle(
        repo,
        args.base,
        args.test_cmd,
        targets=args.target,
        timeout=args.timeout,
        reruns=args.reruns,
        cache=cache,
    ) as oracle:
        result = minimize_text(patch_text, oracle)
    if args.json:
        print(json.dumps(result.to_dict(), indent=2))
    else:
        if result.error:
            print(f"error: {result.error}")
        for v in result.verdicts:
            mark = "needed    " if v.needed else "not needed"
            print(f"{mark}  hunk {v.index:<3} {v.label:<32} +{v.added}/-{v.removed}  {v.heading}")
        print(
            f"{len(result.needed)} of {len(result.verdicts)} hunks needed; "
            f"{result.runs} test runs ({result.cached_hits} cache hits, "
            f"{result.flaky_runs} flaky) in {result.seconds:.1f}s"
        )
    if args.out is not None and not result.error:
        args.out.write_text(result.minimal_patch)
    return 1 if result.error else 0


if __name__ == "__main__":
    sys.exit(main())
