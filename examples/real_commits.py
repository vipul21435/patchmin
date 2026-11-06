"""Run patchmin on three real bug-fix commits from public Python libraries.

Each case names a public repository, a fix commit, the tests that the fix
turns from failing to passing, and the test directory whose hunks are pinned
(always applied). The rest of the suite runs as ``--rest-cmd``, so a subset
counts only when the whole suite stays green.

Usage:

    uv run python examples/real_commits.py --data DIR --python PY [--jobs 4]
    uv run python examples/real_commits.py --summary

``DIR`` is where the repositories are cloned (they are cloned on first use).
``PY`` is an interpreter that has pytest installed; it runs the libraries'
tests. One JSON result per case is written to ``results/`` and a summary
table is printed.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

CASES = [
    {
        "name": "tabulate-github-alignment",
        "url": "https://github.com/astanin/python-tabulate",
        "dir": "tabulate",
        "commit": "8014ec66820c",
        "subject": "Fix alignments in `github` tables",
        "tests": "test",
        "pin": "test/*",
        "targets": [
            "test/test_output.py::test_github",
            "test/test_output.py::test_github_multiline",
            "test/test_output.py::test_github_with_colalign",
            "test/test_output.py::test_simple_headerless_with_sep_line_with_padding_in_tablefmt",
        ],
    },
    {
        "name": "tabulate-asciidoc-whitespace",
        "url": "https://github.com/astanin/python-tabulate",
        "dir": "tabulate",
        "commit": "3aa568c66987",
        "subject": "fix #408 - remove trailing whitespace from asciidoc output",
        "tests": "test",
        "pin": "test/*",
        "targets": [
            "test/test_output.py::test_asciidoc",
            "test/test_output.py::test_asciidoc_headerless",
            "test/test_regression.py::test_asciidoc_without_trailing_whitespace",
        ],
    },
    {
        "name": "more-itertools-split-maxsplit-0",
        "url": "https://github.com/more-itertools/more-itertools",
        "dir": "more-itertools",
        "commit": "ce676d24136e",
        "subject": "Do not yield an empty list for an empty iterable when maxsplit is 0",
        "tests": "tests",
        "pin": "tests/*",
        "targets": [
            "tests/test_more.py::SplitBeforeTest::test_empty_collection",
            "tests/test_more.py::SplitAfterTest::test_empty_collection",
            "tests/test_more.py::SplitWhenTests::test_empty_iterable",
        ],
    },
]


def ensure_clone(data: Path, case: dict) -> Path:
    repo = data / case["dir"]
    if not (repo / ".git").exists():
        data.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "clone", "-q", case["url"], str(repo)], check=True)
    return repo


def command(case: dict, repo: Path, python: str, jobs: int) -> list[str]:
    pytest = f"{python} -m pytest -q -p no:cacheprovider"
    argv = [
        "--repo", str(repo),
        "--base", case["commit"] + "^",
        "--fix", case["commit"],
        "--pin", case["pin"],
        "--test-cmd", pytest,
        "--rest-cmd", f"{pytest} {case['tests']}",
        "--lines", "--jobs", str(jobs), "--reruns", "1", "--confirm", "2", "--json",
    ]
    for target in case["targets"]:
        argv += ["--target", target]
    return argv


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--data", type=Path, default=None, help="folder for the clones")
    ap.add_argument("--python", default=sys.executable, help="interpreter with pytest")
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--out", type=Path, default=Path("results"))
    ap.add_argument("--only", default=None, help="run only the case with this name")
    ap.add_argument(
        "--summary", action="store_true", help="print the table from saved results, run nothing"
    )
    args = ap.parse_args()
    if args.data is None and not args.summary:
        ap.error("--data is required unless --summary is given")
    args.out.mkdir(parents=True, exist_ok=True)
    for case in CASES:
        if args.summary or (args.only and case["name"] != args.only):
            continue
        repo = ensure_clone(args.data, case)
        argv = command(case, repo, args.python, args.jobs)
        t0 = time.monotonic()
        proc = subprocess.run(
            [sys.executable, "-m", "patchmin", *argv], capture_output=True, text=True
        )
        wall = time.monotonic() - t0
        if not proc.stdout.strip():
            print(proc.stderr, file=sys.stderr)
            return 1
        result = json.loads(proc.stdout)
        record = {
            "case": {k: case[k] for k in ("name", "url", "commit", "subject", "targets")},
            "wall_seconds": round(wall, 1),
            "result": result,
        }
        (args.out / f"{case['name']}.json").write_text(json.dumps(record, indent=2) + "\n")
    print_table(args.out)
    return 0


def print_table(folder: Path) -> None:
    rows = []
    for case in CASES:
        path = folder / f"{case['name']}.json"
        if not path.exists():
            continue
        record = json.loads(path.read_text())
        result = record["result"]
        searched = [h for h in result["hunks"] if h["status"] != "pinned"]
        lines = result["lines"]
        rows.append(
            (
                f"{case['dir']} {case['commit'][:7]}",
                f"{len(result['needed'])} of {len(searched)}",
                f"{sum(1 for x in lines if x['needed'])} of {len(lines)}",
                str(result["tests"]),
                str(result["runs"]),
                str(result["cached_hits"]),
                ", ".join(result["confirm"]),
                f"{record['wall_seconds']:.1f}",
            )
        )
    header = (
        "commit", "hunks needed", "lines needed", "ddmin trials", "test runs", "cache hits",
        "confirm", "wall s",
    )
    print("| " + " | ".join(header) + " |")
    print("|" + "---|" * len(header))
    for row in rows:
        print("| " + " | ".join(row) + " |")


if __name__ == "__main__":
    sys.exit(main())
