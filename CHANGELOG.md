# Changelog

## Unreleased

### Added

- Three more real bug-fix commits in the results: toolz `d2eba03` and attrs `af9c510` and `ce89f5d`, next to the three from tabulate and more-itertools, all six run with `--jobs 1` and `--jobs 4`.
- `examples/reproduce_real_commits.sh`, which clones the libraries, makes a test environment with pinned pytest and hypothesis versions, runs every case with both job counts and prints the results table and the hunk verdicts.
- `examples/real_commits.py`: per-case extra pytest arguments for the test and rest commands, the job count and load average in each saved result, `--summary` over several result folders, `--hunks` to list every hunk with its verdict, and offline tests for these.

### Changed

- Saved real-commit results moved from `results/` to `results/jobs1/` and `results/jobs4/`.

## 0.1.0 - 2026-11-06

### Added

- Unified-diff parsing into hunks, read as bytes and split on `\n` only, with hunk ends taken from the `@@` line counts, so `git diff`, `diff -ru` and `git format-patch` output all parse; any subset of hunks renders as a diff that applies with `git apply`.
- ddmin over hunks (working on positions, so repeated items keep 1-minimality), with an optional second pass over the change lines of each needed hunk (`--lines`) that pairs the k-th removed line with the k-th added line.
- Property tests for 1-minimality and the n^2 + 3n trial bound on random non-monotone oracles, and a test that renders every subset of a small patch and checks it with `git apply --check`.
- A git-worktree test oracle: a rest-of-suite command, timeouts that kill the whole process group, reruns that flag flaky outcomes, and parallel worktrees (`--jobs`) whose free workers try the first complements of a round speculatively.
- Clean trials: tracked files restored, untracked files deleted, ignored Python bytecode removed between trials (`--clean-ignored` removes every ignored file), and test commands run with `PYTHONDONTWRITEBYTECODE=1`.
- A content-hashed run cache, in memory or in a JSON file written atomically, keyed on the commit id that `--base` resolves to, the commands, the targets and the patch text.
- Pinned paths (`--pin`), uncached confirmation runs of the final patch (`--confirm`), and fail-to-pass target discovery for pytest (`--find-targets`).
- Hunk kinds in the report: docs, test, whitespace, comment, rename, code.
- `--context N` for finer hunks (zero-context diffs are applied with `git apply --unidiff-zero`), `--patch` to read a diff file, and `--json` and `--markdown` reports.
- Handling of files with no hunks (new or deleted empty files, mode changes), deleted files labelled by their old path, and an error before any run for patches that contain a rename, a copy or a binary change.
- A command line that checks `--repo`, `--base`, `--fix` and `--patch` before any run and prints single `error:` lines on stderr; exit code 1 for usage errors and 2 for a failed confirmation.
- Examples: a fixture walk-through (`examples/run_fixture.py`), a Python API example, a ddmin cost simulation with a chart, and a run on three real bug-fix commits from tabulate and more-itertools with saved JSON results (`examples/real_commits.py`, `results/`).
- README with the algorithm, a cost analysis, a command reference, the real-data results and a Limitations section.

### Known limitations

The search gives a 1-minimal subset, not a global minimum, and `--find-targets` supports pytest only; see the Limitations section of the README for the full list.
