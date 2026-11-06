"""Run the test command against a subset of the patch in a git worktree."""

from __future__ import annotations

import os
import shlex
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


class Outcome(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    UNRESOLVED = "unresolved"


@dataclass
class RunResult:
    outcome: Outcome
    seconds: float
    returncode: int | None
    cached: bool = False
    flaky: bool = False
    stdout_tail: str = ""
    reason: str = ""
    """Why a run is unresolved: ``apply``, ``timeout`` or ``flaky``; empty otherwise."""


def run_command(cmd: list[str], cwd: Path, timeout: float) -> tuple[int | None, str]:
    """Run ``cmd`` in its own process group and return ``(returncode, output)``.

    On timeout the whole group is killed, so test runners that start worker
    processes do not leave them behind, and the return code is None.
    """
    proc = subprocess.Popen(
        cmd,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
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
        self.runs = 0
        self._worktrees: list[Path] = []
        self._lock = threading.Lock()

    @property
    def _worktree(self) -> Path | None:
        return self._worktrees[0] if self._worktrees else None

    def __enter__(self) -> WorktreeOracle:
        self.open()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def open(self) -> None:
        if self._worktrees:
            return
        root = self.work_dir or Path(tempfile.mkdtemp(prefix="patchmin-"))
        root.mkdir(parents=True, exist_ok=True)
        for i in range(self.jobs):
            wt = root / f"wt{i}"
            _git(self.repo, "worktree", "add", "--detach", str(wt), self.base_commit)
            self._worktrees.append(wt)

    def close(self) -> None:
        for wt in self._worktrees:
            _git(self.repo, "worktree", "remove", "--force", str(wt), check=False)
        self._worktrees = []

    @staticmethod
    def _reset(worktree: Path) -> None:
        _git(worktree, "checkout", "--", ".")
        _git(worktree, "clean", "-fdq")

    def _apply(self, worktree: Path, patch_text: str) -> bool:
        if not patch_text.strip():
            return True
        flags = ["--unidiff-zero"] if self.unidiff_zero else []
        proc = subprocess.run(
            ["git", "-C", str(worktree), "apply", "--whitespace=nowarn", *flags, "-"],
            input=patch_text,
            capture_output=True,
            text=True,
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
            returncode, out = run_command(cmd, wt, self.timeout)
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
            self.base_commit, self.test_cmd, " ".join(self.targets), self.rest_cmd or "",
            patch_text,
        )

    def _cached(self, key: str) -> RunResult | None:
        with self._lock:
            hit = self.cache.get(key)
        if hit is None:
            return None
        if hit.get("reason") == "timeout" and hit.get("timeout", 0.0) < self.timeout:
            # Timed out under a shorter limit than the current one: run again.
            return None
        if hit["outcome"] != Outcome.UNRESOLVED.value and hit.get("reruns", 0) < self.reruns:
            # A pass or fail confirmed by fewer runs than asked for now: run again.
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
        code, out = run_command(shlex.split(self.test_cmd) + extra_args, wt, self.timeout)
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
