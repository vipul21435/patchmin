"""Command line entry point."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from . import __version__
from .cache import RunCache
from .core import minimize_text, pinned_hunks
from .diff import parse_patch
from .oracle import WorktreeOracle, resolve_commit
from .report import markdown_report
from .targets import find_targets


def _diff_between(repo: Path, base: str, fix: str, context: int = 3) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), "diff", "--no-color", f"-U{context}", base, fix],
        capture_output=True,
        text=True,
        check=True,
    )
    return proc.stdout


def _count(minimum: int):
    """argparse type: an integer no smaller than ``minimum``."""

    def parse(text: str) -> int:
        try:
            value = int(text)
        except ValueError:
            raise argparse.ArgumentTypeError(f"not an integer: {text!r}") from None
        if value < minimum:
            raise argparse.ArgumentTypeError(f"must be at least {minimum}, got {value}")
        return value

    return parse


def _seconds(text: str) -> float:
    try:
        value = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a number: {text!r}") from None
    if not value > 0:
        raise argparse.ArgumentTypeError(f"must be more than 0, got {text}")
    return value


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
    p.add_argument(
        "--rest-cmd",
        default=None,
        help="command for the rest of the suite; it must also pass for a subset to count",
    )
    p.add_argument(
        "--pin",
        action="append",
        default=[],
        metavar="GLOB",
        help="path pattern whose hunks are always applied and never searched (repeatable)",
    )
    p.add_argument(
        "--context",
        type=_count(0),
        default=3,
        metavar="N",
        help="context lines for git diff with --fix (default 3); fewer lines give "
        "smaller hunks, 0 gives one hunk per run of changed lines",
    )
    p.add_argument("--timeout", type=_seconds, default=600.0, help="seconds per test run")
    p.add_argument("--reruns", type=_count(0), default=0, help="extra runs to confirm each outcome")
    p.add_argument(
        "--find-targets",
        action="store_true",
        help="pytest only: run the tests before and after the fix and use the "
        "fail-to-pass tests as targets (needs --pin for the test files)",
    )
    p.add_argument(
        "--confirm",
        type=_count(0),
        default=1,
        help="uncached runs of the final minimal patch to confirm it passes (0 to skip)",
    )
    p.add_argument("--jobs", type=_count(1), default=1, help="git worktrees to run in parallel")
    p.add_argument("--cache", type=Path, default=None, help="JSON file for the run cache")
    p.add_argument(
        "--lines", action="store_true", help="also minimise the change lines inside needed hunks"
    )
    p.add_argument("--json", action="store_true", help="print the result as JSON")
    p.add_argument("--out", type=Path, default=None, help="write the minimal patch here")
    p.add_argument(
        "--markdown", type=Path, default=None, help="write a Markdown report of the result here"
    )
    return p


def _check_inputs(args: argparse.Namespace, repo: Path) -> str | None:
    """Return an error message for inputs that cannot work, or None."""
    if not repo.is_dir():
        return f"--repo {args.repo} is not a directory"
    probe = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "--is-inside-work-tree"],
        capture_output=True,
        text=True,
    )
    if probe.returncode != 0 or probe.stdout.strip() != "true":
        return f"--repo {args.repo} is not a git work tree"
    for flag, rev in (("--base", args.base), ("--fix", args.fix)):
        if rev is None:
            continue
        try:
            resolve_commit(repo, rev)
        except ValueError:
            return f"{flag} {rev} does not name a commit in {args.repo}"
    if args.patch is not None and not args.patch.is_file():
        return f"--patch {args.patch} is not a file"
    return None


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    repo = args.repo.resolve()
    problem = _check_inputs(args, repo)
    if problem is not None:
        print(f"error: {problem}", file=sys.stderr)
        return 1
    try:
        return _run(args, repo)
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or "").strip().splitlines()
        why = detail[-1] if detail else f"exit code {exc.returncode}"
        print(f"error: {' '.join(map(str, exc.cmd))} failed: {why}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


def _run(args: argparse.Namespace, repo: Path) -> int:
    if args.patch is not None:
        patch_text = args.patch.read_text()
    else:
        patch_text = _diff_between(repo, args.base, args.fix, args.context)
    # Hunks without context lines (a -U0 diff) need git apply --unidiff-zero.
    zero_context = any(
        not any(ln.startswith(" ") for ln in h.lines) for h in parse_patch(patch_text).hunks
    )
    cache = RunCache(args.cache)
    with WorktreeOracle(
        repo,
        args.base,
        args.test_cmd,
        targets=args.target,
        timeout=args.timeout,
        reruns=args.reruns,
        cache=cache,
        rest_cmd=args.rest_cmd,
        jobs=args.jobs,
        unidiff_zero=zero_context,
    ) as oracle:
        if args.find_targets:
            patch = parse_patch(patch_text)
            report = find_targets(patch, pinned_hunks(patch, args.pin), oracle)
            if report.error or not report.targets:
                print(f"error: {report.error or 'no test fails before the fix and passes after'}")
                return 1
            oracle.targets = args.target + report.targets
            if not args.json:
                print(f"targets ({len(report.targets)} fail before the fix, pass after):")
                for t in report.targets:
                    print(f"  {t}")
                if report.broken:
                    print(f"warning: {len(report.broken)} tests pass before the fix and fail after")
        result = minimize_text(
            patch_text, oracle, lines=args.lines, pin=args.pin, confirm=args.confirm
        )
    if args.json:
        data = result.to_dict()
        data["targets"] = oracle.targets
        print(json.dumps(data, indent=2))
    else:
        if result.error:
            print(f"error: {result.error}")
        for v in result.verdicts:
            line = (
                f"{v.status:<10}  hunk {v.index:<3} {v.kind:<10} {v.label:<32} "
                f"+{v.added}/-{v.removed}  {v.heading}"
            )
            print(line.rstrip())
        for lv in result.line_verdicts:
            mark = "needed    " if lv.needed else "not needed"
            print(f"{mark}  hunk {lv.hunk:<3} line {lv.position:<3} {lv.text}")
        if result.line_verdicts:
            kept = sum(1 for lv in result.line_verdicts if lv.needed)
            print(f"{kept} of {len(result.line_verdicts)} change lines needed")
        timeouts = result.unresolved.get("timeout", 0)
        unapplied = result.unresolved.get("apply", 0)
        runs = (
            f"{result.runs} test runs ({result.cached_hits} cache hits, "
            f"{result.flaky_runs} flaky, {timeouts} timed out, {unapplied} did not apply) "
            f"in {result.seconds:.1f}s"
        )
        if result.error:
            # No search ran, so there are no hunk counts to report.
            print(runs)
        else:
            searched = len(result.verdicts) - len(result.pinned)
            pinned = f", {len(result.pinned)} pinned" if result.pinned else ""
            print(f"{len(result.needed)} of {searched} hunks needed{pinned}; {runs}")
        if result.confirm_outcomes:
            verdict = "confirmed" if result.confirmed else "NOT confirmed"
            print(f"minimal patch {verdict}: {', '.join(result.confirm_outcomes)}")
    if args.out is not None and not result.error:
        args.out.write_text(result.minimal_patch)
    if args.markdown is not None:
        args.markdown.write_text(markdown_report(result))
    if result.error:
        return 1
    return 2 if result.confirmed is False else 0


if __name__ == "__main__":
    sys.exit(main())
