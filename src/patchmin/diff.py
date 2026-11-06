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
        if raw.startswith("diff --git "):
            current = None
            hunk = None
            old_path = new_path = ""
            pending_header = [raw]
            continue
        if raw.startswith("--- ") and (hunk is None or current is None):
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
        if hunk is None:
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


def change_lines(hunk: Hunk) -> list[int]:
    """Positions of the ``+`` and ``-`` lines inside ``hunk.lines``."""
    return [i for i, ln in enumerate(hunk.lines) if ln[:1] in ("+", "-")]


def hunk_with_lines(hunk: Hunk, keep: set[int]) -> Hunk:
    """Copy ``hunk`` keeping only the change lines at positions in ``keep``.

    A dropped ``+`` line disappears; a dropped ``-`` line becomes context, so
    the old text stays. Counts are recomputed.
    """
    lines: list[str] = []
    for i, ln in enumerate(hunk.lines):
        tag = ln[:1]
        if tag in ("+", "-") and i not in keep:
            if tag == "-":
                lines.append(" " + ln[1:])
            continue
        lines.append(ln)
    old_count = sum(1 for ln in lines if ln[:1] in (" ", "-"))
    new_count = sum(1 for ln in lines if ln[:1] in (" ", "+"))
    return Hunk(
        index=hunk.index,
        old_path=hunk.old_path,
        new_path=hunk.new_path,
        old_start=hunk.old_start,
        old_count=old_count,
        new_start=hunk.new_start,
        new_count=new_count,
        lines=lines,
        heading=hunk.heading,
    )


def with_hunk(patch: Patch, replacement: Hunk) -> Patch:
    """A copy of ``patch`` where the hunk with the same index is replaced."""
    files = []
    for fp in patch.files:
        hunks = [replacement if h.index == replacement.index else h for h in fp.hunks]
        files.append(FilePatch(fp.old_path, fp.new_path, list(fp.header), hunks))
    return Patch(files)


_DOC_SUFFIXES = (".md", ".rst", ".txt", ".adoc")
_PY_COMMENT = re.compile(r"^\s*#")
_DOCSTRING_FENCE = re.compile(r'^\s*[rRbBuU]{0,2}("""|\'\'\')')


def _is_test_path(path: str) -> bool:
    parts = path.split("/")
    name = parts[-1]
    return (
        any(p in ("test", "tests", "testing") for p in parts[:-1])
        or name.startswith("test_")
        or name.endswith(("_test.py", "_tests.py", ".test.js", ".test.ts", ".spec.js", ".spec.ts"))
    )


def classify_hunk(hunk: Hunk) -> str:
    """A rough label for what a hunk changes: ``docs``, ``test``, ``whitespace``,
    ``comment`` or ``code``.

    The label is a hint for reading the report and plays no part in the
    search. ``docs`` is a hunk in a documentation file; ``test`` a hunk in a
    test file; ``whitespace`` a hunk whose added and removed lines are equal
    once whitespace is removed; ``comment`` a hunk whose changed lines are
    all ``#`` comments or docstring fences; anything else is ``code``.
    """
    path = hunk.new_path or hunk.old_path
    if path.endswith(_DOC_SUFFIXES) or path.startswith(("docs/", "doc/")):
        return "docs"
    if _is_test_path(path):
        return "test"
    added = [ln[1:] for ln in hunk.lines if ln.startswith("+")]
    removed = [ln[1:] for ln in hunk.lines if ln.startswith("-")]
    squash = [re.sub(r"\s+", "", ln) for ln in added]
    squash_removed = [re.sub(r"\s+", "", ln) for ln in removed]
    if squash == squash_removed and (added or removed):
        return "whitespace"
    changed = [ln for ln in added + removed if ln.strip()]
    if changed and all(_PY_COMMENT.match(ln) or _DOCSTRING_FENCE.match(ln) for ln in changed):
        return "comment"
    if changed and _docstring_only(hunk):
        return "comment"
    return "code"


def _fences(text: str) -> int:
    return text.count('"""') + text.count("'''")


def _starts_inside_docstring(hunk: Hunk) -> bool:
    """Guess whether the hunk's first line sits inside a docstring.

    The hunk does not show what came before it, so the first line with an
    odd number of triple quotes decides: a fence followed by text opens a
    docstring, text followed by a fence closes one, and a bare fence opens
    one when it is the hunk's first line or the last non-blank line before
    it ends with a colon (a def or class header). A hunk with no fence is
    taken to start outside, so a hunk wholly inside a long docstring is
    labelled ``code``.
    """
    previous = ""
    for i, ln in enumerate(hunk.lines):
        text = ln[1:].strip()
        if _fences(text) % 2 == 1:
            fence = text[:3] if text[:3] in ('"""', "'''") else None
            if text in ('"""', "'''"):
                if not previous:
                    # Only blank lines before it: a closing fence after the
                    # usual blank line, unless it is the very first line.
                    return i > 0
                return not previous.endswith(":")
            return fence is None
        if text:
            previous = text
    return False


def _docstring_only(hunk: Hunk) -> bool:
    """True when every changed line of ``hunk`` sits inside a docstring.

    Tracks triple-quote fences through the hunk, starting from the guess of
    ``_starts_inside_docstring``; a changed line counts when it is inside a
    docstring or is a fence line itself.
    """
    inside = _starts_inside_docstring(hunk)
    for ln in hunk.lines:
        tag, text = ln[:1], ln[1:]
        is_fence = _fences(text) % 2 == 1
        if tag in ("+", "-") and text.strip() and not (inside or is_fence):
            return False
        if is_fence:
            inside = not inside
    return True


def matches_any(path: str, patterns: list[str]) -> bool:
    """fnmatch ``path`` against ``patterns``; a pattern with no slash also
    matches any path component."""
    from fnmatch import fnmatch

    for pat in patterns:
        if fnmatch(path, pat):
            return True
        if "/" not in pat and any(fnmatch(part, pat) for part in path.split("/")):
            return True
        if pat.endswith("/") and path.startswith(pat):
            return True
    return False
