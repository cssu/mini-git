"""Smoke tests: the package imports and the CLI runs.

These exist so the test suite is green from day one. Delete them once there
are real tests to run.
"""

import pytest

from minigit.cli import main
from minigit.errors import MergeConflictError, MiniGitError


def test_cli_reports_version(capsys):
    with pytest.raises(SystemExit) as exit_info:
        main(["--version"])

    assert exit_info.value.code == 0
    assert "minigit" in capsys.readouterr().out


def test_cli_without_a_command_prints_help(capsys):
    assert main([]) == 1
    assert "usage: minigit" in capsys.readouterr().out


def test_init_creates_repository_layout(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)

    assert main(["init"]) == 0

    assert (tmp_path / ".minigit").is_dir()
    assert (tmp_path / ".minigit" / "objects").is_dir()
    assert (tmp_path / ".minigit" / "refs" / "heads").is_dir()
    assert (tmp_path / ".minigit" / "index").read_bytes() == b""
    assert (tmp_path / ".minigit" / "config").read_bytes() == b""
    assert (tmp_path / ".minigit" / "HEAD").read_text() == "ref: refs/heads/main\n"

    sentinel = tmp_path / ".minigit" / "config"
    sentinel.write_bytes(b"keep me")
    assert main(["init"]) == 0
    assert sentinel.read_bytes() == b"keep me"
    assert "already a minigit repository" in capsys.readouterr().out


def test_merge_conflict_error_carries_paths():
    error = MergeConflictError(["src/a.py", "src/b.py"])

    assert error.paths == ["src/a.py", "src/b.py"]
    assert isinstance(error, MiniGitError)
