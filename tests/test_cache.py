import json

from patchmin.cache import RunCache, content_key
from patchmin.oracle import WorktreeOracle


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


def test_oracle_key_depends_on_every_input(tmp_path):
    def key(**kw):
        args = dict(repo=tmp_path, base="b", test_cmd="t", targets=["x"], rest_cmd=None)
        args.update(kw)
        return WorktreeOracle(**args)._key("patch")

    base = key()
    assert key() == base
    assert key(base="other") != base
    assert key(test_cmd="t2") != base
    assert key(targets=["y"]) != base
    assert key(rest_cmd="r") != base
    assert WorktreeOracle(tmp_path, "b", "t", targets=["x"])._key("patch2") != base


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
