# Changelog

## 0.1.0

- Unified-diff parsing into hunks and rendering of any subset of hunks as a
  diff that applies with `git apply`.
- ddmin over hunks, with an optional second pass over the change lines of
  each needed hunk; property tests for 1-minimality and the n^2 + 3n trial
  bound on random non-monotone oracles.
- A git-worktree test oracle: rest-of-suite command, timeouts that kill the
  whole process group, reruns that flag flaky outcomes, and parallel
  worktrees (`--jobs`).
- A content-hashed run cache, in memory or in a JSON file written atomically.
- Pinned paths (`--pin`), uncached confirmation runs of the final patch
  (`--confirm`) and fail-to-pass target discovery for pytest
  (`--find-targets`).
- Hunk kinds in the report: docs, test, whitespace, comment, rename, code.
- Examples: a fixture walk-through, a ddmin cost simulation with a chart, and
  a run on three real bug-fix commits from tabulate and more-itertools.
