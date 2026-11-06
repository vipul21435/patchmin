# patchmin

Find the part of a fix that actually fixes it: delta debugging over the hunks (and optionally the lines) of a patch, against a test command, down to a 1-minimal subset that still turns the target tests green while the rest of the suite stays green.

## Why this is hard

A bug-fix commit rarely contains only the fix. It carries docstring edits, renamed variables, reformatting, a changelog line and sometimes a second unrelated change. Reading the diff does not tell you which hunks the failing tests depend on, and trying subsets by hand is slow: a patch with n hunks has 2^n subsets, and every trial is a checkout, an apply and a test run. Test runs are also noisy: a subset may fail to apply because it depends on a hunk that was dropped, a test may be flaky, and a run may hang. The tool has to search the subsets with few runs, treat "did not apply" and "timed out" as a third outcome rather than a failure, never run the same subset twice, and confirm what it reports.

## Install

```
pip install git+https://github.com/vipul21435/patchmin
```

Python 3.10 or newer; no runtime dependencies beyond the standard library and `git` on the `PATH`. For development:

```
uv venv --python 3.12 .venv
uv pip install -e ".[dev]"
uv run pytest -q
```

On 2026-11-06 `uv run pytest -q --cov=patchmin` ran 121 tests, offline, with 98 % line coverage of `src/patchmin` (the tests build their own git repositories in temporary folders). The laptop was busy with other work (1-minute load average between 8 and 27): that run took 70 s, and `uv run pytest -q` took 43 s right after it.

## Quickstart

`examples/run_fixture.py` builds a two-commit git repository in a temporary folder: a small `calc.py` with a wrong `add`, then a fix commit of four hunks (a module docstring edit, the real fix together with a comment edit, a function docstring edit, a rewrite of `neg`). It then runs the command line tool on it. Output of `uv run python examples/run_fixture.py` on 2026-11-06:

```
$ patchmin --repo <repo> --base <base> --fix <fix> --lines \
      --test-cmd 'python -c "import calc; assert calc.add(2, 3) == 5 and calc.mul(2, 3) == 6 and calc.neg(4) == -4"'
not needed  hunk 0   comment    calc.py:1                        +1/-1
needed      hunk 1   code       calc.py:11                       +2/-2  PAD = [
not needed  hunk 2   comment    calc.py:25                       +1/-1  PAD2 = [
not needed  hunk 3   code       calc.py:39                       +1/-1  PAD3 = [
not needed  hunk 1   line 3   -    # adds two numbers
needed      hunk 1   line 4   -    return a - b
not needed  hunk 1   line 5   +    # adds two numbers, now correctly
needed      hunk 1   line 6   +    return a + b
2 of 4 change lines needed
1 of 4 hunks needed; 14 test runs (6 cache hits, 0 flaky, 0 timed out, 0 did not apply) in 0.7s
minimal patch confirmed: pass
```

The general form:

```
patchmin --repo PATH --base COMMIT (--fix COMMIT | --patch FILE) --test-cmd CMD
    [--context N] [--target TEST ...] [--find-targets] [--pin GLOB ...] [--rest-cmd CMD]
    [--lines] [--clean-ignored] [--jobs N] [--reruns K] [--confirm C] [--timeout SECONDS]
    [--cache runs.json] [--json] [--out minimal.diff] [--markdown report.md]
```

| Option | Meaning |
|---|---|
| `--fix` / `--patch` | the fix as a commit (the patch is `git diff BASE FIX`) or as a unified diff file |
| `--context N` | context lines for `git diff` with `--fix` (default 3); `0` gives one hunk per run of adjacent changed lines, applied with `git apply --unidiff-zero` |
| `--test-cmd` | the test command; `--target` values are appended to it |
| `--find-targets` | pytest only: run the tests before and after the fix and use the tests that fail before and pass after as the targets |
| `--pin GLOB` | hunks in matching files are applied in every trial and never searched (typically the test files of the fix) |
| `--rest-cmd` | a second command that must also pass for a subset to count, so the rest of the suite stays green |
| `--lines` | after the hunk search, minimise the `+`/`-` lines inside each needed hunk |
| `--clean-ignored` | delete every ignored file between trials (`git clean -X`); by default only ignored `__pycache__` directories and `.pyc` files are deleted |
| `--jobs N` | run up to N subsets at once, each in its own git worktree (default 1) |
| `--reruns K` | run every fresh outcome K more times; disagreement marks it flaky (default 0) |
| `--confirm C` | run the final minimal patch C more times without the cache (default 1); exit code 2 if any of them fails |
| `--timeout` | seconds per command (default 600); a run that hits it is unresolved |
| `--cache FILE` | keep outcomes in a JSON file, so a second invocation reuses them |
| `--json`, `--out` | print the result as JSON; write the minimal patch as a diff that `git apply` accepts on `BASE` |
| `--markdown FILE` | write a Markdown report: counts, one row per hunk, the line verdicts and the minimal patch |

