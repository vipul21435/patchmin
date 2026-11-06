# Changelog

## Unreleased

- Ignored `__pycache__` directories and `.pyc` files are deleted between
  trials and test commands run with `PYTHONDONTWRITEBYTECODE=1`; before,
  a same-size edit could run the previous trial's bytecode and give a
  wrong verdict. `--clean-ignored` deletes every ignored file.
- Hunks end where their `@@` line counts say, so plain `diff -ru` patches
  with several files and `git format-patch` mails (with their `-- `
  signature) parse correctly.
- Diffs are read as bytes and split on `\n` only: CRLF files, form feeds,
  U+2028 and bytes that are not UTF-8 no longer break the run. Test output
  that is not UTF-8 is decoded with replacement characters.
- The line pass pairs the k-th `-` line of a run of changes with the k-th
  `+` line, so a kept old line sits where its replacement would go and
  fewer lines are reported as needed.
- `--find-targets` builds node ids from the junit class name, so test
  methods inherited from a class in another module keep their own class.
- Cache entries ignored for a shorter timeout or fewer reruns are no longer
  counted as hits; targets are encoded as a JSON list in the cache key
  (older cache files no longer match).
- Unbalanced quotes in `--test-cmd`/`--rest-cmd` and a `--cache` file that
  is not JSON give one `error:` line; all errors go to stderr.
- Keyword, constant and builtin swaps (`True` to `False`, `min` to `max`)
  are labelled `code`, not `rename`.
- The run cache is keyed on the commit id `--base` resolves to, not on the
  name as typed, and a cached pass or fail recorded with fewer `--reruns`
  than asked for is run again.
- ddmin works on positions, so inputs with repeated items keep
  1-minimality.
- The temporary folder that holds the worktrees is removed on close.
- A test program that does not exist gives an unresolved run with the
  reason `command not found`, which is not cached.
- The command line checks `--repo`, `--base`, `--fix` and `--patch` before
  any run, rejects negative counts, and prints `error:` lines instead of
  tracebacks.
- The line pass keeps a hunk that deletes a whole file whole.
- A test renders every subset of hunks and lines of a small patch and checks
  each with `git apply --check`.

## 0.1.0

- Unified-diff parsing into hunks and rendering of any subset of hunks as a
  diff that applies with `git apply`.
- ddmin over hunks, with an optional second pass over the change lines of
  each needed hunk; property tests for 1-minimality and the n^2 + 3n trial
  bound on random non-monotone oracles.
- A git-worktree test oracle: rest-of-suite command, timeouts that kill the
  whole process group, reruns that flag flaky outcomes, and parallel
  worktrees (`--jobs`) whose free workers try the first complements of a
  round speculatively.
- A content-hashed run cache, in memory or in a JSON file written atomically.
- Pinned paths (`--pin`), uncached confirmation runs of the final patch
  (`--confirm`) and fail-to-pass target discovery for pytest
  (`--find-targets`).
- Hunk kinds in the report: docs, test, whitespace, comment, rename, code.
- `--context N` for finer hunks (zero-context diffs are applied with
  `git apply --unidiff-zero`) and a `--markdown` report.
- Examples: a fixture walk-through, a ddmin cost simulation with a chart, and
  a run on three real bug-fix commits from tabulate and more-itertools.
