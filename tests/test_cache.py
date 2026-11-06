import json
import subprocess

import pytest

from patchmin.cache import RunCache, content_key
from patchmin.oracle import Outcome, WorktreeOracle


def test_content_key_separates_parts():
    assert content_key("ab", "c") != content_key("a", "bc")
    assert content_key("x") == content_key("x")
    assert len(content_key("x")) == 64


def test_memory_cache_counts_hits_and_misses():
    cache = RunCache()
    assert cache.get("k") is None
    cache.put("k", {"outcome": "pass"})
    assert cache.get("k") == {"outcome": "pass"}
    assert (cache.hits, cache.misses) == (1, 1)


def test_file_cache_persists_and_leaves_no_temp_file(tmp_path):
    path = tmp_path / "sub" / "runs.json"
    RunCache(path).put("k", {"outcome": "fail", "seconds": 0.5})
    assert json.loads(path.read_text()) == {"k": {"outcome": "fail", "seconds": 0.5}}
    assert not (tmp_path / "sub" / "runs.json.tmp").exists()
    again = RunCache(path)
    assert again.get("k")["outcome"] == "fail"


def test_oracle_key_depends_on_every_input(fixture_repo):
    repo, b = fixture_repo["repo"], fixture_repo["base"]

    def key(**kw):
        args = dict(repo=repo, base=b, test_cmd="t", targets=["x"], rest_cmd=None)
        args.update(kw)
        return WorktreeOracle(**args)._key("patch")

    base = key()
    assert key() == base
    assert key(base=fixture_repo["fix"]) != base
    assert key(test_cmd="t2") != base
    assert key(targets=["y"]) != base
    assert key(rest_cmd="r") != base
    assert WorktreeOracle(repo, b, "t", targets=["x"])._key("patch2") != base


def test_key_uses_the_commit_a_name_resolves_to(fixture_repo):
    repo = fixture_repo["repo"]
    by_name = WorktreeOracle(repo, "main~1", "t")._key("p")
    by_id = WorktreeOracle(repo, fixture_repo["base"], "t")._key("p")
    assert by_name == by_id


def test_unknown_base_is_rejected(fixture_repo):
    with pytest.raises(ValueError, match="not a commit"):
        WorktreeOracle(fixture_repo["repo"], "no-such-ref", "t")


def test_cached_outcome_is_not_reused_after_the_base_name_moves(fixture_repo, tmp_path):
    repo = fixture_repo["repo"]
    path = tmp_path / "c.json"
    cmd = fixture_repo["test_cmd"]
    # main~1 is the buggy commit: the empty patch fails there.
    with WorktreeOracle(repo, "main~1", cmd, cache=RunCache(path)) as first:
        assert first.run("").outcome is Outcome.FAIL
    # One more commit moves main~1 to the fixed commit, where the empty patch passes.
    (repo / "notes.txt").write_text("later\n")
    subprocess.run(["git", "-C", str(repo), "add", "notes.txt"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "notes"], check=True)
    with WorktreeOracle(repo, "main~1", cmd, cache=RunCache(path)) as second:
        result = second.run("")
    assert not result.cached and result.outcome is Outcome.PASS
    assert second.runs == 1


def test_outcome_cached_without_reruns_is_rerun_when_reruns_are_asked(fixture_repo, tmp_path):
    path = tmp_path / "c.json"
    args = (fixture_repo["repo"], fixture_repo["base"], fixture_repo["test_cmd"])
    with WorktreeOracle(*args, cache=RunCache(path)) as plain:
        plain.run("")
    with WorktreeOracle(*args, reruns=1, cache=RunCache(path)) as checked:
        assert not checked.run("").cached
        assert checked.runs == 2
    # Checked with one rerun: good enough for a later run that asks for none or one.
    for reruns in (0, 1):
        with WorktreeOracle(*args, reruns=reruns, cache=RunCache(path)) as later:
            assert later.run("").cached


def test_cached_outcome_is_reused_by_a_new_oracle(fixture_repo, tmp_path):
    path = tmp_path / "c.json"
    args = (fixture_repo["repo"], fixture_repo["base"], fixture_repo["test_cmd"])
    with WorktreeOracle(*args, cache=RunCache(path)) as first:
        r1 = first.run("")
    assert not r1.cached and first.runs == 1
    second = WorktreeOracle(*args, cache=RunCache(path))
    r2 = second.run("")
    assert r2.cached and r2.outcome is r1.outcome
    assert second.runs == 0
