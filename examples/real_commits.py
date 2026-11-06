"""Run patchmin on six real bug-fix commits from public Python libraries.

Each case names a library on PyPI (its source repository is the link under
``url_key`` in the project URLs of its PyPI page), a fix commit, the tests that
the fix turns from failing to passing, and the test directory whose hunks are
pinned (always applied). The rest of the suite runs as ``--rest-cmd``, so a
subset counts only when the whole suite stays green.

Usage:

    uv run python examples/real_commits.py --data DIR --python PY [--jobs 4] [--out DIR]
    uv run python examples/real_commits.py --summary results/jobs1 results/jobs4
    uv run python examples/real_commits.py --hunks results/jobs4

``DIR`` is where the repositories are cloned (they are cloned on first use).
``PY`` is an interpreter that has pytest (and, for attrs, hypothesis, pympler
and cloudpickle) installed; it runs the libraries' tests. One JSON result per
case is written to ``--out`` and a summary table is printed.
``examples/reproduce_real_commits.sh`` does all of it end to end.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

CASES = [
    {
        "name": "tabulate-github-alignment",
        "pypi": "tabulate",
        "dir": "tabulate",
        "commit": "8014ec66820c",
        "date": "2026-03-05",
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
        "pypi": "tabulate",
        "dir": "tabulate",
        "commit": "3aa568c66987",
        "date": "2026-03-05",
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
        "pypi": "more-itertools",
        "dir": "more-itertools",
        "commit": "ce676d24136e",
        "date": "2026-09-04",
        "subject": "Do not yield an empty list for an empty iterable when maxsplit is 0",
        "tests": "tests",
        "pin": "tests/*",
        "targets": [
            "tests/test_more.py::SplitBeforeTest::test_empty_collection",
            "tests/test_more.py::SplitAfterTest::test_empty_collection",
            "tests/test_more.py::SplitWhenTests::test_empty_iterable",
        ],
    },
    {
        "name": "toolz-interpose-empty",
        "pypi": "toolz",
        "url_key": "homepage",
        "dir": "toolz",
        "commit": "d2eba0374a45",
        "date": "2026-09-18",
        "subject": "Fix interpose([]) raising StopIteration",
        "tests": "toolz",
        "pin": "toolz/tests/*",
        # test_has_version reads the installed package metadata; the clone is
        # not installed, so it fails before and after the fix.
        "rest_args": ["--deselect", "toolz/tests/test_package.py::test_has_version"],
        "targets": ["toolz/tests/test_itertoolz.py::test_interpose_empty"],
    },
    {
        "name": "attrs-nested-disabled",
        "pypi": "attrs",
        "url_key": "GitHub",
        "dir": "attrs",
        "commit": "af9c510912ce",
        "date": "2026-03-14",
        "subject": "Fix validators.disabled() to save/restore state on nesting",
        "tests": "tests",
        "pin": "tests/*",
        # attrs keeps its package under src/ and runs pytest with
        # --import-mode=importlib, so src/ is put on the path explicitly.
        "test_args": ["-o", "pythonpath=src"],
        # test_packaging.py reads the installed package metadata.
        "rest_args": ["--ignore", "tests/test_packaging.py"],
        "targets": ["tests/test_validators.py::TestDisableValidators::test_disabled_ctx_nested"],
    },
    {
        "name": "attrs-frozen-error-message",
        "pypi": "attrs",
        "url_key": "GitHub",
        "dir": "attrs",
        "commit": "ce89f5d11feb",
        "date": "2026-03-14",
        "subject": "Fix message passing in frozen errors",
        "tests": "tests",
        "pin": "tests/*",
        "test_args": ["-o", "pythonpath=src"],
        "rest_args": ["--ignore", "tests/test_packaging.py"],
        "targets": [
            "tests/test_functional.py::TestFunctional::test_frozen_instance[Frozen]",
            "tests/test_functional.py::TestFunctional::test_frozen_instance[FrozenToo]",
        ],
    },
]


def source_url(pypi_name: str, key: str = "Homepage") -> str:
    """The source repository listed under ``key`` on the project's PyPI page."""
    with urllib.request.urlopen(f"https://pypi.org/pypi/{pypi_name}/json", timeout=30) as resp:
        info = json.load(resp)["info"]
    return info["project_urls"][key]


def ensure_clone(data: Path, case: dict) -> Path:
    repo = data / case["dir"]
    if not (repo / ".git").exists():
        data.mkdir(parents=True, exist_ok=True)
        url = source_url(case["pypi"], case.get("url_key", "Homepage"))
        subprocess.run(["git", "clone", "-q", url, str(repo)], check=True)
    return repo