Exit codes: 0 when the minimal patch is found (and confirmed), 2 when a confirming run fails, 1 on an error. A `--repo` that is not a git work tree, a `--base` or `--fix` that names no commit, or a missing `--patch` file stops the run before any test runs, with one `error:` line on stderr; negative counts, `--jobs 0` and a non-positive `--timeout` are rejected by the argument parser. A test program that does not exist makes the run unresolved with the reason `command not found`, and such runs are not cached.

From Python (`uv run python examples/python_api.py`, abridged output below the code):

```python
from patchmin import RunCache, WorktreeOracle, minimize_text

with WorktreeOracle(repo, base, test_cmd, cache=RunCache(), jobs=2) as oracle:
    result = minimize_text(diff, oracle, lines=True, confirm=1)
print(result.needed, result.runs, result.cached_hits, result.confirmed)
print(result.minimal_patch)
```

```
needed hunks: [1]
kinds: [(0, 'comment', 'not needed'), (1, 'code', 'needed'), (2, 'comment', 'not needed'), (3, 'code', 'not needed')]
runs: 19 cache hits: 6 confirmed: True
@@ -11,8 +11,8 @@ PAD = [
 ]

 def add(a, b):
     # adds two numbers
-    return a - b
+    return a + b
```

With `jobs=2` the run count is 19 rather than the 14 of the sequential command above: each parallel round runs every chunk of the round.

Every hunk is printed with a status (`needed`, `not needed`, `pinned`) and a kind (`docs`, `test`, `whitespace`, `comment`, `rename`, `code`). The kind is a hint for reading the report; it plays no part in the search.

## How it works

