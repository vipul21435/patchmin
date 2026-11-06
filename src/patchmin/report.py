"""Render a minimisation result as a Markdown report."""

from __future__ import annotations

from .core import MinimizeResult


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("`", "'").strip()


def markdown_report(result: MinimizeResult, title: str = "patchmin report") -> str:
    """A Markdown page with the counts, one row per hunk, the line verdicts
    and the minimal patch."""
    out = [f"# {title}", ""]
    if result.error:
        out += [f"Error: {result.error}", ""]
        return "\n".join(out)
    searched = len(result.verdicts) - len(result.pinned)
    out += [
        f"- Hunks needed: {len(result.needed)} of {searched}"
        + (f" ({len(result.pinned)} pinned)" if result.pinned else ""),
        f"- Test runs: {result.runs} ({result.cached_hits} cache hits, "
        f"{result.flaky_runs} flaky)",
        f"- ddmin trials: {result.trace.tests}",
        f"- Wall time: {result.seconds:.1f} s",
    ]
    if result.unresolved:
        parts = ", ".join(f"{k}: {v}" for k, v in result.unresolved.items())
        out.append(f"- Unresolved subsets: {parts}")
    if result.confirm_outcomes:
        verdict = "confirmed" if result.confirmed else "not confirmed"
        out.append(f"- Minimal patch {verdict}: {', '.join(result.confirm_outcomes)}")
    out += [
        "",
        "| hunk | status | kind | location | lines | context |",
        "|---|---|---|---|---|---|",
    ]
    for v in result.verdicts:
        out.append(
            f"| {v.index} | {v.status} | {v.kind} | `{_cell(v.label)}` | "
            f"+{v.added}/-{v.removed} | {_cell(v.heading)} |"
        )
    if result.line_verdicts:
        kept = sum(1 for lv in result.line_verdicts if lv.needed)
        out += ["", f"Change lines needed: {kept} of {len(result.line_verdicts)}", ""]
        out += ["| hunk | line | status | text |", "|---|---|---|---|"]
        for lv in result.line_verdicts:
            status = "needed" if lv.needed else "not needed"
            out.append(f"| {lv.hunk} | {lv.position} | {status} | `{_cell(lv.text)}` |")
    out += ["", "## Minimal patch", "", "```diff", result.minimal_patch.rstrip("\n"), "```", ""]
    return "\n".join(out)
