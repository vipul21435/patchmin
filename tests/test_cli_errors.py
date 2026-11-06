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
     ("--timeout", "0"), ("--jobs", "two")],
)
def test_out_of_range_numbers_are_rejected(fixture_repo, capsys, flag, value):
    with pytest.raises(SystemExit) as stop:
        main(_args(fixture_repo, **{flag: value}))
    assert stop.value.code == 2
    assert flag in capsys.readouterr().err


def test_missing_program_prints_no_hunk_counts(fixture_repo, capsys):
    code = main(_args(fixture_repo, **{"--test-cmd": "patchmin-no-such-program"}))
    assert code == 1
    out = capsys.readouterr().out
    assert "error: full patch does not make the target tests pass" in out
    assert "command not found" in out
    assert "hunks needed" not in out
    assert "test runs" in out


def test_unwritable_output_is_an_error_line(fixture_repo, capsys):
    code = main(_args(fixture_repo, **{"--out": "/nonexistent/dir/min.diff"}))
    assert code == 1
    assert capsys.readouterr().err.startswith("error: ")
