"""Parse unified diffs into hunks and render subsets of hunks back out."""

from __future__ import annotations

import builtins
import keyword
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
        """Number of ``+`` lines."""
        return sum(1 for ln in self.lines if ln.startswith("+"))

    @property
    def removed(self) -> int:
        """Number of ``-`` lines."""
        return sum(1 for ln in self.lines if ln.startswith("-"))

    @property
    def delta(self) -> int:
        """Change in line count when this hunk is applied."""
        return self.added - self.removed

    def label(self) -> str:
        """``path:old_start``, as printed in the report."""
        return f"{self.new_path}:{self.old_start}"


_EXTENDED = (
    "old mode ", "new mode ", "deleted file mode ", "new file mode ", "similarity index ",
    "dissimilarity index ", "rename from ", "rename to ", "copy from ", "copy to ", "index ",
    "Binary files ", "GIT binary patch",
)
_MODE = ("old mode ", "new mode ")


@dataclass
class FilePatch:
    """All hunks of one file plus the header lines that precede them.

    A file entry with no hunks (a new or deleted empty file, a mode change,
    a pure rename) has only its ``diff --git`` header.
    """

    old_path: str
    new_path: str
    header: list[str] = field(default_factory=list)
    hunks: list[Hunk] = field(default_factory=list)

    @property
    def header_only(self) -> bool:
        """True for an entry with no hunks."""
        return not self.hunks

    def mode_header(self) -> list[str]:
        """The ``diff --git`` line and mode lines, or ``[]`` when the mode
        does not change. Applied on its own, it changes only the mode."""
        modes = [ln for ln in self.header if ln.startswith(_MODE)]
        first = next((ln for ln in self.header if ln.startswith("diff --git ")), None)
        return [first, *modes] if modes and first is not None else []


@dataclass
class Patch:
    """A parsed diff: its files in order, each with its hunks."""

    files: list[FilePatch] = field(default_factory=list)

    @property
    def hunks(self) -> list[Hunk]:
        """Every hunk of every file, in patch order."""
        return [h for f in self.files for h in f.hunks]

    def unsupported(self) -> str | None:
        """Why the patch cannot be searched, or None.

        A rename or copy travels with the hunks of its file, so dropping a
        hunk would also drop the rename; a binary change cannot be split.
        Both stop the search rather than give a wrong verdict.
        """
        for fp in self.files:
            if any(ln.startswith(("rename from ", "copy from ")) for ln in fp.header):
                return (
                    f"the patch renames or copies {fp.old_path}; make it with "
                    "git diff --no-renames"
                )
            if any(ln.startswith(("Binary files ", "GIT binary patch")) for ln in fp.header):
                return f"the patch has a binary change to {fp.new_path}, which is not supported"
        return None


def decode_patch(data: bytes) -> str:
    """Decode diff bytes as UTF-8, keeping other bytes as surrogate escapes."""
    return data.decode("utf-8", errors="surrogateescape")


def encode_patch(text: str) -> bytes:
    """The inverse of :func:`decode_patch`."""
    return text.encode("utf-8", errors="surrogateescape")


def _strip_prefix(path: str) -> str:
    path = path.split("\t", 1)[0].strip()
    if path.startswith(("a/", "b/")):
        return path[2:]
    return path


def _git_paths(line: str) -> tuple[str, str]:
    """Old and new path from a ``diff --git a/x b/y`` line."""
    rest = line[len("diff --git ") :]
    if rest.startswith("a/") and " b/" in rest:
        old, new = rest[2:].split(" b/", 1)
        return old, new
    parts = rest.split(" ", 1)
    return parts[0], parts[-1]


