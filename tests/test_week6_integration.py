"""Week 6 checkpoint: complete branch exchange across fresh CLI processes."""

import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from minigit.commits import CommitManager
from minigit.remote import RemoteServer


@pytest.fixture
def server(tmp_path):
    remote = tmp_path / "remote"
    remote.mkdir()
    server = RemoteServer(remote, token="test-token")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.close()
    thread.join(timeout=2)
    assert not thread.is_alive()


def _run(repo, *argv) -> int:
    """Run a fresh CLI process with bounded runtime and forward its output."""
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])}
    result = subprocess.run(
        [sys.executable, "-m", "minigit.cli", *argv],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )
    print(result.stdout, end="")
    print(result.stderr, end="", file=sys.stderr)
    return result.returncode


def _commit_file(monkeypatch, repo, path, text, message) -> str:
    (repo / path).write_text(text)
    assert _run(repo, "add", path) == 0
    assert _run(repo, "commit", "-m", message) == 0
    manager = CommitManager(repo)
    return manager.read_ref(manager.read_head())


def _repo_with_merge_commit(monkeypatch, root):
    """main and feature diverge from a base, then main records a two-parent merge."""

    root.mkdir()
    assert _run(root, "init") == 0
    base = _commit_file(monkeypatch, root, "a.txt", "line\n", "base")
    assert _run(root, "branch", "feature") == 0
    assert _run(root, "checkout", "feature") == 0
    feature = _commit_file(monkeypatch, root, "f.txt", "feature\n", "feature")
    assert _run(root, "checkout", "main") == 0
    main_tip = _commit_file(monkeypatch, root, "a.txt", "main\n", "main")

    assert _run(root, "merge", "feature") == 0
    manager = CommitManager(root)
    merge = manager.read_ref("main")
    assert manager.read_commit(merge).parents == [main_tip, feature]
    return base, feature, main_tip, merge


def test_two_repos_exchange_branches_and_merge_commit(tmp_path, monkeypatch, capsys, server):
    address = f"127.0.0.1:{server.port}"
    a, b = tmp_path / "a", tmp_path / "b"
    _, feature, main_tip, merge = _repo_with_merge_commit(monkeypatch, a)

    assert _run(a, "push", address, "--all", "--token", "test-token") == 0

    b.mkdir()
    assert _run(b, "init") == 0
    capsys.readouterr()
    assert _run(b, "ls-remote", address, "--token", "test-token") == 0
    assert capsys.readouterr().out == f"{feature}\tfeature\n{merge}\tmain\n"

    # feature isn't checked out in B, so it's imported by ref alone.
    assert _run(b, "pull", address, "feature", "--token", "test-token") == 0
    assert CommitManager(b).read_ref("feature") == feature
    capsys.readouterr()
    assert _run(b, "pull", address, "feature", "--token", "test-token") == 0
    assert "feature is up to date" in capsys.readouterr().out

    # The merge commit's full history (both parents) arrives intact.
    assert _run(b, "fetch", address, "main", "--token", "test-token") == 0
    receiver = CommitManager(b)
    assert receiver.read_commit(merge).parents == [main_tip, feature]
    assert set(receiver.walk_history(merge)) == set(CommitManager(a).walk_history(merge))

    # A and B both commit on feature; A pushes first, so B's push is rejected.
    assert _run(b, "checkout", "feature") == 0
    assert (b / "f.txt").read_text() == "feature\n"
    _commit_file(monkeypatch, b, "f.txt", "from b\n", "b")
    assert _run(a, "checkout", "feature") == 0
    a_feature = _commit_file(monkeypatch, a, "f.txt", "from a\n", "a")
    assert _run(a, "push", address, "feature", "--token", "test-token") == 0

    capsys.readouterr()
    assert _run(b, "push", address, "feature", "--token", "test-token") == 1
    assert "pull first" in capsys.readouterr().err
    assert CommitManager(server.repo_path).read_ref("feature") == a_feature


def test_pull_into_checked_out_branch_and_resolve_conflict(tmp_path, monkeypatch, server):
    address = f"127.0.0.1:{server.port}"
    a, b = tmp_path / "a", tmp_path / "b"
    _, _, _, merge = _repo_with_merge_commit(monkeypatch, a)
    assert _run(a, "push", address, "main", "--token", "test-token") == 0

    b.mkdir()
    assert _run(b, "init") == 0
    assert _run(b, "pull", address, "main", "--token", "test-token") == 0
    assert CommitManager(b).read_ref("main") == merge
    assert (b / "a.txt").read_text() == "main\n"

    # Same line changed on both sides -> pull reports the conflict.
    a_tip = _commit_file(monkeypatch, a, "a.txt", "from a\n", "a")
    assert _run(a, "push", address, "main", "--token", "test-token") == 0
    b_tip = _commit_file(monkeypatch, b, "a.txt", "from b\n", "b")
    assert _run(b, "pull", address, "main", "--token", "test-token") == 1
    assert "<<<<<<<" in (b / "a.txt").read_text()

    # Resolve -> add -> commit, across fresh CLI calls, yields a two-parent commit.
    resolved = _commit_file(monkeypatch, b, "a.txt", "resolved\n", "resolve")
    assert CommitManager(b).read_commit(resolved).parents == [b_tip, a_tip]
    assert _run(b, "push", address, "main", "--token", "test-token") == 0

    assert _run(a, "pull", address, "main", "--token", "test-token") == 0
    assert CommitManager(a).read_ref("main") == resolved
    assert (a / "a.txt").read_text() == "resolved\n"
    assert _run(a, "status") == 0
    assert _run(b, "status") == 0
