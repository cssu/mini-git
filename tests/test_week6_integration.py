"""Week 6 checkpoint: two independent repos exchanging branches through the CLI.

The parts that pull into a checked-out branch need Module 3's fetched-tip
integration (#40); they skip until `CommitManager.integrate_fetched` exists
and then run automatically.
"""

import threading

import pytest

from minigit.cli import main
from minigit.commits import CommitManager
from minigit.remote import RemoteServer

needs_m3_integration = pytest.mark.skipif(
    not hasattr(CommitManager, "integrate_fetched"),
    reason="pulling into the checked-out branch needs M3's integrate_fetched (#40)",
)


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


def _run(monkeypatch, repo, *argv) -> int:
    """Run one fresh `minigit` CLI call inside `repo`."""

    monkeypatch.chdir(repo)
    return main(list(argv))


def _commit_file(monkeypatch, repo, path, text, message) -> str:
    (repo / path).write_text(text)
    assert _run(monkeypatch, repo, "add", path) == 0
    assert _run(monkeypatch, repo, "commit", "-m", message) == 0
    manager = CommitManager(repo)
    return manager.read_ref(manager.read_head())


def _repo_with_merge_commit(monkeypatch, root):
    """main and feature diverge from a base, then main records a two-parent merge."""

    root.mkdir()
    assert _run(monkeypatch, root, "init") == 0
    base = _commit_file(monkeypatch, root, "a.txt", "line\n", "base")
    assert _run(monkeypatch, root, "branch", "feature") == 0
    assert _run(monkeypatch, root, "checkout", "feature") == 0
    feature = _commit_file(monkeypatch, root, "f.txt", "feature\n", "feature")
    assert _run(monkeypatch, root, "checkout", "main") == 0
    main_tip = _commit_file(monkeypatch, root, "a.txt", "main\n", "main")

    # Three-way merge isn't in M3 yet (#33), so record the merge commit directly.
    (root / "f.txt").write_text("feature\n")
    assert _run(monkeypatch, root, "add", "f.txt") == 0
    manager = CommitManager(root)
    tree = manager.tree.build_tree_from_index()
    merge = manager.create_commit(tree, [main_tip, feature], "tester <t@example.com>", "merge")
    return base, feature, main_tip, merge


def test_two_repos_exchange_branches_and_merge_commit(tmp_path, monkeypatch, capsys, server):
    address = f"127.0.0.1:{server.port}"
    a, b = tmp_path / "a", tmp_path / "b"
    _, feature, main_tip, merge = _repo_with_merge_commit(monkeypatch, a)

    assert _run(monkeypatch, a, "push", address, "--all", "--token", "test-token") == 0

    b.mkdir()
    assert _run(monkeypatch, b, "init") == 0
    capsys.readouterr()
    assert _run(monkeypatch, b, "ls-remote", address, "--token", "test-token") == 0
    assert capsys.readouterr().out == f"{feature}\tfeature\n{merge}\tmain\n"

    # feature isn't checked out in B, so it's imported by ref alone.
    assert _run(monkeypatch, b, "pull", address, "feature", "--token", "test-token") == 0
    assert CommitManager(b).read_ref("feature") == feature
    capsys.readouterr()
    assert _run(monkeypatch, b, "pull", address, "feature", "--token", "test-token") == 0
    assert "feature is up to date" in capsys.readouterr().out

    # The merge commit's full history (both parents) arrives intact.
    assert _run(monkeypatch, b, "fetch", address, "main", "--token", "test-token") == 0
    receiver = CommitManager(b)
    assert receiver.read_commit(merge).parents == [main_tip, feature]
    assert set(receiver.walk_history(merge)) == set(CommitManager(a).walk_history(merge))

    # A and B both commit on feature; A pushes first, so B's push is rejected.
    assert _run(monkeypatch, b, "checkout", "feature") == 0
    assert (b / "f.txt").read_text() == "feature\n"
    _commit_file(monkeypatch, b, "f.txt", "from b\n", "b")
    assert _run(monkeypatch, a, "checkout", "feature") == 0
    a_feature = _commit_file(monkeypatch, a, "f.txt", "from a\n", "a")
    assert _run(monkeypatch, a, "push", address, "feature", "--token", "test-token") == 0

    capsys.readouterr()
    assert _run(monkeypatch, b, "push", address, "feature", "--token", "test-token") == 1
    assert "pull first" in capsys.readouterr().err
    assert CommitManager(server.repo_path).read_ref("feature") == a_feature


@needs_m3_integration
def test_pull_into_checked_out_branch_and_resolve_conflict(tmp_path, monkeypatch, server):
    address = f"127.0.0.1:{server.port}"
    a, b = tmp_path / "a", tmp_path / "b"
    _, _, _, merge = _repo_with_merge_commit(monkeypatch, a)
    assert _run(monkeypatch, a, "push", address, "main", "--token", "test-token") == 0

    b.mkdir()
    assert _run(monkeypatch, b, "init") == 0
    assert _run(monkeypatch, b, "pull", address, "main", "--token", "test-token") == 0
    assert CommitManager(b).read_ref("main") == merge
    assert (b / "a.txt").read_text() == "main\n"

    # Same line changed on both sides -> pull reports the conflict.
    a_tip = _commit_file(monkeypatch, a, "a.txt", "from a\n", "a")
    assert _run(monkeypatch, a, "push", address, "main", "--token", "test-token") == 0
    b_tip = _commit_file(monkeypatch, b, "a.txt", "from b\n", "b")
    assert _run(monkeypatch, b, "pull", address, "main", "--token", "test-token") == 1
    assert "<<<<<<<" in (b / "a.txt").read_text()

    # Resolve -> add -> commit, across fresh CLI calls, yields a two-parent commit.
    resolved = _commit_file(monkeypatch, b, "a.txt", "resolved\n", "resolve")
    assert CommitManager(b).read_commit(resolved).parents == [b_tip, a_tip]
    assert _run(monkeypatch, b, "push", address, "main", "--token", "test-token") == 0
