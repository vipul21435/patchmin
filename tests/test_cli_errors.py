import pytest

from patchmin.cli import main


def _args(fx, **over):
    base = {
        "--repo": str(fx["repo"]),
        "--base": fx["base"],
        "--fix": fx["fix"],
        "--test-cmd": fx["test_cmd"],
    }
    base.update(over)
    out: list[str] = []
    for flag, value in base.items():
        if value is not None:
            out += [flag, value]
    return out


@pytest.mark.parametrize(
    ("over", "message"),
    [
        ({"--repo": "/nonexistent/patchmin-repo"}, "is not a directory"),
        ({"--base": "no-such-ref"}, "--base no-such-ref does not name a commit"),
        ({"--fix": "no-such-ref"}, "--fix no-such-ref does not name a commit"),
        ({"--fix": None, "--patch": "/nonexistent/fix.diff"}, "is not a file"),
    ],
)
def test_bad_inputs_give_one_error_line(fixture_repo, capsys, over, message):
    assert main(_args(fixture_repo, **over)) == 1
    err = capsys.readouterr().err
    assert err.startswith("error: ") and message in err
    assert "Traceback" not in err


def test_directory_that_is_not_a_repo(fixture_repo, tmp_path, capsys):
    plain = tmp_path / "plain"
    plain.mkdir()
    assert main(_args(fixture_repo, **{"--repo": str(plain)})) == 1
    assert "is not a git work tree" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("flag", "value"),
    [("--reruns", "-1"), ("--jobs", "0"), ("--confirm", "-2"), ("--context", "-1"),
     ("--timeout", "0"), ("--timeout", "soon"), ("--jobs", "two")],
)
def test_out_of_range_numbers_are_rejected(fixture_repo, capsys, flag, value):
    # Exit code 1 like any other error; 2 means a confirming run failed.
    with pytest.raises(SystemExit) as stop:
        main(_args(fixture_repo, **{flag: value}))
    assert stop.value.code == 1
    assert flag in capsys.readouterr().err


def test_missing_required_flag_exits_with_one(fixture_repo, capsys):
    with pytest.raises(SystemExit) as stop:
        main(_args(fixture_repo, **{"--test-cmd": None}))
    assert stop.value.code == 1
    assert "--test-cmd" in capsys.readouterr().err


def test_missing_program_prints_no_hunk_counts(fixture_repo, capsys):
    code = main(_args(fixture_repo, **{"--test-cmd": "patchmin-no-such-program"}))
    assert code == 1
    captured = capsys.readouterr()
    assert captured.err.startswith("error: full patch does not make the target tests pass")
    assert "command not found" in captured.err
    assert "hunks needed" not in captured.out
    assert "test runs" in captured.out


def test_unwritable_output_is_an_error_line(fixture_repo, capsys):
    code = main(_args(fixture_repo, **{"--out": "/nonexistent/dir/min.diff"}))
    assert code == 1
    assert capsys.readouterr().err.startswith("error: ")


def test_failing_git_command_is_an_error_line(fixture_repo, capsys, monkeypatch):
    import subprocess

    from patchmin import cli

    def broken(*args, **kwargs):
        raise subprocess.CalledProcessError(
            128, ["git", "diff"], stderr="fatal: bad object\n"
        )

    monkeypatch.setattr(cli, "_diff_between", broken)
    assert main(_args(fixture_repo, **{"--timeout": "30"})) == 1
    assert capsys.readouterr().err == "error: git diff failed: fatal: bad object\n"


@pytest.mark.parametrize(
    ("over", "message"),
    [
        ({"--test-cmd": 'pytest -q "unclosed'}, "--test-cmd cannot be split"),
        ({"--rest-cmd": "pytest 'unclosed"}, "--rest-cmd cannot be split"),
    ],
)
def test_unbalanced_quotes_give_one_error_line(fixture_repo, capsys, over, message):
    assert main(_args(fixture_repo, **over)) == 1
    err = capsys.readouterr().err
    assert err.startswith(f"error: {message}")
    assert err.count("\n") == 1


def test_cache_file_that_is_not_json(fixture_repo, tmp_path, capsys):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    assert main(_args(fixture_repo, **{"--cache": str(bad)})) == 1
    err = capsys.readouterr().err
    assert err == f"error: --cache {bad} is not a JSON cache\n"