1. **Hunks.** The patch is parsed into hunks, numbered across files. A subset of hunks is rendered back into a unified diff; new-side line numbers are recomputed from the cumulative line delta of the hunks kept before it, so any subset of a file's hunks applies cleanly.
2. **Oracle.** A trial applies the rendered diff with `git apply` in a detached worktree at `BASE` (after `git checkout -- .` and `git clean -fdq`), runs the test command with the targets appended and, if that passes, the rest command. Each command runs in its own process group; on timeout the whole group is killed, so test runners that start workers leave nothing behind. Exit code 0 is a pass, any other code a fail; a diff that does not apply or a command that times out is *unresolved*, which ddmin treats like a fail (the subset is not kept).
3. **Cache.** Every outcome is stored under `SHA-256(base commit id, test command, targets, rest command, rendered diff)`, with a NUL byte after each part. `--base` is resolved to its full commit id first, so a name such as `HEAD~1` that later points at another commit does not reuse old outcomes. Equal subsets are never run twice, within a search, across the hunk and line passes, or across invocations with `--cache`. A cached timeout is ignored when the current `--timeout` is longer than the one it was recorded with. A cached pass or fail is ignored when it was recorded with fewer `--reruns` than the current run asks for. The file is written to a sibling and renamed, so an interrupted run does not corrupt it.
4. **Flakiness.** With `--reruns K`, a fresh pass or fail is run K more times; if any rerun disagrees, the subset is recorded as flaky and unresolved. The report counts distinct flaky subsets.
5. **Pre-checks.** The full patch must pass and the pinned hunks alone (the empty patch when nothing is pinned) must fail; otherwise the tool says which and stops with exit code 1.
6. **Search.** ddmin, the minimising delta debugging algorithm of Zeller and Hildebrandt, "Simplifying and Isolating Failure-Inducing Input", IEEE Transactions on Software Engineering 28(2), 183-200, 2002 (https://doi.org/10.1109/32.988498). The property kept here is "the target tests pass". Starting with granularity n = 2, the current set is split into n chunks; if some chunk alone passes, it becomes the current set and n resets to 2; otherwise, if the complement of some chunk passes, it becomes the current set and n becomes max(n - 1, 2); otherwise n doubles, up to the size of the set. With two chunks the complements are the chunks themselves and are not tried again. The loop ends when n equals the size of the set and no chunk or complement passes; the result is then 1-minimal: removing any single hunk makes the tests fail. Proposition 12 of the paper bounds the number of trials by n² + 3n for n hunks; the property tests check that bound and 1-minimality on random non-monotone oracles.
7. **Parallel worktrees.** With `--jobs N`, all chunks of one round are run at once, one worktree each, and the first passing one in order is taken. When a round has fewer chunks than N, the free worktrees run the first complements of the same round speculatively; the remaining complements follow only if no chunk and no speculative complement passes. The answer is the same as the sequential search; the parallel search may run subsets that the sequential one would have skipped.
8. **Lines.** With `--lines`, each needed hunk is refined by a second ddmin over its `+` and `-` lines, one hunk at a time with the other needed hunks kept whole. A dropped `+` line disappears and a dropped `-` line turns back into context, so every trial is still a valid diff; `tests/test_render_subsets.py` checks this with `git apply --check` for every subset of hunks and every non-empty subset of lines in each hunk of a two-file patch with a new file and a deleted file, at 3 and 0 context lines. A hunk that deletes a whole file is kept whole: keeping some of its `-` lines would leave a deleted file with contents, which `git apply` rejects.
9. **Confirmation.** The final minimal patch is run `--confirm` more times, bypassing the cache. This catches a cached pass that was lucky and checks the combined result of the line pass, whose pieces were run one hunk at a time.
10. **Targets.** With `--find-targets`, the test command is run with `--junitxml` (xunit1 family) once with only the pinned hunks and once with the whole patch, limited to the Python files of the pinned hunks. The targets are the tests that pass after the fix and failed (or did not exist) before it: the FAIL_TO_PASS set of SWE-bench ("SWE-bench: Can Language Models Resolve Real-World GitHub Issues?", ICLR 2024, https://arxiv.org/abs/2310.06770; field definitions on https://huggingface.co/datasets/princeton-nlp/SWE-bench). Tests that passed before and fail after are reported as a warning.
11. **Kinds.** `docs` is a hunk in a documentation file, `test` a hunk in a test file, `whitespace` a hunk whose lines are equal once whitespace is removed, `comment` a hunk whose changed lines are `#` comments or sit inside a docstring (the docstring state at the top of the hunk is guessed from the first triple-quote fence), `rename` a hunk whose removed and added lines are the same token for token except for identifiers that map one to one, and `code` anything else.

There are no thresholds on patch size or on the number of hunks, and the tool never grades a fix. A "not needed" label says only that the target tests (and the rest command) pass without that hunk; it does not say the hunk is useless.

### Cost of the search

`examples/ddmin_cost.py` counts the trials ddmin makes when k of n hunks are needed (oracle: "all k are present"), over 200 random placements per point with seed 1. Output of `uv run --with matplotlib python examples/ddmin_cost.py --plot docs/ddmin_cost.png`, mean (max) trials:

| hunks n | k=1 | k=2 | k=4 | n²+3n |
|---|---|---|---|---|
| 4 | 3.0 (4) | 11.4 (16) | 10.0 (10) | 28 |
| 8 | 4.5 (6) | 21.6 (30) | 40.9 (58) | 88 |
| 16 | 6.0 (8) | 33.2 (44) | 76.3 (104) | 304 |
| 32 | 7.6 (10) | 44.5 (58) | 115.5 (149) | 1120 |
| 64 | 8.8 (12) | 56.4 (72) | 152.2 (197) | 4288 |

![ddmin trials against the number of hunks, for 1, 2 and 4 needed hunks](docs/ddmin_cost.png)

These are trials, not test runs: ddmin asks about some subsets more than once, and the cache answers the repeats.

## Results on real public data

Three bug-fix commits from two small pure-Python libraries, chosen because the fix commit carries more than the fix (a README or changelog edit, a removed block, several code hunks). For each, the targets are the tests that fail before the fix and pass after; they were first found by hand (applying the commit's test files to the parent and running pytest) and `--find-targets` returns the same lists for all three. The test files are pinned; the whole test suite is the rest command.

| Library | Commit | Fix |
|---|---|---|
| tabulate | `8014ec6` (2026-03-05) | Fix alignments in `github` tables |
| tabulate | `3aa568c` (2026-03-05) | fix #408 - remove trailing whitespace from asciidoc output |
| more-itertools | `ce676d2` (2026-09-04) | Do not yield an empty list for an empty iterable when maxsplit is 0 |

Command (Python 3.12.13, pytest 9.2.0 in the interpreter given as `--python`, one laptop, 2026-11-06):

```
uv run python examples/real_commits.py --data DATA_DIR --python PYTHON_WITH_PYTEST --jobs 4
```

which runs, for each case, `patchmin --base C^ --fix C --pin 'test/*' --test-cmd 'PY -m pytest -q -p no:cacheprovider' --target ... --rest-cmd 'PY -m pytest -q -p no:cacheprovider test' --lines --jobs 4 --reruns 1 --confirm 2 --json` (with `tests/*` and `tests` for more-itertools) and saves the JSON in `results/`. `uv run python examples/real_commits.py --summary` prints the table from those files:

| commit | hunks needed | lines needed | ddmin trials | test runs | cache hits | confirm | wall s |
|---|---|---|---|---|---|---|---|
| tabulate 8014ec6 | 1 of 3 | 1 of 4 | 8 | 22 | 0 | pass, pass | 13.7 |
| tabulate 3aa568c | 1 of 3 | 5 of 5 | 24 | 42 | 6 | pass, pass | 11.6 |
| more-itertools ce676d2 | 3 of 4 | 12 of 12 | 46 | 90 | 4 | pass, pass | 263.0 |

The files in `results/` and this table come from a rerun with the current code (1-minute load average 30.6 at the start); it gave the same verdicts and counts as the first run, whose wall times were 11.8, 10.2 and 180.5 s. "Hunks needed" counts the hunks searched (pinned test hunks excluded). "Test runs" includes the two pre-checks, the reruns (`--reruns 1` doubles every fresh run) and the two confirmation runs. No subset was flaky, timed out or failed to apply.

What the minimal subsets show:

- **tabulate 8014ec6.** The fix deletes the old `"github"` entry from the format table (10 lines) and adds, after the table, `_table_formats["github"] = _table_formats["pipe"]`. Only the added assignment is needed: it overrides the old entry, so the deletion is not needed for the tests, and the README hunk is not needed. The line pass keeps 1 of the 4 added lines; the two comment lines and the blank line go.
- **tabulate 3aa568c.** Of the two hunks in `_asciidoc_row`, only the second is needed: it strips the line built for header and data rows, which is where the trailing whitespace came from. The first adds `.rstrip()` to the `[cols=...]` / `|====` line and to the above-line branch; that text ends in `|====`, so stripping it changes nothing the tests see, and the targets and the full suite pass without it. Every line of the needed hunk is needed.
- **more-itertools ce676d2.** All three code hunks (`split_before`, `split_after`, `split_when`) are needed, each by its own target test, and every line inside them; only the changelog line in `docs/versions.rst` is not needed.

Parallel worktrees. The same command with `--jobs 1`, `4` and `8` (and `--out OTHER_DIR`), six runs in all on the same laptop on 2026-11-06, while other work was running (the 1-minute load average at the start of each run is given; the laptop has 8 cores). Wall seconds:

| run | jobs | load | tabulate 8014ec6 | tabulate 3aa568c | more-itertools ce676d2 |
|---|---|---|---|---|---|
| first | 1 | not recorded | 12.8 | 14.7 | 199.2 |
| first | 4 | not recorded | 11.8 | 10.2 | 180.5 |
| repeat | 4 | 5.1 | 17.1 | 15.6 | 275.8 |
| repeat | 8 | 3.7 | 12.1 | 10.3 | 179.5 |
| repeat | 1 | 2.6 | 13.2 | 13.9 | 221.2 |
| rerun (table above) | 4 | 30.6 | 13.7 | 11.6 | 263.0 |

The repeats ran after the batching of chunks with complements was added and gave the same verdicts, trial and run counts as the first runs. With one job, more-itertools took 43 trials and 84 runs; with 4 or 8 jobs, 46 trials and 90 runs, because a parallel round runs every chunk of the round, including those after the first passing one. On this machine the noise between repeats (180.5 s and 275.8 s for the same command) is larger than the gain from parallel worktrees, so these runs do not show a clear speed-up. Two reasons in the search itself: with 4 hunks a round has at most 4 chunks, so at most 4 worktrees are busy, and every passing subset also runs the whole suite as the rest command (about 11 s for more-itertools) inside its own worktree.

Data: the repositories were cloned from the source links on https://pypi.org/project/tabulate/ and https://pypi.org/project/more-itertools/ (the script resolves the clone URL from PyPI). Both are MIT-licensed; the licence was checked on the LICENSE file of each source repository on 2026-11-06. Nothing from them is stored in this repository except the short hunk headings and changed lines inside `results/*.json`.

## Limitations

- 1-minimal is not globally minimal: a different, smaller subset may exist that ddmin does not find. The line pass is 1-minimal within each hunk, given the other needed hunks; a line needed only together with a line in another hunk is kept.
- A subset that does not apply counts as unresolved, which can keep a hunk the fix does not strictly need when a later hunk's context overlaps it. Hunks are not split or re-anchored after parsing; `--context 0` gives smaller hunks from the start.
- The isolation algorithm of the same paper (dd, which narrows the difference between a passing and a failing configuration) is not implemented; only ddmin is.
- `--find-targets` works with pytest only, and maps junit entries to node ids by file and class name; parametrised ids are kept as pytest writes them. Other test runners need `--target`.
- Test commands are split with `shlex` and run without a shell: pipes, redirection and environment assignments in the command are not supported (wrap them in a script).
- The environment is whatever the given interpreter has: the tool does not install the project or its dependencies, and does not isolate runs from each other beyond separate worktrees.
- Between trials in one worktree, tracked files are restored and untracked files deleted, but ignored files persist (build output, tool caches), except Python bytecode: ignored `__pycache__` directories and `.pyc` files are deleted, and test commands run with `PYTHONDONTWRITEBYTECODE=1`, because CPython would otherwise trust a `.pyc` left by a previous trial after a same-size edit made within the same second. `--clean-ignored` deletes every ignored file instead.
- Kinds are heuristics read from the diff alone; a hunk wholly inside a long docstring with no fence in view is labelled `code`.
- With `--jobs` above 1, test runs share the machine: tests that write to fixed paths outside the worktree, bind fixed ports or rely on timing can interfere with each other.
- Wall times in this README were measured on a laptop that was running other work at the same time; repeats of the same command differ by tens of percent (see the results section).
- Binary patches, renames and mode changes are not handled. Process-group killing on timeout assumes a POSIX system.
- The cache key covers the base commit id, the commands, the targets and the patch text, not the environment: untracked files in the repository, the interpreter and the installed packages are not part of it. Delete the `--cache` file after changing any of them. The repository path is not part of the key either; the same commit id names the same tracked content in any clone.
- In the line pass, a hunk that deletes a whole file is all or nothing; its lines are reported as needed without a search.
- Flaky detection only sees disagreement within the reruns requested; with `--reruns 0` a flaky test can mislead the search, and only the confirmation runs may reveal it.

## Related tools

- [commitminer](https://github.com/vipul21435/commitminer): mine and rank candidate tasks from repository history.
- [reporewind](https://github.com/vipul21435/reporewind): resolve Python fix commits into source and test patches.
- [taskledger](https://github.com/vipul21435/taskledger): schema validation and lint rules for benchmark-task bundles.
- [graderguard](https://github.com/vipul21435/graderguard): audit graders for reward hacking and flakiness.

## Licence

MIT, see `LICENSE`.

Built by Vipul Raj Jha · https://vipul21435.github.io