def parse_patch(text: str) -> Patch:
    """Parse a unified diff into a Patch.

    Accepts ``git diff`` output, ``git format-patch`` mails and plain
    ``diff -u``/``diff -ru`` output. A file starts at a ``--- `` line
    followed by a ``+++ `` line; a hunk takes exactly the lines its ``@@``
    header counts (plus ``\\ No newline`` markers), so text after it, such
    as a mail signature or the next file's header, is never read as part of
    the hunk. A ``diff --git`` entry with no ``---``/``+++`` lines (a new
    or deleted empty file, a mode change, a pure rename, a binary change)
    becomes a FilePatch with no hunks. Lines keep a trailing ``\\r``; read
    the diff with :func:`decode_patch` so that bytes that are not UTF-8
    survive.
    """
    patch = Patch()
    current: FilePatch | None = None
    index = 0
    pending_header: list[str] = []
    # The extended header of the latest diff --git line, until a ---/+++
    # pair claims it; None once claimed. ``open_git`` is False after the
    # first line that is not an extended header line.
    git_header: list[str] | None = None
    open_git = False

    def close_git() -> None:
        nonlocal git_header
        if git_header is not None and len(git_header) > 1:
            old, new = _git_paths(git_header[0])
            for ln in git_header[1:]:
                if ln.startswith(("rename from ", "copy from ")):
                    old = ln.split(" ", 2)[2]
                elif ln.startswith(("rename to ", "copy to ")):
                    new = ln.split(" ", 2)[2]
            patch.files.append(FilePatch(old, new, list(git_header)))
        git_header = None

    # Split on "\n" only: str.splitlines also splits on "\r", form feeds,
    # U+2028 and other characters that can sit inside a line of a file.
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    i = 0
    while i < len(lines):
        raw = lines[i]
        i += 1
        if raw.startswith("diff --git "):
            close_git()
            current = None
            pending_header = [raw]
            git_header, open_git = [raw], True
            continue
        if raw.startswith("--- ") and i < len(lines) and lines[i].startswith("+++ "):
            plus = lines[i]
            i += 1
            git_header, open_git = None, False
            current = FilePatch(
                old_path=_strip_prefix(raw[4:]),
                new_path=_strip_prefix(plus[4:]),
                header=[*pending_header, raw, plus],
            )
            patch.files.append(current)
            pending_header = []
            continue
        if open_git and git_header is not None and raw.startswith(_EXTENDED):
            git_header.append(raw)
            pending_header.append(raw)
            continue
        open_git = False
        m = _HUNK_RE.match(raw)
        if m is None or current is None:
            pending_header.append(raw)
            continue
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
        old_left, new_left = hunk.old_count, hunk.new_count
        while i < len(lines):
            ln = lines[i]
            tag = ln[:1]
            if tag == "\\" and hunk.lines:
                hunk.lines.append(ln)
            elif old_left == 0 and new_left == 0:
                break
            elif tag == "-" and old_left > 0:
                hunk.lines.append(ln)
                old_left -= 1
            elif tag == "+" and new_left > 0:
                hunk.lines.append(ln)
                new_left -= 1
            elif tag in (" ", "") and old_left > 0 and new_left > 0:
                # An empty line is a context line whose text is empty,
                # written without the space.
                hunk.lines.append(ln or " ")
                old_left -= 1
                new_left -= 1
            else:
                break
            i += 1
    close_git()
    return patch


def render_patch(patch: Patch, keep: set[int] | None = None) -> str:
    """Render the patch keeping only hunks whose ``index`` is in ``keep``.

    ``keep=None`` renders everything. New-side line numbers are recomputed so
    that the subset applies cleanly with ``git apply``. File entries with no
    hunks, and the mode change of a file whose hunks are all dropped, are
    rendered whatever ``keep`` says: they are part of every trial.
    """
    out: list[str] = []
    for fp in patch.files:
        if fp.header_only:
            out.extend(fp.header)
            continue
        chosen = [h for h in fp.hunks if keep is None or h.index in keep]
        if not chosen:
            out.extend(fp.mode_header())
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


def lines_searchable(hunk: Hunk) -> bool:
    """False for a hunk that deletes its whole file.

    Keeping only some of its ``-`` lines leaves a deleted file with contents,
    which ``git apply`` rejects, so such a hunk is all or nothing.
    """
    return hunk.new_path != "/dev/null"


NO_NEWLINE = "\\ No newline at end of file"


def _items(hunk: Hunk) -> list[tuple[int, str, bool]]:
    """``(position, line, no_newline)`` for every line of ``hunk`` that is not
    a ``\\`` marker; ``no_newline`` is true when a marker follows the line."""
    items: list[tuple[int, str, bool]] = []
    for i, ln in enumerate(hunk.lines):
        if ln.startswith("\\"):
            if items:
                items[-1] = (items[-1][0], items[-1][1], True)
            continue
        items.append((i, ln, False))
    return items


