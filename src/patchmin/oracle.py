"""Run the test command against a subset of the patch in a git worktree."""

from __future__ import annotations

import json
import os
import shlex
import shutil
import signal
import subprocess
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from .cache import RunCache, content_key
from .diff import encode_patch


class Outcome(str, Enum):
    """The outcome of one trial."""

    PASS = "pass"
    FAIL = "fail"
    UNRESOLVED = "unresolved"


@dataclass
class RunResult:
    """One trial: its outcome, time, return code and the tail of its output."""

    outcome: Outcome
    seconds: float
    returncode: int | None
    cached: bool = False
    flaky: bool = False
    stdout_tail: str = ""
    reason: str = ""
    """Why a run is unresolved: ``apply``, ``timeout``, ``flaky`` or ``command not found``;
    empty otherwise."""


def run_env() -> dict[str, str]:
    """``os.environ`` with ``PYTHONDONTWRITEBYTECODE=1``."""
    return {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}


def run_command(
    cmd: list[str], cwd: Path, timeout: float, env: dict[str, str] | None = None
) -> tuple[int | None, str]:
    """Run ``cmd`` in its own process group and return ``(returncode, output)``.

    The environment is ``os.environ`` with ``PYTHONDONTWRITEBYTECODE=1`` unless
    ``env`` is given, so Python test runs leave no bytecode behind for the
    next trial. Output that is not UTF-8 is decoded with replacement
    characters; only its tail is kept for the report.

    On timeout the whole group is killed, so test runners that start worker
    processes do not leave them behind, and the return code is None. An
    ``OSError`` (the program does not exist or is not executable) is raised
    to the caller.
    """
    proc = subprocess.Popen(
        cmd,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=run_env() if env is None else env,
        text=True,
        encoding="utf-8",
        errors="replace",
        start_new_session=True,
    )
    try:
        out, _ = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        out, _ = proc.communicate()
        return None, out or ""
    return proc.returncode, out or ""


NOT_FOUND = "command not found"


def _not_found(exc: OSError, seconds: float) -> RunResult:
    return RunResult(Outcome.UNRESOLVED, seconds, None, stdout_tail=str(exc), reason=NOT_FOUND)


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=check
    )


def resolve_commit(repo: Path, rev: str) -> str:
    """Return the full commit id ``rev`` names in ``repo``; ValueError if none."""
    proc = _git(repo, "rev-parse", "--verify", "--quiet", f"{rev}^{{commit}}", check=False)
    if proc.returncode != 0 or not proc.stdout.strip():
        raise ValueError(f"{rev!r} is not a commit in {repo}")
    return proc.stdout.strip()