def command(case: dict, repo: Path, python: str, jobs: int) -> list[str]:
    base = [python, "-m", "pytest", "-q", "-p", "no:cacheprovider"]
    pytest = " ".join([*base, *case.get("test_args", [])])
    rest = " ".join([pytest, case["tests"], *case.get("rest_args", [])])
    argv = [
        "--repo", str(repo),
        "--base", case["commit"] + "^",
        "--fix", case["commit"],
        "--pin", case["pin"],
        "--test-cmd", pytest,
        "--rest-cmd", rest,
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
    ap.add_argument("--out", type=Path, default=Path("results/jobs4"))
    ap.add_argument("--only", default=None, help="run only the case with this name")
    ap.add_argument(
        "--summary", nargs="+", type=Path, metavar="DIR",
        help="print one table from the saved results in these folders, run nothing",
    )
    ap.add_argument(
        "--hunks", type=Path, metavar="DIR",
        help="print every hunk and its verdict from the saved results, run nothing",
    )
    args = ap.parse_args()
    if args.summary:
        print("\n".join(table(args.summary)))
        return 0
    if args.hunks:
        print("\n".join(hunk_lines(args.hunks)))
        return 0
    if args.data is None:
        ap.error("--data is required unless --summary or --hunks is given")
    args.out.mkdir(parents=True, exist_ok=True)
    for case in CASES:
        if args.only and case["name"] != args.only:
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
            "case": {k: case[k] for k in ("name", "commit", "subject", "targets")},
            "source": f"https://pypi.org/project/{case['pypi']}/",
            "jobs": args.jobs,
            "load_average": round(os.getloadavg()[0], 1) if hasattr(os, "getloadavg") else None,
            "wall_seconds": round(wall, 1),
            "result": result,
        }
        # The load average is read after the run, so it covers the run itself.
        (args.out / f"{case['name']}.json").write_text(json.dumps(record, indent=2) + "\n")
        print(f"{case['name']}: {wall:.1f} s", file=sys.stderr)
    print("\n".join(table([args.out])))
    return 0


def summary_row(case: dict, record: dict) -> tuple[str, ...]:
    """One table row from a saved record."""
    result = record["result"]
    searched = [h for h in result["hunks"] if h["status"] != "pinned"]
    lines = result["lines"]
    unresolved = sum(result.get("unresolved", {}).values())
    return (
        f"{case['dir']} {case['commit'][:7]}",
        str(record.get("jobs", "")),
        f"{len(result['needed'])} of {len(searched)}",
        f"{sum(1 for x in lines if x['needed'])} of {len(lines)}",
        str(result["tests"]),
        str(result["runs"]),
        str(result["cached_hits"]),
        str(result["flaky_runs"]),
        str(unresolved),
        ", ".join(result["confirm"]),
        f"{record['wall_seconds']:.1f}",
    )


HEADER = (
    "commit", "jobs", "hunks needed", "lines needed", "ddmin trials", "test runs", "cache hits",
    "flaky", "unresolved", "confirm", "wall s",
)


def table(folders: list[Path]) -> list[str]:
    """A Markdown table with one row per case and folder, cases in CASES order."""
    out = ["| " + " | ".join(HEADER) + " |", "|" + "---|" * len(HEADER)]
    for case in CASES:
        for folder in folders:
            path = folder / f"{case['name']}.json"
            if path.exists():
                row = summary_row(case, json.loads(path.read_text()))
                out.append("| " + " | ".join(row) + " |")
    return out


def hunk_lines(folder: Path) -> list[str]:
    """Every hunk of every saved case with its kind and verdict, then the line counts."""
    out = []
    for case in CASES:
        path = folder / f"{case['name']}.json"
        if not path.exists():
            continue
        result = json.loads(path.read_text())["result"]
        out.append(f"{case['dir']} {case['commit'][:7]}")
        for h in result["hunks"]:
            lines = [x for x in result["lines"] if x["hunk"] == h["index"]]
            kept = sum(1 for x in lines if x["needed"])
            detail = f"  lines {kept} of {len(lines)} needed" if lines else ""
            out.append(
                f"  hunk {h['index']}  {h['status']:<10}  {h['kind']:<10}  {h['label']}"
                f"  +{h['added']}/-{h['removed']}{detail}"
            )
    return out


if __name__ == "__main__":
    sys.exit(main())
