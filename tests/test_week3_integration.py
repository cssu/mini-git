import threading

from minigit.cli import main
from minigit.commits import CommitManager
from minigit.index import WorkingTree
from minigit.objects import ObjectStore
from minigit.remote import RemoteClient, RemoteServer


def test_cli_commit_status_and_history(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert main(["init"]) == 0
    (tmp_path / "src").mkdir()
    file = tmp_path / "src" / "hello.txt"
    file.write_text("first")
    assert main(["add", "src/hello.txt"]) == 0
    capsys.readouterr()
    assert main(["status"]) == 0
    assert capsys.readouterr().out == "staged:\n  src/hello.txt\n"
    assert main(["commit", "-m", "first"]) == 0
    manager = CommitManager(tmp_path)
    first = manager.read_ref("main")
    first_tree = manager.get_head_tree()
    entries = WorkingTree(tmp_path).read_tree_entries(first_tree)
    assert [e.path for e in entries] == ["src/hello.txt"]
    assert manager.store.read_object(entries[0].hash) == ("blob", b"first")
    capsys.readouterr()
    assert main(["status"]) == 0
    assert capsys.readouterr().out == "clean\n"
    file.write_text("second")
    assert main(["add", "src/hello.txt"]) == 0
    file.write_text("third")
    assert main(["status"]) == 0
    assert capsys.readouterr().out == "staged:\n  src/hello.txt\nnot staged:\n  src/hello.txt\n"
    assert main(["commit", "-m", "second"]) == 0
    second = manager.read_ref("main")
    assert manager.read_commit(second).parents == [first]
    assert manager.store.read_object(entries[0].hash) == ("blob", b"first")
    capsys.readouterr()
    assert main(["log"]) == 0
    assert capsys.readouterr().out == f"{second[:7]} second\n{first[:7]} first\n"


def test_fetch_real_merge_history_and_nested_trees(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "src").mkdir()
    file = source / "src" / "data.bin"
    file.write_bytes(b"binary\x00\ncontent")
    wt = WorkingTree(source)
    wt.stage_file("src/data.bin")
    manager = CommitManager(source)
    first_tree = wt.build_tree_from_index()
    base = manager.create_commit(first_tree, [], "Test <test@example.com>", "base")
    left = manager.create_commit(first_tree, [base], "Test <test@example.com>", "left")
    file.write_bytes(b"changed")
    wt.stage_file("src/data.bin")
    second_tree = wt.build_tree_from_index()
    right = manager.create_commit(second_tree, [base], "Test <test@example.com>", "right")
    tip = manager.create_commit(second_tree, [left, right], "Test <test@example.com>", "merge")
    source_client = RemoteClient(source)
    reachable = source_client.collect_reachable("main")
    assert {base, left, right, tip, first_tree, second_tree} <= reachable
    assert len(reachable) == 10  # four commits, four trees, two blobs
    server = RemoteServer(source, token="test-token")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    destination = tmp_path / "destination"
    client = RemoteClient(destination)
    try:
        client.fetch_objects(f"127.0.0.1:{server.port}", sorted(reachable), "test-token")
    finally:
        server.close()
        thread.join(timeout=2)
    assert not thread.is_alive()
    store = ObjectStore(destination)
    for obj_hash in reachable:
        assert store.read_object(obj_hash) == manager.store.read_object(obj_hash)
    client.commits.write_ref("main", tip)
    assert client.collect_reachable("main") == reachable
    assert client.commits.log() == manager.log()