class WorktreeOracle:
    """Applies a patch text on top of ``base`` in a worktree and runs a command.

    ``test_cmd`` is split with shlex; ``targets`` are appended to it. A run
    passes when the return code is 0, fails on any other return code, and is
    unresolved when the patch does not apply or the command times out.
    ``reruns`` extra runs confirm a non-cached outcome; disagreement marks the
    result flaky and resolves to UNRESOLVED. ``rest_cmd``, when given, runs
    after a passing target run; a subset passes only when both commands do,
    so the rest of the suite stays green. ``jobs`` worktrees are created and
    ``run_many`` runs that many subsets at once. ``unidiff_zero`` lets
        ``git apply`` take hunks without context lines (diffs made with ``-U0``).
    ``clean_ignored`` deletes ignored files too between trials (build output,
    caches); by default only ignored Python bytecode is deleted.

    ``base`` is resolved to a commit id once, here, and the cache key uses
    that id, so a persistent cache never answers for a different commit that
    the same name (``HEAD~1``, a branch) points at later. A ``ValueError`` is
    raised when ``base`` does not name a commit in ``repo``.
    """

    def __init__(
        self,
        repo: Path,
        base: str,
        test_cmd: str,
        targets: list[str] | None = None,
        timeout: float = 600.0,
        reruns: int = 0,
        cache: RunCache | None = None,
        work_dir: Path | None = None,
        rest_cmd: str | None = None,
        jobs: int = 1,
        unidiff_zero: bool = False,
        clean_ignored: bool = False,
    ) -> None:
        self.repo = Path(repo)
        self.base = base
        self.base_commit = resolve_commit(self.repo, base)
        self.test_cmd = test_cmd
        self.targets = list(targets or [])
        self.timeout = timeout
        self.reruns = reruns
        self.cache = cache if cache is not None else RunCache()
        self.work_dir = work_dir
        self.rest_cmd = rest_cmd
        self.jobs = max(1, int(jobs))
        self.unidiff_zero = unidiff_zero
        self.clean_ignored = clean_ignored
        self.runs = 0
        self._worktrees: list[Path] = []
        self._temp_root: Path | None = None
        self._lock = threading.Lock()

    @property
    def _worktree(self) -> Path | None:
        return self._worktrees[0] if self._worktrees else None

    @property
    def worktree(self) -> Path | None:
        """The first worktree, where :meth:`execute` runs; None before :meth:`open`."""
        return self._worktree

    def __enter__(self) -> WorktreeOracle:
        self.open()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def open(self) -> None:
        """Create the ``jobs`` worktrees at the base commit (once)."""
        if self._worktrees:
            return
        if self.work_dir is not None:
            root = self.work_dir
            root.mkdir(parents=True, exist_ok=True)
        else:
            root = self._temp_root = Path(tempfile.mkdtemp(prefix="patchmin-"))
        for i in range(self.jobs):
            wt = root / f"wt{i}"
            _git(self.repo, "worktree", "add", "--detach", str(wt), self.base_commit)
            self._worktrees.append(wt)

    def close(self) -> None:
        """Remove the worktrees, and the temporary folder if this oracle made it."""
        for wt in self._worktrees:
            _git(self.repo, "worktree", "remove", "--force", str(wt), check=False)
        self._worktrees = []
        if self._temp_root is not None:
            # Only a directory this oracle made itself; a given work_dir stays.
            shutil.rmtree(self._temp_root, ignore_errors=True)
            self._temp_root = None

    def _reset(self, worktree: Path) -> None:
        """Bring ``worktree`` back to the base commit before a trial.

        ``git clean`` keeps ignored files, so ignored ``__pycache__``
        directories and ``.pyc`` files are deleted as well: CPython trusts a
        cached ``.pyc`` whose source has the same size and mtime second,
        which a same-size edit restored by the previous trial can have.
        With ``clean_ignored`` every ignored file goes (``git clean -X``).
        """
        _git(worktree, "checkout", "--", ".")
        _git(worktree, "clean", "-fdqx" if self.clean_ignored else "-fdq")
        if self.clean_ignored:
            return
        listed = _git(
            worktree, "ls-files", "-z", "--others", "--ignored", "--exclude-standard",
            "--directory",
        ).stdout
        for entry in listed.split("\0"):
            name = entry.rstrip("/")
            if name.rsplit("/", 1)[-1] == "__pycache__":
                shutil.rmtree(worktree / name, ignore_errors=True)
            elif name.endswith((".pyc", ".pyo")):
                (worktree / name).unlink(missing_ok=True)

    def _apply(self, worktree: Path, patch_text: str) -> bool:
        if not patch_text.strip():
            return True
        flags = ["--unidiff-zero"] if self.unidiff_zero else []
        proc = subprocess.run(
            ["git", "-C", str(worktree), "apply", "--whitespace=nowarn", *flags, "-"],
            input=encode_patch(patch_text),
            capture_output=True,
        )
        return proc.returncode == 0

    def _run_once(self, patch_text: str, worktree: Path | None = None) -> RunResult:
        wt = worktree if worktree is not None else self._worktree
        assert wt is not None
        with self._lock:
            self.runs += 1
        self._reset(wt)
        t0 = time.monotonic()
        if not self._apply(wt, patch_text):
            return RunResult(Outcome.UNRESOLVED, time.monotonic() - t0, None, reason="apply")
        commands = [shlex.split(self.test_cmd) + self.targets]
        if self.rest_cmd:
            commands.append(shlex.split(self.rest_cmd))
        tail = ""
        returncode: int | None = 0
        for cmd in commands:
            try:
                returncode, out = run_command(cmd, wt, self.timeout)
            except OSError as exc:
                return _not_found(exc, time.monotonic() - t0)
            tail = out[-2000:]
            if returncode is None:
                return RunResult(
                    Outcome.UNRESOLVED, time.monotonic() - t0, None, stdout_tail=tail,
                    reason="timeout",
                )
            if returncode != 0:
                break
        outcome = Outcome.PASS if returncode == 0 else Outcome.FAIL
        return RunResult(outcome, time.monotonic() - t0, returncode, stdout_tail=tail)

    def _key(self, patch_text: str) -> str:
        return content_key(
            self.base_commit, self.test_cmd, json.dumps(self.targets), self.rest_cmd or "",
            patch_text,
        )

    def _usable(self, hit: dict | None) -> bool:
        if hit is None:
            return False
        if hit.get("reason") == "timeout" and hit.get("timeout", 0.0) < self.timeout:
            # Timed out under a shorter limit than the current one: run again.
            return False
        if hit["outcome"] != Outcome.UNRESOLVED.value and hit.get("reruns", 0) < self.reruns:
            # A pass or fail confirmed by fewer runs than asked for now: run again.
            return False
        return True

    def _cached(self, key: str) -> RunResult | None:
        """The cached result for ``key`` if it can stand for a fresh run.

        Only an entry that is used counts as a cache hit; an entry ignored
        for a shorter timeout or fewer reruns counts as a miss.
        """
        with self._lock:
            hit = self.cache.peek(key)
            usable = self._usable(hit)
            self.cache.count(usable)
        if not usable or hit is None:
            return None
        return RunResult(
            Outcome(hit["outcome"]), hit["seconds"], hit.get("returncode"), cached=True,
            flaky=hit.get("flaky", False), reason=hit.get("reason", ""),
        )

    def run(self, patch_text: str) -> RunResult:
        """Run one subset: the cache first, then the first worktree."""
        key = self._key(patch_text)
        hit = self._cached(key)
        if hit is not None:
            return hit
        if not self._worktrees:
            self.open()
        return self._run_fresh(key, patch_text, self._worktrees[0])

    def confirm(self, patch_text: str, times: int) -> list[RunResult]:
        """Run ``patch_text`` ``times`` times without the cache.

        Used on the final answer: a cached pass may come from an earlier run
        that was lucky, and the line pass may combine refined hunks into a
        patch that was not run as a whole.
        """
        if not self._worktrees:
            self.open()
        return [self._run_once(patch_text, self._worktrees[0]) for _ in range(times)]

    def execute(self, patch_text: str, extra_args: list[str]) -> RunResult:
        """Apply ``patch_text`` and run the test command with ``extra_args``
        in place of the targets; no rest command, no cache, no reruns."""
        if not self._worktrees:
            self.open()
        wt = self._worktrees[0]
        with self._lock:
            self.runs += 1
        self._reset(wt)
        t0 = time.monotonic()
        if not self._apply(wt, patch_text):
            return RunResult(Outcome.UNRESOLVED, time.monotonic() - t0, None, reason="apply")
        try:
            code, out = run_command(shlex.split(self.test_cmd) + extra_args, wt, self.timeout)
        except OSError as exc:
            return _not_found(exc, time.monotonic() - t0)
        seconds = time.monotonic() - t0
        if code is None:
            return RunResult(Outcome.UNRESOLVED, seconds, None, stdout_tail=out[-2000:],
                             reason="timeout")
        outcome = Outcome.PASS if code == 0 else Outcome.FAIL
        return RunResult(outcome, seconds, code, stdout_tail=out[-2000:])

    def run_many(self, patch_texts: list[str]) -> list[RunResult]:
        """Run several subsets, up to ``jobs`` at a time, one worktree each.

        Equal texts in the batch run once. Cached outcomes are answered
        without a run. Results come back in the order of the input.
        """
        if not self._worktrees:
            self.open()
        keys = [self._key(t) for t in patch_texts]
        results: dict[str, RunResult] = {}
        todo: list[tuple[str, str]] = []
        for key, text in zip(keys, patch_texts, strict=True):
            if key in results or any(k == key for k, _ in todo):
                continue
            hit = self._cached(key)
            if hit is not None:
                results[key] = hit
            else:
                todo.append((key, text))
        if todo:
            free: list[Path] = list(self._worktrees)

            def work(item: tuple[str, str]) -> tuple[str, RunResult]:
                with self._lock:
                    wt = free.pop()
                try:
                    return item[0], self._run_fresh(item[0], item[1], wt)
                finally:
                    with self._lock:
                        free.append(wt)

            with ThreadPoolExecutor(max_workers=len(self._worktrees)) as pool:
                for key, result in pool.map(work, todo):
                    results[key] = result
        return [results[k] for k in keys]

    def _run_fresh(self, key: str, patch_text: str, worktree: Path) -> RunResult:
        first = self._run_once(patch_text, worktree)
        result = first
        if first.outcome is not Outcome.UNRESOLVED:
            for _ in range(self.reruns):
                again = self._run_once(patch_text, worktree)
                if again.outcome is not first.outcome:
                    result = RunResult(
                        Outcome.UNRESOLVED, first.seconds + again.seconds, None, flaky=True,
                        stdout_tail=again.stdout_tail, reason="flaky",
                    )
                    break
        if result.reason == NOT_FOUND:
            # A missing program says nothing about the subset: never cache it.
            return result
        with self._lock:
            self.cache.put(
                key,
                {
                    "outcome": result.outcome.value,
                    "seconds": result.seconds,
                    "returncode": result.returncode,
                    "flaky": result.flaky,
                    "reason": result.reason,
                    "timeout": self.timeout,
                    "reruns": self.reruns,
                },
            )
        return result
