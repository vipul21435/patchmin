"""Run the test command against a subset of the patch in a git worktree."""

from __future__ import annotations

import shlex
import subprocess
import tempfile
import time
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


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=check
    )


class WorktreeOracle:
    """Applies a patch text on top of ``base`` in a worktree and runs a command.

    ``test_cmd`` is split with shlex; ``targets`` are appended to it. A run
    passes when the return code is 0, fails on any other return code, and is
    unresolved when the patch does not apply or the command times out.
    ``reruns`` extra runs confirm a non-cached outcome; disagreement marks the
    result flaky and resolves to UNRESOLVED. ``rest_cmd``, when given, runs
    after a passing target run; a subset passes only when both commands do,
    so the rest of the suite stays green.
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
    ) -> None:
        self.repo = Path(repo)
        self.base = base
        self.test_cmd = test_cmd
        self.targets = list(targets or [])
        self.timeout = timeout
        self.reruns = reruns
        self.cache = cache if cache is not None else RunCache()
        self.work_dir = work_dir
        self.rest_cmd = rest_cmd
        self.runs = 0
        self._worktree: Path | None = None

    def __enter__(self) -> WorktreeOracle:
        self.open()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def open(self) -> None:
        if self._worktree is not None:
            return
        root = self.work_dir or Path(tempfile.mkdtemp(prefix="patchmin-"))
        root.mkdir(parents=True, exist_ok=True)
        self._worktree = root / "wt"
        _git(self.repo, "worktree", "add", "--detach", str(self._worktree), self.base)

    def close(self) -> None:
        if self._worktree is None:
            return
        _git(self.repo, "worktree", "remove", "--force", str(self._worktree), check=False)
        self._worktree = None

    def _reset(self) -> None:
        assert self._worktree is not None
        _git(self._worktree, "checkout", "--", ".")
        _git(self._worktree, "clean", "-fdq")

    def _apply(self, patch_text: str) -> bool:
        assert self._worktree is not None
        if not patch_text.strip():
            return True
        proc = subprocess.run(
            ["git", "-C", str(self._worktree), "apply", "--whitespace=nowarn", "-"],
            input=patch_text,
            capture_output=True,
            text=True,
        )
        return proc.returncode == 0

    def _run_once(self, patch_text: str) -> RunResult:
        assert self._worktree is not None
        self.runs += 1
        self._reset()
        t0 = time.monotonic()
        if not self._apply(patch_text):
            return RunResult(Outcome.UNRESOLVED, time.monotonic() - t0, None)
        commands = [shlex.split(self.test_cmd) + self.targets]
        if self.rest_cmd:
            commands.append(shlex.split(self.rest_cmd))
        tail = ""
        returncode = 0
        for cmd in commands:
            try:
                proc = subprocess.run(
                    cmd,
                    cwd=self._worktree,
                    capture_output=True,
                    text=True,
                    timeout=self.timeout,
                )
            except subprocess.TimeoutExpired:
                return RunResult(Outcome.UNRESOLVED, time.monotonic() - t0, None)
            tail = (proc.stdout + proc.stderr)[-2000:]
            returncode = proc.returncode
            if returncode != 0:
                break
        outcome = Outcome.PASS if returncode == 0 else Outcome.FAIL
        return RunResult(outcome, time.monotonic() - t0, returncode, stdout_tail=tail)

    def run(self, patch_text: str) -> RunResult:
        key = content_key(
            self.base, self.test_cmd, " ".join(self.targets), self.rest_cmd or "", patch_text
        )
        hit = self.cache.get(key)
        if hit is not None:
            return RunResult(
                Outcome(hit["outcome"]), hit["seconds"], hit.get("returncode"), cached=True,
                flaky=hit.get("flaky", False),
            )
        if self._worktree is None:
            self.open()
        first = self._run_once(patch_text)
        result = first
        if first.outcome is not Outcome.UNRESOLVED:
            for _ in range(self.reruns):
                again = self._run_once(patch_text)
                if again.outcome is not first.outcome:
                    result = RunResult(
                        Outcome.UNRESOLVED, first.seconds + again.seconds, None, flaky=True,
                        stdout_tail=again.stdout_tail,
                    )
                    break
        self.cache.put(
            key,
            {
                "outcome": result.outcome.value,
                "seconds": result.seconds,
                "returncode": result.returncode,
                "flaky": result.flaky,
            },
        )
        return result
