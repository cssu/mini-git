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


def test_merge_conflict_error_carries_paths():
    error = MergeConflictError(["src/a.py", "src/b.py"])

    assert error.paths == ["src/a.py", "src/b.py"]
    assert isinstance(error, MiniGitError)
