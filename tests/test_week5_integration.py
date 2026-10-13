"""Week 5 regressions across real storage, diffing, merging, and the CLI."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from minigit.commits import _merge_text

PROJECT = Path(__file__).resolve().parents[1]


def cli(root, *args, expected=0):
    env = dict(os.environ, PYTHONPATH=str(PROJECT))
    result = subprocess.run(
        [sys.executable, "-m", "minigit.cli", *args],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == expected, result.stdout + result.stderr
    return result.stdout + result.stderr


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize(
    "base,ours,theirs,expected",
    [
        ("a\nb\nc\n", "A\nb\nc\n", "insert\na\nb\nc\n", "insert\nA\nb\nc\n"),
        ("a\nb\nc\n", "a\nc\n", "a\nB\nc\n", None),
        ("a\nb\nc", "A\nb\nc", "a\nb\nC", "A\nb\nC"),
        ("", "ours", "theirs", None),
        ("a\nb\nc\n", "a\nx\nb\nc\n", "a\nb\nC\n", "a\nx\nb\nC\n"),
    ],
)
def test_text_merge_uses_base_line_coordinates(base, ours, theirs, expected, reverse):
    if reverse:
        ours, theirs = theirs, ours
    assert _merge_text(base, ours, theirs) == expected


@pytest.mark.parametrize("delete_ours", [False, True])
@pytest.mark.parametrize("resolution", ["./file.txt", "file.txt", "delete"])
def test_conflict_resolution_across_cli_processes(tmp_path, delete_ours, resolution):
    from minigit.commits import CommitManager

    cli(tmp_path, "init")
    path = tmp_path / "file.txt"
    path.write_text("base")
    cli(tmp_path, "add", "file.txt")
    cli(tmp_path, "commit", "-m", "base")
    cli(tmp_path, "branch", "feature")
    if delete_ours:
        path.unlink()
    else:
        path.write_text("ours")
    cli(tmp_path, "add", "./file.txt")
    cli(tmp_path, "commit", "-m", "ours")
    cli(tmp_path, "checkout", "feature")
    path.write_text("theirs")
    cli(tmp_path, "add", "file.txt")
    cli(tmp_path, "commit", "-m", "theirs")
    cli(tmp_path, "checkout", "main")
    manager = CommitManager(tmp_path)
    parents = [manager.read_ref("main"), manager.read_ref("feature")]
    cli(tmp_path, "merge", "feature", expected=1)
    markers = path.read_text().splitlines()
    assert "=======" in markers
    assert ">>>>>>> feature" in markers
    cli(tmp_path, "commit", "-m", "early", expected=1)
    cli(tmp_path, "checkout", "feature", expected=1)
    cli(tmp_path, "merge", "feature", expected=1)
    if resolution == "delete":
        path.unlink()
        cli(tmp_path, "add", "./file.txt")
    else:
        path.write_text("resolved\n")
        cli(tmp_path, "add", resolution)
    cli(tmp_path, "commit", "-m", "Resolved by hand")
    commit = manager.read_commit(manager.read_ref("main"))
    assert commit.parents == parents
    assert commit.message == "Resolved by hand"
    assert cli(tmp_path, "status").strip() == "clean"
    assert not list((tmp_path / ".minigit").glob("MERGE_*"))


def test_add_unknown_path_fails_without_changing_index(tmp_path):
    cli(tmp_path, "init")
    index = tmp_path / ".minigit/index"
    before = index.read_bytes()
    cli(tmp_path, "add", "missing.txt", expected=1)
    assert index.read_bytes() == before


def test_packed_three_way_merge_push_and_stale_rejection(tmp_path):
    import threading

    from minigit.commits import CommitManager
    from minigit.remote import RemoteClient, RemoteServer

    local = tmp_path / "local"
    remote = tmp_path / "remote"
    local.mkdir()
    remote.mkdir()
    for root in (local, remote):
        cli(root, "init")
    path = local / "file.txt"
    path.write_bytes(b"a\nb\nc\n")
    cli(local, "add", "file.txt")
    cli(local, "commit", "-m", "base")
    cli(local, "branch", "feature")
    path.write_bytes(b"A\nb\nc\n")
    cli(local, "add", "file.txt")
    cli(local, "commit", "-m", "ours")
    cli(local, "branch", "stale")
    cli(local, "checkout", "feature")
    path.write_bytes(b"a\nb\nC\n")
    cli(local, "add", "file.txt")
    cli(local, "commit", "-m", "theirs")
    cli(local, "checkout", "main")
    manager = CommitManager(local)
    parents = [manager.read_ref("main"), manager.read_ref("feature")]
    client = RemoteClient(local)
    reachable = client.collect_reachable("main") | client.collect_reachable("feature")
    manager.store.pack_objects(reachable)
    for obj_hash in reachable:
        manager.store._object_path(obj_hash).unlink()
    cli(local, "merge", "feature")
    assert path.read_bytes() == b"A\nb\nC\n"
    assert cli(local, "status").strip() == "clean"
    assert cli(local, "diff") == ""
    tip = manager.read_ref("main")
    assert manager.read_commit(tip).parents == parents
    reachable = client.collect_reachable("main")
    manager.store.pack_objects(reachable)
    for obj_hash in reachable:
        manager.store._object_path(obj_hash).unlink(missing_ok=True)

    server = RemoteServer(remote, token="test-token", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    address = f"127.0.0.1:{server.port}"
    receiver = CommitManager(remote)
    original_head = (remote / ".minigit/HEAD").read_bytes()
    original_index = (remote / ".minigit/index").read_bytes()
    try:
        cli(local, "push", address, "main", "--token", "test-token")
        assert receiver.read_ref("main") == tip
        receiver.store.pack_objects(reachable)
        for obj_hash in reachable:
            receiver.store._object_path(obj_hash).unlink()
            assert receiver.store.read_object(obj_hash) == manager.store.read_object(obj_hash)
        before = sorted(receiver.store.objects_dir.rglob("*"))
        assert "up to date" in cli(local, "push", address, "main", "--token", "test-token")
        assert sorted(receiver.store.objects_dir.rglob("*")) == before
        cli(local, "checkout", "stale")
        path.write_bytes(b"stale writer\n")
        cli(local, "add", "file.txt")
        cli(local, "commit", "-m", "stale writer")
        manager.write_ref("main", manager.read_ref("stale"))
        assert "diverged" in cli(
            local, "push", address, "main", "--token", "test-token", expected=1
        )
        assert receiver.read_ref("main") == tip
        assert (remote / ".minigit/HEAD").read_bytes() == original_head
        assert (remote / ".minigit/index").read_bytes() == original_index
        assert sorted(remote.iterdir()) == [remote / ".minigit"]
    finally:
        server.close()
        thread.join(timeout=2)
    assert not thread.is_alive()
