"""Offline checks of examples/real_commits.py: commands, tables and saved results."""

from __future__ import annotations

import importlib.util
import json
import shlex
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load():
    path = ROOT / "examples" / "real_commits.py"
    spec = importlib.util.spec_from_file_location("real_commits", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _flag(argv: list[str], name: str) -> str:
    return argv[argv.index(name) + 1]


def test_cases_have_unique_names_and_full_commit_prefixes():
    rc = _load()
    names = [c["name"] for c in rc.CASES]
    assert len(names) == len(set(names))
    for case in rc.CASES:
        assert len(case["commit"]) == 12
        assert case["targets"]
        assert case["pin"].startswith(case["tests"])


def test_command_adds_case_arguments_to_both_commands():
    rc = _load()
    case = next(c for c in rc.CASES if c["dir"] == "attrs")
    argv = rc.command(case, Path("/r"), "py", 4)
    test_cmd = shlex.split(_flag(argv, "--test-cmd"))
    rest_cmd = shlex.split(_flag(argv, "--rest-cmd"))
    pytest = ["py", "-m", "pytest", "-q", "-p", "no:cacheprovider"]
    assert test_cmd == [*pytest, "-o", "pythonpath=src"]
    assert rest_cmd == [*test_cmd, "tests", "--ignore", "tests/test_packaging.py"]
    assert _flag(argv, "--jobs") == "4"
    assert argv.count("--target") == len(case["targets"])


def test_command_without_case_arguments_keeps_the_old_form():
    rc = _load()
    case = next(c for c in rc.CASES if c["dir"] == "tabulate")
    argv = rc.command(case, Path("/r"), "py", 1)
    assert _flag(argv, "--test-cmd") == "py -m pytest -q -p no:cacheprovider"
    assert _flag(argv, "--rest-cmd") == "py -m pytest -q -p no:cacheprovider test"


def _record(case: dict, jobs: int) -> dict:
    return {
        "jobs": jobs,
        "wall_seconds": 3.25,
        "result": {
            "needed": [1],
            "hunks": [
                {"index": 0, "status": "not needed", "kind": "docs", "label": "a.md:1",
                 "added": 1, "removed": 0},
                {"index": 1, "status": "needed", "kind": "code", "label": "a.py:4",
                 "added": 2, "removed": 1},
                {"index": 2, "status": "pinned", "kind": "test", "label": "t.py:9",
                 "added": 3, "removed": 0},
            ],
            "lines": [
                {"hunk": 1, "needed": True},
                {"hunk": 1, "needed": False},
                {"hunk": 1, "needed": True},
            ],
            "tests": 7,
            "runs": 12,
            "cached_hits": 2,
            "flaky_runs": 1,
            "unresolved": {"flaky": 1, "timeout": 0, "apply": 2},
            "confirm": ["pass", "pass"],
        },
        "case": {"name": case["name"]},
    }


def test_table_has_one_row_per_case_and_folder(tmp_path):
    rc = _load()
    case = rc.CASES[0]
    for jobs in (1, 4):
        folder = tmp_path / f"jobs{jobs}"
        folder.mkdir()
        (folder / f"{case['name']}.json").write_text(json.dumps(_record(case, jobs)))
    lines = rc.table([tmp_path / "jobs1", tmp_path / "jobs4"])
    assert len(lines) == 4
    assert lines[0].count("|") == len(rc.HEADER) + 1
    cells = [c.strip() for c in lines[2].strip("|").split("|")]
    assert cells[:2] == [f"{case['dir']} {case['commit'][:7]}", "1"]
    assert cells[2:] == ["1 of 2", "2 of 3", "7", "12", "2", "1", "3", "pass, pass", "3.2"]
    assert lines[3].split("|")[2].strip() == "4"


def test_hunk_lines_list_every_hunk_with_line_counts(tmp_path):
    rc = _load()
    case = rc.CASES[0]
    (tmp_path / f"{case['name']}.json").write_text(json.dumps(_record(case, 4)))
    out = rc.hunk_lines(tmp_path)
    assert out[0] == f"{case['dir']} {case['commit'][:7]}"
    assert "not needed" in out[1] and "docs" in out[1] and "lines" not in out[1]
    assert out[2].endswith("+2/-1  lines 2 of 3 needed")
    assert "pinned" in out[3]


def test_saved_results_match_their_cases():
    rc = _load()
    names = {c["name"]: c for c in rc.CASES}
    paths = sorted((ROOT / "results").glob("jobs*/*.json"))
    assert paths
    for path in paths:
        record = json.loads(path.read_text())
        case = names[path.stem]
        assert record["case"]["commit"] == case["commit"]
        assert record["jobs"] == int(path.parent.name.removeprefix("jobs"))
        assert record["result"]["confirmed"] is True
