"""Find the target tests of a fix: the tests that fail before it and pass after.

This is the fail-to-pass set used by SWE-bench style benchmarks. It works
for pytest only: the test command is run with ``--junitxml`` (``xunit1``
family, which records the file of each test case) once on the base with the
pinned hunks applied and once with the whole patch, and the two reports are
compared.
"""

from __future__ import annotations

import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

from .diff import Patch, render_patch
from .oracle import WorktreeOracle

PASSED = "passed"
FAILED = "failed"
SKIPPED = "skipped"


def node_id(file: str, classname: str, name: str) -> str:
    """Rebuild a pytest node id from a junit ``testcase`` element's fields.

    ``classname`` is the dotted module path, followed by the class name for
    a test method; the module part is matched against ``file``.
    """
    module = file[:-3].replace("/", ".") if file.endswith(".py") else file.replace("/", ".")
    rest = classname[len(module) :].lstrip(".") if classname.startswith(module) else ""
    parts = [file, *([p for p in rest.split(".") if p]), name]
    return "::".join(parts)


def parse_junit(text: str) -> dict[str, str]:
    """Map node id to ``passed``, ``failed`` or ``skipped``.

    A test case with a ``failure`` or ``error`` child counts as failed, even
    when another entry for the same id passed (pytest writes extra entries
    for subtests).
    """
    root = ET.fromstring(text)
    out: dict[str, str] = {}
    for case in root.iter("testcase"):
        file = case.get("file") or ""
        nid = node_id(file, case.get("classname", ""), case.get("name", ""))
        tags = {child.tag for child in case}
        if tags & {"failure", "error"}:
            status = FAILED
        elif "skipped" in tags:
            status = SKIPPED
        else:
            status = PASSED
        if out.get(nid) == FAILED:
            continue
        out[nid] = status
    return out


@dataclass
class TargetReport:
    targets: list[str]
    before: dict[str, str] = field(default_factory=dict)
    after: dict[str, str] = field(default_factory=dict)
    error: str | None = None

    @property
    def broken(self) -> list[str]:
        """Tests that passed before the fix and fail after it."""
        return sorted(
            t for t, s in self.before.items() if s == PASSED and self.after.get(t) == FAILED
        )


def pinned_test_files(patch: Patch, pinned: set[int]) -> list[str]:
    """Python files touched by the pinned hunks, in patch order."""
    files: list[str] = []
    for h in patch.hunks:
        path = h.new_path
        if h.index in pinned and path.endswith(".py") and path not in files:
            files.append(path)
    return files


def find_targets(
    patch: Patch, pinned: set[int], oracle: WorktreeOracle, paths: list[str] | None = None
) -> TargetReport:
    """Run pytest before and after the patch and return the fail-to-pass tests.

    ``oracle`` is a :class:`~patchmin.oracle.WorktreeOracle`; its test command
    must start pytest. ``paths`` limits the run (default: the files of the
    pinned hunks; with no pinned Python file, the whole command as given).
    """
    paths = paths if paths is not None else pinned_test_files(patch, pinned)
    reports: list[dict[str, str]] = []
    with tempfile.TemporaryDirectory(prefix="patchmin-junit-") as tmp:
        for i, keep in enumerate((pinned, None)):
            xml = Path(tmp) / f"run{i}.xml"
            extra = [f"--junitxml={xml}", "-o", "junit_family=xunit1", *paths]
            result = oracle.execute(render_patch(patch, keep), extra)
            if result.reason or not xml.exists():
                what = "before" if i == 0 else "after"
                why = result.reason or "no junit report written"
                return TargetReport([], error=f"test run {what} the fix failed: {why}")
            reports.append(parse_junit(xml.read_text()))
    before, after = reports
    targets = sorted(t for t, s in after.items() if s == PASSED and before.get(t, FAILED) == FAILED)
    return TargetReport(targets, before, after)
