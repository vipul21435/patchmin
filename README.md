# patchmin

Find the part of a fix that actually fixes it: delta debugging over the hunks of a patch, against a test command, down to a 1-minimal subset that still turns the target tests green.

Status: first version. The hunk-level minimiser, line-level refinement inside needed hunks, the content-hashed run cache, the git worktree oracle, reruns with flaky detection, timeouts and the `patchmin` command work on a fixture. Parallel worktrees and results on real repositories are not implemented yet.

## Why this is hard

A bug-fix commit rarely contains only the fix. It carries docstring edits, renamed variables, reformatting, a changelog line and sometimes a second unrelated change. Reading the diff does not tell you which hunks the failing test depends on, and trying subsets by hand is slow: a patch with n hunks has 2^n subsets, and every trial is a checkout, an apply and a test run. Test runs are also noisy: a subset may fail to apply because it depends on a hunk that was dropped, a test may be flaky, and a run may hang. The tool has to search the subsets efficiently, treat "did not apply" and "timed out" as a third outcome rather than a failure, and confirm what it reports.

## Install

```
pip install git+https://github.com/vipul21435/patchmin
```

For development:

```
uv venv --python 3.12 .venv
uv pip install -e ".[dev]"
uv run pytest -q
```

## Quickstart

`examples/run_fixture.py` builds a two-commit git repository in a temporary folder: a small `calc.py` with a wrong `add`, then a fix commit of four hunks (a module docstring edit, the real fix together with a comment edit, a function docstring edit, a refactor of `neg`). It then runs the command line tool on it. Output of `uv run python examples/run_fixture.py` on 2026-11-06:

```
$ patchmin --repo <repo> --base <base> --fix <fix> --lines \
      --test-cmd 'python -c ...'
not needed  hunk 0   calc.py:1                        +1/-1
needed      hunk 1   calc.py:11                       +2/-2  PAD = [
not needed  hunk 2   calc.py:25                       +1/-1  PAD2 = [
not needed  hunk 3   calc.py:39                       +1/-1  PAD3 = [
not needed  hunk 1   line 3   -    # adds two numbers
needed      hunk 1   line 4   -    return a - b
not needed  hunk 1   line 5   +    # adds two numbers, now correctly
needed      hunk 1   line 6   +    return a + b
2 of 4 change lines needed
1 of 4 hunks needed; 13 test runs (8 cache hits, 0 flaky) in 0.6s
```

The general form:

```
patchmin --repo PATH --base COMMIT --fix COMMIT \
    --test-cmd "python -m pytest -q" --target tests/test_x.py::test_y \
    [--rest-cmd "python -m pytest -q"] [--lines] [--reruns 1] [--timeout 600] \
    [--cache runs.json] [--json] [--out minimal.diff]
```

`--patch FILE` takes a unified diff instead of `--fix`. `--target` values are appended to the test command; `--rest-cmd` is a second command that must also pass for a subset to count, so the rest of the suite stays green. `--lines` runs a second pass over the change lines of each needed hunk. `--out` writes the minimal patch as a unified diff that `git apply` accepts on top of `--base`. `--json` prints the verdict per hunk, the number of runs, cache hits, flaky runs and the wall time.

## How it works

1. The patch (`git diff BASE FIX`, or the file given) is parsed into hunks, numbered across files. Each hunk is the unit of the search.
2. A subset of hunks is rendered back into a unified diff. New-side line numbers are recomputed from the cumulative line delta of the hunks kept before it, so the subset applies cleanly.
3. The oracle applies the rendered diff in a detached git worktree at `BASE`, runs the test command (with the targets appended) and, if that passes, the rest command. Return code 0 is a pass; any other code is a fail; a patch that does not apply or a command that hits the timeout is unresolved. With `--reruns k`, a non-cached outcome is run k more times, and a disagreement marks the subset flaky and unresolved. Every outcome is cached under the SHA-256 of the base commit, the commands and the rendered diff, so a repeated subset never runs twice.
4. Before searching, the full patch must pass and the empty patch must fail; otherwise the tool reports that and stops.
5. The search is ddmin, the minimising delta debugging algorithm of Zeller and Hildebrandt, "Simplifying and isolating failure-inducing input", IEEE Transactions on Software Engineering 28(2), 183-200, 2002, https://doi.org/10.1109/32.988498. Here the "interesting" property is "the target tests pass", so the search keeps the hunks a passing run needs. Starting with granularity n = 2, the current set is split into n chunks; if some chunk alone is interesting, it becomes the current set and n resets to 2; otherwise, if the complement of some chunk is interesting, it becomes the current set and n decreases by one; otherwise n doubles, up to the size of the set. The loop ends when n cannot grow, and the result is 1-minimal: dropping any single hunk makes the target tests fail. The worst case is quadratic in the number of hunks; the fixture above took 5 runs for the 4 hunks, 2 of them the pre-checks, before the line pass.
6. With `--lines`, each needed hunk is refined by a second ddmin over its `+` and `-` lines, one hunk at a time with the other needed hunks kept whole. A dropped `+` line disappears and a dropped `-` line turns back into context, so the result is still a valid diff. In the fixture, hunk 1 mixes a comment edit with the fix; the second pass keeps only the two `return` lines, at a cost of 8 more runs (8 further subsets were answered from the cache).
7. Hunks and lines not in the result are labelled "not needed". A label says only that the target tests (and the rest command, if given) pass without them; it does not say they are useless.

There are no thresholds on patch size or on the number of hunks, and the tool never grades a fix.

## Results on real public data

Not yet. The next version will run the tool on real bug-fix commits from small, permissively licensed Python libraries, after verifying that the fix commit's tests fail before and pass after, and report the minimal subset, the number of runs and the time for each. The numbers above come from the fixture only.

## Limitations

- Without `--lines`, a hunk that mixes the fix with a comment edit (hunk 1 above) is reported as needed as a whole. With it, lines are refined one hunk at a time, so a line that is needed only together with a line in another hunk is still kept.
- One worktree, one run at a time. Parallel worktrees are not implemented.
- A test command is any shell-free command line (split with `shlex`); pipes and shell features are not supported.
- 1-minimal is not globally minimal: a different, smaller subset may exist that ddmin does not find.
- A subset that does not apply counts as unresolved, which can keep a hunk the fix does not strictly need when a later hunk's context overlaps it.
- Binary patches and renames are not handled.

## Related tools

- https://github.com/vipul21435/commitminer
- https://github.com/vipul21435/reporewind
- https://github.com/vipul21435/testlogs
- https://github.com/vipul21435/verifybench
- https://github.com/vipul21435/issuelint

## Licence

MIT

Built by Vipul Raj Jha · https://vipul21435.github.io