def hunk_with_lines(hunk: Hunk, keep: set[int]) -> Hunk:
    """Copy ``hunk`` keeping only the change lines at positions in ``keep``.

    A dropped ``+`` line disappears; a dropped ``-`` line becomes context, so
    the old text stays. Inside each run of change lines the k-th ``-`` line
    is paired with the k-th ``+`` line, and a kept old line is placed where
    its partner goes: keeping ``-a = 1`` and ``+a = 2`` of a two-line
    replacement leaves the second old line after ``a = 2``, as in the new
    file, rather than before it. ``\\ No newline`` markers stay with their
    line and are moved or dropped so that only the last line of each side
    lacks a newline. Counts are recomputed.
    """
    marker = next((ln for ln in hunk.lines if ln.startswith("\\")), NO_NEWLINE)
    if set(change_lines(hunk)) <= keep:
        out = list(hunk.lines)
    else:
        items: list[tuple[str, str, bool]] = []
        block: list[tuple[int, str, bool]] = []

        def flush() -> None:
            minus = [b for b in block if b[1].startswith("-")]
            plus = [b for b in block if b[1].startswith("+")]
            for k in range(max(len(minus), len(plus))):
                if k < len(minus):
                    pos, ln, eol = minus[k]
                    items.append(("-" if pos in keep else " ", ln[1:], eol))
                if k < len(plus) and plus[k][0] in keep:
                    items.append(("+", plus[k][1][1:], plus[k][2]))
            block.clear()

        for item in _items(hunk):
            if item[1][:1] in ("+", "-"):
                block.append(item)
            else:
                flush()
                items.append((" ", item[1][1:], item[2]))
        flush()
        last_new = max((i for i, it in enumerate(items) if it[0] != "-"), default=-1)
        out = []
        for i, (tag, text, no_newline) in enumerate(items):
            if no_newline and tag == " " and i != last_new:
                # The old file's last line, with lines added after it: the
                # new side needs it with a newline.
                out += ["-" + text, marker, "+" + text]
                continue
            out.append(tag + text)
            if no_newline and (tag == "-" or i == last_new):
                out.append(marker)
    old_count = sum(1 for ln in out if ln[:1] in (" ", "-"))
    new_count = sum(1 for ln in out if ln[:1] in (" ", "+"))
    return Hunk(
        index=hunk.index,
        old_path=hunk.old_path,
        new_path=hunk.new_path,
        old_start=hunk.old_start,
        old_count=old_count,
        new_start=hunk.new_start,
        new_count=new_count,
        lines=out,
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
    ``comment``, ``rename`` or ``code``.

    The label is a hint for reading the report and plays no part in the
    search. ``docs`` is a hunk in a documentation file; ``test`` a hunk in a
    test file; ``whitespace`` a hunk whose added and removed lines are equal
    once whitespace is removed; ``comment`` a hunk whose changed lines are
    all ``#`` comments or docstring fences, or sit inside a docstring;
    ``rename`` a hunk whose removed and added lines are the same token for
    token except for identifiers that map one to one (old name to new name);
    anything else is ``code``.
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
    if _is_rename(removed, added):
        return "rename"
    return "code"


_TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|\d+|\S")
_NOT_NAMES = frozenset(keyword.kwlist) | frozenset(dir(builtins))


def _is_name(token: str) -> bool:
    return (token[0].isalpha() or token[0] == "_") and token not in _NOT_NAMES


def _is_rename(removed: list[str], added: list[str]) -> bool:
    """True when ``added`` is ``removed`` with identifiers renamed one to one.

    Lines are compared pairwise after splitting into tokens; every token pair
    must be equal or a pair of identifiers, the mapping old to new must be
    consistent in both directions, and at least one identifier must change.
    Keywords and builtins (``True``, ``None``, ``and``, ``min``, ``len``) are
    not identifiers here: swapping one for another changes the logic.
    """
    if not removed or len(removed) != len(added):
        return False
    forward: dict[str, str] = {}
    backward: dict[str, str] = {}
    for old_line, new_line in zip(removed, added, strict=True):
        old, new = _TOKEN.findall(old_line), _TOKEN.findall(new_line)
        if len(old) != len(new):
            return False
        for a, b in zip(old, new, strict=True):
            is_name = _is_name(a) and _is_name(b)
            if a != b and not is_name:
                return False
            if is_name and (forward.setdefault(a, b) != b or backward.setdefault(b, a) != a):
                return False
    return any(a != b for a, b in forward.items())


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
