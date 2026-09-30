import threading
from pathlib import Path

from minigit.cli import main
from minigit.commits import CommitManager
from minigit.remote import RemoteClient, RemoteServer


def test_week4_cli_checkpoint_and_push(tmp_path, monkeypatch, capsys):
    local = tmp_path / "local"
    local.mkdir()
    monkeypatch.chdir(local)
    assert main(["init"]) == 0
    (local / "file.txt").write_text("A\n")
    assert main(["add", "file.txt"]) == 0
    assert main(["commit", "-m", "A"]) == 0
    manager = CommitManager(local)
    a = manager.read_ref("main")
    assert main(["branch", "feature"]) == 0
    assert main(["checkout", "feature"]) == 0
    (local / "file.txt").write_text("B\n")
    assert main(["add", "file.txt"]) == 0
    assert main(["commit", "-m", "B"]) == 0
    b = manager.read_ref("feature")
    assert a != b
    assert main(["checkout", "main"]) == 0
    assert (local / "file.txt").read_text() == "A\n"
    assert main(["merge", "feature"]) == 0
    assert (local / "file.txt").read_text() == "B\n"
    assert manager.read_head() == "main"
    assert manager.read_ref("main") == manager.read_ref("feature") == b
    capsys.readouterr()
    assert main(["status"]) == 0
    assert capsys.readouterr().out.strip() == "clean"

    remote = tmp_path / "remote"
    remote.mkdir()
    server = RemoteServer(remote, token="test-token")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        assert main(["push", f"127.0.0.1:{server.port}", "main", "--token", "test-token"]) == 0
        receiver = CommitManager(remote)
        assert receiver.read_ref("main") == b
        assert receiver.walk_history(b) == [b, a]
        for obj_hash in RemoteClient(local).collect_reachable("main"):
            assert receiver.store.read_object(obj_hash) == manager.store.read_object(obj_hash)
        assert not (remote / ".minigit/index").exists()
        assert not (remote / ".minigit/HEAD").exists()
        assert list(Path(remote).iterdir()) == [remote / ".minigit"]
    finally:
        server.close()
        thread.join(timeout=2)
    assert not thread.is_alive()
