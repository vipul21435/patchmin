"""Parse unified diffs into hunks and render subsets of hunks back out."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)$")


@dataclass
class Hunk:
    """One hunk of a unified diff.

    ``lines`` keeps the leading tag character (space, ``+`` or ``-``) and the
    text without the trailing newline. ``index`` is the position of the hunk
    in the whole patch, counted across files.
    """

    index: int
    old_path: str
    new_path: str
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    lines: list[str] = field(default_factory=list)
    heading: str = ""

    @property
    def added(self) -> int:
        return sum(1 for ln in self.lines if ln.startswith("+"))

    @property
    def removed(self) -> int:
        return sum(1 for ln in self.lines if ln.startswith("-"))

    @property
    def delta(self) -> int:
        """Change in line count when this hunk is applied."""
        return self.added - self.removed

    def label(self) -> str:
        return f"{self.new_path}:{self.old_start}"


@dataclass
class FilePatch:
    """All hunks of one file plus the header lines that precede them."""

    old_path: str
    new_path: str
    header: list[str] = field(default_factory=list)
    hunks: list[Hunk] = field(default_factory=list)


@dataclass
class Patch:
    files: list[FilePatch] = field(default_factory=list)

    @property
    def hunks(self) -> list[Hunk]:
        return [h for f in self.files for h in f.hunks]


def _strip_prefix(path: str) -> str:
    path = path.split("\t", 1)[0].strip()
    if path.startswith(("a/", "b/")):
        return path[2:]
    return path


def parse_patch(text: str) -> Patch:
    """Parse a unified diff (as produced by ``git diff``) into a Patch."""
    patch = Patch()
    current: FilePatch | None = None
    hunk: Hunk | None = None
    index = 0
    old_path = new_path = ""
    pending_header: list[str] = []

    for raw in text.splitlines():
        if raw.startswith("diff --git ") or raw.startswith("--- ") and hunk is None and not raw.startswith("--- a/") and current is None and not old_path:
            pass
        if raw.startswith("diff --git "):
            current = None
            hunk = None
            old_path = new_path = ""
            pending_header = [raw]
            continue
        if raw.startswith("--- ") and hunk is None or (raw.startswith("--- ") and current is None):
            old_path = _strip_prefix(raw[4:])
            pending_header.append(raw)
            hunk = None
            continue
        if raw.startswith("+++ ") and hunk is None:
            new_path = _strip_prefix(raw[4:])
            pending_header.append(raw)
            current = FilePatch(old_path=old_path, new_path=new_path, header=list(pending_header))
            patch.files.append(current)
            continue
        m = _HUNK_RE.match(raw)
        if m and current is not None:
            hunk = Hunk(
                index=index,
                old_path=current.old_path,
                new_path=current.new_path,
                old_start=int(m.group(1)),
                old_count=int(m.group(2) or 1),
                new_start=int(m.group(3)),
                new_count=int(m.group(4) or 1),
                heading=m.group(5),
            )
            index += 1
            current.hunks.append(hunk)
            continue
        if hunk is not None and raw[:1] in (" ", "+", "-", "\\"):
            hunk.lines.append(raw)
            continue
        if hunk is not None and raw == "":
            # A context line whose text is empty, written without the space.
            hunk.lines.append(" ")
            continue
        if current is None or hunk is None:
            pending_header.append(raw)
    return patch


def render_patch(patch: Patch, keep: set[int] | None = None) -> str:
    """Render the patch keeping only hunks whose ``index`` is in ``keep``.

    ``keep=None`` renders everything. New-side line numbers are recomputed so
    that the subset applies cleanly with ``git apply``.
    """
    out: list[str] = []
    for fp in patch.files:
        chosen = [h for h in fp.hunks if keep is None or h.index in keep]
        if not chosen:
            continue
        out.extend(fp.header)
        shift = 0
        for h in chosen:
            new_start = h.old_start + shift
            if h.old_count == 0:
                new_start = h.old_start + shift + 1
            old_part = f"{h.old_start},{h.old_count}" if h.old_count != 1 else str(h.old_start)
            new_part = f"{new_start},{h.new_count}" if h.new_count != 1 else str(new_start)
            out.append(f"@@ -{old_part} +{new_part} @@{h.heading}")
            out.extend(h.lines)
            shift += h.delta
    if not out:
        return ""
    return "\n".join(out) + "\n"
