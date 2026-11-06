"""Content-hashed cache of test outcomes."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path


def content_key(*parts: str) -> str:
    """SHA-256 over ``parts``, each terminated by a NUL byte so that
    ``("ab", "c")`` and ``("a", "bc")`` get different keys."""
    h = hashlib.sha256()
    for p in parts:
        h.update(p.encode("utf-8"))
        h.update(b"\0")
    return h.hexdigest()


class RunCache:
    """Maps a content key to a stored outcome dictionary.

    ``path=None`` keeps everything in memory.
    """

    def __init__(self, path: Path | None = None) -> None:
        self.path = path
        self.data: dict[str, dict] = {}
        self.hits = 0
        self.misses = 0
        if path is not None and path.exists():
            self.data = json.loads(path.read_text())

    def get(self, key: str) -> dict | None:
        value = self.data.get(key)
        if value is None:
            self.misses += 1
        else:
            self.hits += 1
        return value

    def put(self, key: str, value: dict) -> None:
        self.data[key] = value
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            # Write a sibling file and rename it, so an interrupted run never
            # leaves a half-written cache behind.
            tmp = self.path.with_name(self.path.name + ".tmp")
            tmp.write_text(json.dumps(self.data, indent=1, sort_keys=True))
            os.replace(tmp, self.path)
