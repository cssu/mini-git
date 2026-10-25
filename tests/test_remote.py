import hashlib
import socket
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

import pytest

from minigit.cli import main
from minigit.commits import CommitManager
from minigit.errors import (
    MergeConflictError,
    MiniGitError,
    NetworkProtocolError,
)
from minigit.objects import ObjectStore
from minigit.objects import TreeEntry as RealTreeEntry
from minigit.remote import RemoteClient, RemoteServer, receive_line, recv_exact, send_line

KNOWN_HASH = "a" * 40


class FakeObjectStore:
    """Matches ObjectStore's hashing so wire-level tests can check hashes without disk I/O."""

    def __init__(self):
        self._objects = {}

    def hash_object(self, data: bytes, obj_type: str) -> str:
        return hashlib.sha1(f"{obj_type} {len(data)}".encode() + b"\0" + data).hexdigest()

    def write_object(self, data: bytes, obj_type: str) -> str:
        obj_hash = self.hash_object(data, obj_type)
        self._objects[obj_hash] = (obj_type, data)
        return obj_hash

    def read_object(self, hash: str) -> tuple[str, bytes]:
        return self._objects[hash]


class FakeCommitManager:
    pass


def make_client():
    return RemoteClient(store=FakeObjectStore(), commits=FakeCommitManager())


@pytest.fixture
def remote_server(tmp_path):
    refs_dir = tmp_path / ".minigit" / "refs" / "heads"
    refs_dir.mkdir(parents=True)
    (refs_dir / "main").write_text(KNOWN_HASH + "\n")

    server = RemoteServer(repo_path=str(tmp_path), token="tok", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    server._thread = thread  # test-only: lets a test wait for a manual close() to land

    yield server

    server.close()
    thread.join(timeout=2)


def test_parse_address_valid():
    client = make_client()
    assert client._parse_address("127.0.0.1:9418") == ("127.0.0.1", 9418)


def test_parse_address_no_colon_raises():
    client = make_client()
    with pytest.raises(NetworkProtocolError):
        client._parse_address("localhost")


def test_parse_address_non_numeric_port_raises():
    client = make_client()
    with pytest.raises(NetworkProtocolError):
        client._parse_address("host:abc")


def test_parse_address_port_zero_raises():
    client = make_client()
    with pytest.raises(NetworkProtocolError):
        client._parse_address("host:0")


def test_parse_address_empty_raises():
    client = make_client()
    with pytest.raises(NetworkProtocolError):
        client._parse_address("")


def test_push_empty_token_raises():
    client = make_client()
    with pytest.raises(NetworkProtocolError):
        client.push("127.0.0.1:9418", "main", "")


# --- push: real M1/M3 instances on both ends, exercising the full PUSH/HAVE/PUT/DONE session ---


@dataclass
class PushPair:
    client: RemoteClient
    local_store: ObjectStore
    local_commits: CommitManager
    remote_store: ObjectStore
    remote_commits: CommitManager
    server: RemoteServer
    address: str


@pytest.fixture
def push_pair(tmp_path):
    remote_root = tmp_path / "remote"
    remote_root.mkdir()
    remote_store = ObjectStore(str(remote_root))
    remote_commits = CommitManager(str(remote_root), store=remote_store)
    server = RemoteServer(
        repo_path=str(remote_root), token="tok", port=0, store=remote_store, commits=remote_commits
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    local_root = tmp_path / "local"
    local_root.mkdir()
    local_store = ObjectStore(str(local_root))
    local_commits = CommitManager(str(local_root), store=local_store)
    client = RemoteClient(repo_path=str(local_root), store=local_store, commits=local_commits)

    yield PushPair(
        client=client,
        local_store=local_store,
        local_commits=local_commits,
        remote_store=remote_store,
        remote_commits=remote_commits,
        server=server,
        address=f"127.0.0.1:{server.port}",
    )

    server.close()
    thread.join(timeout=2)


def _write_tree(store: ObjectStore, files: dict) -> str:
    """Build (possibly nested) tree objects for a flat {path: content} mapping."""

    entries = []
    subdirs: dict[str, dict] = {}
    for path, content in files.items():
        top, sep, rest = path.partition("/")
        if sep:
            subdirs.setdefault(top, {})[rest] = content
        else:
            blob_hash = store.write_object(content, "blob")
            entries.append(RealTreeEntry("100644", "blob", blob_hash, top))
    for name, nested in subdirs.items():
        entries.append(RealTreeEntry("40000", "tree", _write_tree(store, nested), name))
    return store.write_tree(entries)


def _commit(
    store: ObjectStore, commits: CommitManager, files: dict, parents: list, message="msg"
) -> str:
    tree_hash = _write_tree(store, files)
    return commits.create_commit(tree_hash, parents, "tester <t@example.com>", message)


def test_push_wrong_token_raises(push_pair):
    _commit(push_pair.local_store, push_pair.local_commits, {"a.txt": b"1"}, [])
    with pytest.raises(NetworkProtocolError):
        push_pair.client.push(push_pair.address, "main", "wrong")
    assert push_pair.remote_commits.read_ref("main") is None


def test_push_missing_local_branch_raises(push_pair):
    with pytest.raises(NetworkProtocolError):
        push_pair.client.push(push_pair.address, "nope", "tok")


def test_push_closed_port_raises_network_protocol_error(push_pair):
    _commit(push_pair.local_store, push_pair.local_commits, {"a.txt": b"1"}, [])
    push_pair.server.close()  # closing the listening socket refuses new connections immediately
    with pytest.raises(NetworkProtocolError):
        push_pair.client.push(push_pair.address, "main", "tok")


def test_push_nested_files_multiple_commits(push_pair):
    pair = push_pair
    c1 = _commit(pair.local_store, pair.local_commits, {"a.txt": b"one"}, [])
    c2 = _commit(
        pair.local_store,
        pair.local_commits,
        {"a.txt": b"one", "src/main.py": b"print(1)"},
        [c1],
    )

    pair.client.push(pair.address, "main", "tok")

    assert pair.remote_commits.read_ref("main") == c2
    tree_entries = pair.remote_store.read_tree(pair.remote_commits.read_commit(c2).tree)
    src_entry = next(e for e in tree_entries if e.name == "src")
    nested = pair.remote_store.read_tree(src_entry.hash)
    assert nested[0].name == "main.py"

    # server never touches its own HEAD/index while handling a push
    assert not (Path(pair.server.repo_path) / ".minigit" / "HEAD").exists()
    assert not (Path(pair.server.repo_path) / ".minigit" / "index").exists()


def test_push_to_empty_branch_then_one_more_commit(push_pair):
    pair = push_pair
    assert pair.remote_commits.read_ref("main") is None

    c1 = _commit(pair.local_store, pair.local_commits, {"a.txt": b"one"}, [])
    pair.client.push(pair.address, "main", "tok")
    assert pair.remote_commits.read_ref("main") == c1

    c2 = _commit(pair.local_store, pair.local_commits, {"a.txt": b"two"}, [c1])
    pair.client.push(pair.address, "main", "tok")
    assert pair.remote_commits.read_ref("main") == c2


def test_push_up_to_date_reports_and_stops(push_pair, capsys):
    pair = push_pair
    _commit(pair.local_store, pair.local_commits, {"a.txt": b"one"}, [])
    pair.client.push(pair.address, "main", "tok")
    capsys.readouterr()

    pair.client.push(pair.address, "main", "tok")
    assert "up to date" in capsys.readouterr().out


def test_repeat_push_transfers_no_objects(push_pair):
    pair = push_pair
    _commit(pair.local_store, pair.local_commits, {"a.txt": b"one"}, [])
    pair.client.push(pair.address, "main", "tok")

    put_calls = []
    original_put = RemoteClient._put_object
    RemoteClient._put_object = lambda self, sock, buf, obj_hash: put_calls.append(obj_hash)
    try:
        pair.client.push(pair.address, "main", "tok")
    finally:
        RemoteClient._put_object = original_put

    assert put_calls == []


def test_shared_blobs_transfer_once_and_receiver_reads_full_history(push_pair):
    pair = push_pair
    c1 = _commit(pair.local_store, pair.local_commits, {"shared.txt": b"same", "a.txt": b"1"}, [])
    c2 = _commit(pair.local_store, pair.local_commits, {"shared.txt": b"same", "a.txt": b"2"}, [c1])

    put_calls = []
    original_put = RemoteClient._put_object

    def counting_put(self, sock, buf, obj_hash):
        put_calls.append(obj_hash)
        return original_put(self, sock, buf, obj_hash)

    RemoteClient._put_object = counting_put
    try:
        pair.client.push(pair.address, "main", "tok")
    finally:
        RemoteClient._put_object = original_put

    assert len(put_calls) == len(set(put_calls))  # every object uploaded at most once

    # a *fresh* pair of M1/M3 instances, as if a different process opened the repo
    fresh_store = ObjectStore(pair.server.repo_path)
    fresh_commits = CommitManager(pair.server.repo_path, store=fresh_store)
    assert fresh_commits.read_ref("main") == c2
    assert set(fresh_commits.walk_history(c2)) == {c1, c2}
    shared_hash = pair.local_store.hash_object(b"same", "blob")
    assert fresh_store.read_object(shared_hash) == ("blob", b"same")


def test_push_bad_auth_leaves_ref_unchanged(push_pair):
    pair = push_pair
    _commit(pair.local_store, pair.local_commits, {"a.txt": b"1"}, [])
    with pytest.raises(NetworkProtocolError):
        pair.client.push(pair.address, "main", "wrong-token")
    assert pair.remote_commits.read_ref("main") is None


def test_push_bad_object_payload_leaves_ref_unchanged(push_pair):
    pair = push_pair
    c1 = _commit(pair.local_store, pair.local_commits, {"a.txt": b"1"}, [])

    sock = socket.create_connection(("127.0.0.1", pair.server.port), timeout=5)
    buf = bytearray()
    try:
        send_line(sock, "AUTH tok")
        assert receive_line(sock, buf) == "OK"
        send_line(sock, f"PUSH main - {c1}")
        assert receive_line(sock, buf) == "OK"
        send_line(sock, f"HAVE {c1}")
        assert receive_line(sock, buf) == "NO"
        send_line(sock, f"PUT {c1}")
        bogus = b"not the right content"
        send_line(sock, f"OBJ commit {len(bogus)}")
        sock.sendall(bogus)
        assert receive_line(sock, buf).startswith("ERR")
    finally:
        sock.close()

    assert pair.remote_commits.read_ref("main") is None


def test_push_disconnect_before_done_leaves_ref_unchanged(push_pair):
    pair = push_pair
    c1 = _commit(pair.local_store, pair.local_commits, {"a.txt": b"1"}, [])

    sock = socket.create_connection(("127.0.0.1", pair.server.port), timeout=5)
    buf = bytearray()
    send_line(sock, "AUTH tok")
    assert receive_line(sock, buf) == "OK"
    send_line(sock, f"PUSH main - {c1}")
    assert receive_line(sock, buf) == "OK"
    send_line(sock, f"HAVE {c1}")
    assert receive_line(sock, buf) == "NO"
    sock.close()  # disconnect instead of PUT + DONE

    assert pair.remote_commits.read_ref("main") is None


def test_push_rejects_unrelated_remote_tip(push_pair):
    pair = push_pair
    unrelated = _commit(pair.remote_store, pair.remote_commits, {"other.txt": b"x"}, [])
    local_tip = _commit(pair.local_store, pair.local_commits, {"a.txt": b"1"}, [])

    with pytest.raises(NetworkProtocolError, match="don't have locally - pull first"):
        pair.client.push(pair.address, "main", "tok")
    assert pair.remote_commits.read_ref("main") == unrelated
    assert pair.local_commits.read_ref("main") == local_tip


def test_push_rejects_ref_changed_during_transfer(push_pair):
    pair = push_pair
    c1 = _commit(pair.local_store, pair.local_commits, {"a.txt": b"1"}, [])

    sock = socket.create_connection(("127.0.0.1", pair.server.port), timeout=5)
    buf = bytearray()
    try:
        send_line(sock, "AUTH tok")
        assert receive_line(sock, buf) == "OK"
        send_line(sock, f"PUSH main - {c1}")
        assert receive_line(sock, buf) == "OK"

        # simulate another push landing on the remote mid-transfer
        pair.remote_commits.write_ref("main", "b" * 40)

        for obj_hash in pair.client.collect_reachable("main"):
            send_line(sock, f"HAVE {obj_hash}")
            reply = receive_line(sock, buf)
            if reply == "NO":
                obj_type, content = pair.local_store.read_object(obj_hash)
                send_line(sock, f"PUT {obj_hash}")
                send_line(sock, f"OBJ {obj_type} {len(content)}")
                sock.sendall(content)
                assert receive_line(sock, buf) == "OK"
        send_line(sock, "DONE")
        assert receive_line(sock, buf).startswith("ERR")
    finally:
        sock.close()

    assert pair.remote_commits.read_ref("main") == "b" * 40


def test_serve_objects_returns_requested_object(remote_server, tmp_path):
    store = ObjectStore(str(tmp_path))
    blob_hash = store.write_object(b"hello world", "blob")

    sock = socket.create_connection(("127.0.0.1", remote_server.port), timeout=5)
    buf = bytearray()
    try:
        send_line(sock, "AUTH tok")
        assert receive_line(sock, buf) == "OK"
        send_line(sock, "REF main")
        receive_line(sock, buf)  # REF main <hash>: covered by the push/pull tests

        send_line(sock, f"WANT {blob_hash}")
        header = receive_line(sock, buf)
        obj_type, length = header.removeprefix("OBJ ").split(" ")
        assert obj_type == "blob"
        assert recv_exact(sock, buf, int(length)) == b"hello world"

        send_line(sock, "DONE")
    finally:
        sock.close()


def test_serve_objects_unknown_hash_sends_err_and_stays_open(remote_server):
    sock = socket.create_connection(("127.0.0.1", remote_server.port), timeout=5)
    buf = bytearray()
    try:
        send_line(sock, "AUTH tok")
        receive_line(sock, buf)
        send_line(sock, "REF main")
        receive_line(sock, buf)

        send_line(sock, "WANT " + "f" * 40)
        assert receive_line(sock, buf).startswith("ERR")

        send_line(sock, "DONE")
    finally:
        sock.close()


def test_fetch_objects_stores_multiple_objects_from_one_connection(remote_server, tmp_path):
    remote_store = ObjectStore(remote_server.repo_path)
    blob_hash = remote_store.write_object(b"binary:\x00\nbytes", "blob")
    commit_hash = remote_store.write_object(b"tree deadbeef", "commit")

    local_store = ObjectStore(str(tmp_path / "local"))
    client = RemoteClient(store=local_store, commits=FakeCommitManager())

    client.fetch_objects(
        f"127.0.0.1:{remote_server.port}", [blob_hash, commit_hash, blob_hash], "tok"
    )

    assert local_store.read_object(blob_hash) == ("blob", b"binary:\x00\nbytes")
    assert local_store.read_object(commit_hash) == ("commit", b"tree deadbeef")


def test_fetch_objects_unknown_hash_raises(remote_server, tmp_path):
    client = RemoteClient(store=ObjectStore(str(tmp_path / "local")), commits=FakeCommitManager())
    with pytest.raises(NetworkProtocolError):
        client.fetch_objects(f"127.0.0.1:{remote_server.port}", ["f" * 40], "tok")


def test_fetch_objects_empty_token_raises():
    client = make_client()
    with pytest.raises(NetworkProtocolError):
        client.fetch_objects("127.0.0.1:9418", ["a" * 40], "")


class FakeReceiveSocket:
    """Hands back one pre-scripted OBJ (or ERR) reply for `_receive_object`."""

    def __init__(self, reply: bytes):
        self._reply = reply

    def recv(self, size):
        chunk, self._reply = self._reply[:size], self._reply[size:]
        return chunk


def test_receive_object_wrong_hash_raises():
    client = make_client()
    sock = FakeReceiveSocket(b"OBJ blob 5\nnope!")
    with pytest.raises(NetworkProtocolError):
        client._receive_object(sock, bytearray(), "a" * 40)


def test_receive_object_err_reply_raises():
    client = make_client()
    sock = FakeReceiveSocket(b"ERR unknown object\n")
    with pytest.raises(NetworkProtocolError):
        client._receive_object(sock, bytearray(), "a" * 40)


def test_receive_object_malformed_header_raises():
    client = make_client()
    sock = FakeReceiveSocket(b"OBJ blob notanumber\n")
    with pytest.raises(NetworkProtocolError):
        client._receive_object(sock, bytearray(), "a" * 40)


def test_receive_object_dropped_connection_raises():
    client = make_client()
    sock = FakeReceiveSocket(b"OBJ blob 20\nshort")
    with pytest.raises(NetworkProtocolError):
        client._receive_object(sock, bytearray(), "a" * 40)


class FakeSocket:
    """Hands back pre-scripted chunks instead of reading a real socket."""

    def __init__(self, chunks):
        self._chunks = list(chunks)

    def recv(self, size):
        return self._chunks.pop(0) if self._chunks else b""


def test_receive_line_splits_two_lines_from_one_packet():
    sock = FakeSocket([b"AUTH tok\nREF main\n"])
    buf = bytearray()

    assert receive_line(sock, buf) == "AUTH tok"
    assert receive_line(sock, buf) == "REF main"


def test_recv_exact_reads_payload_split_across_packets():
    sock = FakeSocket([b"hel", b"lo!"])
    buf = bytearray()

    assert recv_exact(sock, buf, 6) == b"hello!"


def test_recv_exact_leaves_trailing_bytes_for_next_read():
    sock = FakeSocket([b"helloREF main\n"])
    buf = bytearray()

    assert recv_exact(sock, buf, 5) == b"hello"
    assert receive_line(sock, buf) == "REF main"


def test_recv_exact_raises_on_connection_closed_mid_payload():
    sock = FakeSocket([b"he"])
    buf = bytearray()

    with pytest.raises(NetworkProtocolError):
        recv_exact(sock, buf, 5)


# --- collect_reachable: fakes for the not-yet-merged M1 #16 / M3 #18 interfaces ---


class TreeEntry(NamedTuple):
    mode: str
    type: str
    hash: str
    name: str


class CommitData(NamedTuple):
    tree: str
    parents: list[str]
    author: str
    committer: str
    message: str


class FakeTreeStore:
    """`read_tree` double: hash -> entries, wired up directly by each test."""

    def __init__(self):
        self.trees: dict[str, list[TreeEntry]] = {}
        self.read_tree_calls: list[str] = []

    def read_tree(self, tree_hash: str) -> list[TreeEntry]:
        self.read_tree_calls.append(tree_hash)
        return self.trees[tree_hash]


class FakeCommitGraph:
    """`read_ref`/`read_commit`/`walk_history` double, wired up directly by each test."""

    def __init__(self):
        self.refs: dict[str, str] = {}
        self.commits: dict[str, CommitData] = {}

    def read_ref(self, branch: str) -> str | None:
        return self.refs.get(branch)

    def read_commit(self, commit_hash: str) -> CommitData:
        return self.commits[commit_hash]

    def walk_history(self, start_hash: str) -> list[str]:
        order: list[str] = []
        seen: set[str] = set()

        def visit(commit_hash: str) -> None:
            if commit_hash in seen:
                return
            seen.add(commit_hash)
            order.append(commit_hash)
            for parent in self.commits[commit_hash].parents:
                visit(parent)

        visit(start_hash)
        return order


def test_collect_reachable_unborn_branch_returns_empty_set():
    client = RemoteClient(store=FakeTreeStore(), commits=FakeCommitGraph())
    assert client.collect_reachable("main") == set()


def test_collect_reachable_single_commit_collects_tree_and_blobs():
    store = FakeTreeStore()
    commits = FakeCommitGraph()

    store.trees["tree1"] = [
        TreeEntry("100644", "blob", "blobA", "a.txt"),
        TreeEntry("100644", "blob", "blobB", "b.txt"),
    ]
    commits.commits["c1"] = CommitData("tree1", [], "a", "a", "root")
    commits.refs["main"] = "c1"

    client = RemoteClient(store=store, commits=commits)
    assert client.collect_reachable("main") == {"c1", "tree1", "blobA", "blobB"}


def test_collect_reachable_nested_trees():
    store = FakeTreeStore()
    commits = FakeCommitGraph()

    store.trees["root-tree"] = [
        TreeEntry("100644", "blob", "readme", "README.md"),
        TreeEntry("40000", "tree", "src-tree", "src"),
    ]
    store.trees["src-tree"] = [TreeEntry("100644", "blob", "mainpy", "main.py")]
    commits.commits["c1"] = CommitData("root-tree", [], "a", "a", "root")
    commits.refs["main"] = "c1"

    client = RemoteClient(store=store, commits=commits)
    assert client.collect_reachable("main") == {
        "c1",
        "root-tree",
        "readme",
        "src-tree",
        "mainpy",
    }


def test_collect_reachable_dedupes_shared_blob_and_tree_across_commits():
    store = FakeTreeStore()
    commits = FakeCommitGraph()

    store.trees["shared-tree"] = [TreeEntry("100644", "blob", "shared-blob", "f.txt")]
    commits.commits["c1"] = CommitData("shared-tree", [], "a", "a", "root")
    commits.commits["c2"] = CommitData("shared-tree", ["c1"], "a", "a", "unchanged")
    commits.refs["main"] = "c2"

    client = RemoteClient(store=store, commits=commits)
    reachable = client.collect_reachable("main")

    assert reachable == {"c1", "c2", "shared-tree", "shared-blob"}
    assert store.read_tree_calls == ["shared-tree"]  # walked once, not once per commit


def test_collect_reachable_includes_both_merge_parents():
    store = FakeTreeStore()
    commits = FakeCommitGraph()

    store.trees["tree-a"] = [TreeEntry("100644", "blob", "blob-a", "a.txt")]
    store.trees["tree-b"] = [TreeEntry("100644", "blob", "blob-b", "b.txt")]
    store.trees["tree-root"] = []
    store.trees["tree-merge"] = [TreeEntry("100644", "blob", "blob-a", "a.txt")]

    commits.commits["root"] = CommitData("tree-root", [], "a", "a", "root")
    commits.commits["left"] = CommitData("tree-a", ["root"], "a", "a", "left")
    commits.commits["right"] = CommitData("tree-b", ["root"], "a", "a", "right")
    commits.commits["merge"] = CommitData("tree-merge", ["left", "right"], "a", "a", "merge")
    commits.refs["main"] = "merge"

    client = RemoteClient(store=store, commits=commits)
    reachable = client.collect_reachable("main")

    assert reachable == {
        "root",
        "left",
        "right",
        "merge",
        "tree-root",
        "tree-a",
        "tree-b",
        "tree-merge",
        "blob-a",
        "blob-b",
    }


def test_receive_line_invalid_utf8_is_protocol_error():
    with pytest.raises(NetworkProtocolError):
        receive_line(FakeSocket([b"\xff\n"]), bytearray())


@pytest.mark.parametrize("header", [b"OBJ blob \xc2\xb2\n", b"OBJ unknown 0\n"])
def test_receive_object_rejects_invalid_type_and_unicode_length(header):
    with pytest.raises(NetworkProtocolError):
        make_client()._receive_object(FakeReceiveSocket(header), bytearray(), "a" * 40)


def test_server_survives_corrupt_objects_and_invalid_requests(remote_server):
    store = ObjectStore(remote_server.repo_path)
    bad_hash = store.write_object(b"broken", "blob")
    store._object_path(bad_hash).write_bytes(b"not compressed")
    good_hash = store.write_object(b"good", "blob")
    with socket.create_connection(("127.0.0.1", remote_server.port), timeout=5) as sock:
        buf = bytearray()
        send_line(sock, "AUTH tok")
        assert receive_line(sock, buf) == "OK"
        for request in [f"WANT {bad_hash}", "WANT ../../config", "REF ../../config"]:
            send_line(sock, request)
            assert receive_line(sock, buf).startswith("ERR ")
        send_line(sock, f"WANT {good_hash}")
        assert receive_line(sock, buf) == "OBJ blob 4"
        assert recv_exact(sock, buf, 4) == b"good"
        send_line(sock, "DONE")


@pytest.mark.parametrize("command", ["HAVE", "PUT"])
def test_push_corrupt_existing_object_does_not_stop_server(push_pair, command):
    pair = push_pair
    tip = _commit(pair.local_store, pair.local_commits, {"a.txt": b"one"}, [])
    blob = pair.remote_store.write_object(b"one", "blob")
    pair.remote_store._object_path(blob).write_bytes(b"corrupt")
    with socket.create_connection(("127.0.0.1", pair.server.port), timeout=2) as sock:
        buf = bytearray()
        send_line(sock, "AUTH tok")
        assert receive_line(sock, buf) == "OK"
        send_line(sock, f"PUSH main - {tip}")
        assert receive_line(sock, buf) == "OK"
        send_line(sock, f"{command} {blob}")
        if command == "PUT":
            send_line(sock, "OBJ blob 3")
            sock.sendall(b"one")
        assert receive_line(sock, buf).startswith("ERR")
    assert pair.remote_commits.read_ref("main") is None
    with socket.create_connection(("127.0.0.1", pair.server.port), timeout=2) as sock:
        buf = bytearray()
        send_line(sock, "AUTH tok")
        assert receive_line(sock, buf) == "OK"
        send_line(sock, "REF main")
        assert receive_line(sock, buf) == "REF main -"
        send_line(sock, "DONE")


@pytest.mark.parametrize("reply", ["ERR", "ERR invalid branch", "REF other -", "REF main bad"])
def test_push_invalid_ref_reply_is_protocol_error(tmp_path, monkeypatch, reply):
    manager = CommitManager(tmp_path)
    _commit(manager.store, manager, {"a.txt": b"one"}, [])

    class ReplySocket(FakeReceiveSocket):
        def sendall(self, data):
            pass

        def close(self):
            pass

    sock = ReplySocket(f"OK\n{reply}\n".encode())
    monkeypatch.setattr(socket, "create_connection", lambda *args, **kwargs: sock)
    with pytest.raises(NetworkProtocolError, match="invalid"):
        RemoteClient(tmp_path).push("localhost:9418", "main", "tok")


# --- push conflict detection (#34) ---


def _second_client(tmp_path, pair: PushPair) -> RemoteClient:
    """Another independent local repo, pointed at the same remote as `pair.client`."""

    root = tmp_path / "local2"
    root.mkdir()
    store = ObjectStore(str(root))
    return RemoteClient(
        repo_path=str(root), store=store, commits=CommitManager(str(root), store=store)
    )


def _count_puts(monkeypatch) -> list:
    """Record every object `RemoteClient` uploads, while still uploading it."""

    calls = []
    original_put = RemoteClient._put_object

    def counting_put(self, sock, buf, obj_hash):
        calls.append(obj_hash)
        return original_put(self, sock, buf, obj_hash)

    monkeypatch.setattr(RemoteClient, "_put_object", counting_put)
    return calls


def _open_push(pair: PushPair, old: str, new: str) -> tuple[socket.socket, bytearray]:
    """Authenticate a raw connection and send `PUSH main <old> <new>`; returns the reply buffer."""

    sock = socket.create_connection(("127.0.0.1", pair.server.port), timeout=5)
    buf = bytearray()
    send_line(sock, "AUTH tok")
    assert receive_line(sock, buf) == "OK"
    send_line(sock, f"PUSH main {old} {new}")
    return sock, buf


def _upload(sock, buf: bytearray, store: ObjectStore, hashes) -> None:
    """Raw-protocol HAVE/PUT for each hash, uploading whatever the remote is missing."""

    for obj_hash in hashes:
        send_line(sock, f"HAVE {obj_hash}")
        if receive_line(sock, buf) == "NO":
            obj_type, content = store.read_object(obj_hash)
            send_line(sock, f"PUT {obj_hash}")
            send_line(sock, f"OBJ {obj_type} {len(content)}")
            sock.sendall(content)
            assert receive_line(sock, buf) == "OK"


def test_push_fast_forward_then_repeat_transfers_nothing(push_pair, monkeypatch, capsys):
    pair = push_pair
    c1 = _commit(pair.local_store, pair.local_commits, {"a.txt": b"1"}, [])
    pair.client.push(pair.address, "main", "tok")
    c2 = _commit(pair.local_store, pair.local_commits, {"a.txt": b"2"}, [c1])
    pair.client.push(pair.address, "main", "tok")
    assert pair.remote_commits.read_ref("main") == c2

    puts = _count_puts(monkeypatch)
    capsys.readouterr()
    pair.client.push(pair.address, "main", "tok")
    assert puts == []
    assert "up to date" in capsys.readouterr().out
    assert pair.remote_commits.read_ref("main") == c2


def test_push_accepts_ancestor_only_via_second_merge_parent(push_pair):
    pair = push_pair
    remote_tip = _commit(pair.local_store, pair.local_commits, {"a.txt": b"a"}, [])
    pair.client.push(pair.address, "main", "tok")

    # `other` is an unrelated root, so the remote tip is reachable only through
    # the merge commit's *second* parent - a first-parent-only walk would miss it.
    other = _commit(pair.local_store, pair.local_commits, {"b.txt": b"b"}, [])
    merge = _commit(
        pair.local_store, pair.local_commits, {"a.txt": b"a", "b.txt": b"b"}, [other, remote_tip]
    )

    pair.client.push(pair.address, "main", "tok")
    assert pair.remote_commits.read_ref("main") == merge
    assert set(pair.remote_commits.walk_history(merge)) == {merge, other, remote_tip}


def test_push_rejects_remote_ahead(push_pair):
    pair = push_pair
    c1 = _commit(pair.local_store, pair.local_commits, {"a.txt": b"1"}, [])
    c2 = _commit(pair.local_store, pair.local_commits, {"a.txt": b"2"}, [c1])
    pair.client.push(pair.address, "main", "tok")
    pair.local_commits.write_ref("main", c1)  # local falls behind a remote it has seen

    with pytest.raises(NetworkProtocolError, match="ahead of local - pull first"):
        pair.client.push(pair.address, "main", "tok")
    assert pair.remote_commits.read_ref("main") == c2
    assert pair.local_commits.read_ref("main") == c1


def test_push_rejects_diverged(push_pair):
    pair = push_pair
    base = _commit(pair.local_store, pair.local_commits, {"a.txt": b"base"}, [])
    theirs = _commit(pair.local_store, pair.local_commits, {"a.txt": b"theirs"}, [base])
    pair.client.push(pair.address, "main", "tok")
    ours = _commit(pair.local_store, pair.local_commits, {"a.txt": b"ours"}, [base])

    with pytest.raises(NetworkProtocolError, match="diverged - pull and merge first"):
        pair.client.push(pair.address, "main", "tok")
    assert pair.remote_commits.read_ref("main") == theirs
    assert pair.local_commits.read_ref("main") == ours


def test_two_stale_clients_second_rejected(tmp_path, push_pair):
    """Week 5 checkpoint: two repos share a remote tip, commit independently, push both."""

    pair = push_pair
    alice = pair.client
    bob = _second_client(tmp_path, pair)

    base = _commit(pair.local_store, pair.local_commits, {"a.txt": b"base"}, [])
    alice.push(pair.address, "main", "tok")
    bob.fetch_objects(pair.address, sorted(alice.collect_reachable("main")), "tok")
    bob.commits.write_ref("main", base)

    alice_tip = _commit(pair.local_store, pair.local_commits, {"a.txt": b"alice"}, [base])
    bob_tip = _commit(bob.store, bob.commits, {"a.txt": b"bob"}, [base])

    alice.push(pair.address, "main", "tok")
    with pytest.raises(NetworkProtocolError, match="pull first"):
        bob.push(pair.address, "main", "tok")

    assert pair.remote_commits.read_ref("main") == alice_tip
    assert bob.commits.read_ref("main") == bob_tip


def test_stale_push_rejected_by_server(push_pair):
    """A client that skips its own ancestry check still can't overwrite a moved ref."""

    pair = push_pair
    base = _commit(pair.local_store, pair.local_commits, {"a.txt": b"base"}, [])
    pair.client.push(pair.address, "main", "tok")
    moved = _commit(pair.remote_store, pair.remote_commits, {"a.txt": b"moved"}, [base])
    stale = _commit(pair.local_store, pair.local_commits, {"a.txt": b"stale"}, [base])

    sock, buf = _open_push(pair, base, stale)
    try:
        reply = receive_line(sock, buf)
    finally:
        sock.close()

    assert reply == "ERR remote main has moved since you last synced - pull first"
    assert pair.remote_commits.read_ref("main") == moved


def test_push_rejects_existing_ref_moved_during_transfer(push_pair):
    pair = push_pair
    base = _commit(pair.local_store, pair.local_commits, {"a.txt": b"base"}, [])
    pair.client.push(pair.address, "main", "tok")
    new = _commit(pair.local_store, pair.local_commits, {"a.txt": b"new"}, [base])

    sock, buf = _open_push(pair, base, new)
    try:
        assert receive_line(sock, buf) == "OK"
        pair.remote_commits.write_ref("main", "b" * 40)  # another push lands mid-transfer
        _upload(sock, buf, pair.local_store, sorted(pair.client.collect_reachable("main")))
        send_line(sock, "DONE")
        assert receive_line(sock, buf) == "ERR remote main changed during push - pull first"
    finally:
        sock.close()

    assert pair.remote_commits.read_ref("main") == "b" * 40


def test_direct_push_not_fast_forward(push_pair):
    pair = push_pair
    base = _commit(pair.local_store, pair.local_commits, {"a.txt": b"base"}, [])
    pair.client.push(pair.address, "main", "tok")
    unrelated = _commit(pair.local_store, pair.local_commits, {"b.txt": b"other"}, [])

    sock, buf = _open_push(pair, base, unrelated)
    try:
        assert receive_line(sock, buf) == "OK"
        _upload(sock, buf, pair.local_store, sorted(pair.client.collect_reachable("main")))
        send_line(sock, "DONE")
        assert receive_line(sock, buf) == "ERR not a fast-forward"
    finally:
        sock.close()

    assert pair.remote_commits.read_ref("main") == base


def test_direct_push_missing_objects(push_pair):
    pair = push_pair
    tip = _commit(pair.local_store, pair.local_commits, {"a.txt": b"1"}, [])

    sock, buf = _open_push(pair, "-", tip)
    try:
        assert receive_line(sock, buf) == "OK"
        send_line(sock, "DONE")  # claims done without uploading anything
        assert receive_line(sock, buf).startswith("ERR incomplete history")
    finally:
        sock.close()

    assert pair.remote_commits.read_ref("main") is None


@pytest.mark.parametrize("old", ["zzz", "A" * 40, "a" * 39])
def test_direct_push_invalid_old_hash(push_pair, old):
    pair = push_pair
    tip = _commit(pair.local_store, pair.local_commits, {"a.txt": b"1"}, [])

    sock, buf = _open_push(pair, old, tip)
    try:
        assert receive_line(sock, buf) == "ERR invalid old hash"
    finally:
        sock.close()

    assert pair.remote_commits.read_ref("main") is None


def test_retry_after_disconnect_reuses_objects(push_pair, monkeypatch):
    pair = push_pair
    tip = _commit(
        pair.local_store,
        pair.local_commits,
        {"a.txt": b"1", "b.txt": b"2", "dir/c.txt": b"3"},
        [],
    )
    objects = sorted(pair.client.collect_reachable("main"))

    sock, buf = _open_push(pair, "-", tip)
    try:
        assert receive_line(sock, buf) == "OK"
        _upload(sock, buf, pair.local_store, objects[:2])
    finally:
        sock.close()  # drop the connection before DONE
    assert pair.remote_commits.read_ref("main") is None

    puts = _count_puts(monkeypatch)
    pair.client.push(pair.address, "main", "tok")
    assert pair.remote_commits.read_ref("main") == tip
    assert sorted(puts) == objects[2:]


def test_stalled_client_does_not_block_server(tmp_path):
    server = RemoteServer(repo_path=str(tmp_path), token="tok", port=0, conn_timeout=0.5)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    stalled = socket.create_connection(("127.0.0.1", server.port), timeout=5)
    try:
        buf = bytearray()
        send_line(stalled, "AUTH tok")
        assert receive_line(stalled, buf) == "OK"
        send_line(stalled, f"PUSH main - {'a' * 40}")
        assert receive_line(stalled, buf) == "OK"
        # ...and then goes silent mid-session, holding the serial server.

        with socket.create_connection(("127.0.0.1", server.port), timeout=3) as sock:
            buf = bytearray()
            send_line(sock, "AUTH tok")
            assert receive_line(sock, buf) == "OK"
            send_line(sock, "REF main")
            assert receive_line(sock, buf) == "REF main -"
            send_line(sock, "DONE")
    finally:
        stalled.close()
        server.close()
        thread.join(timeout=2)
    assert not thread.is_alive()


def test_cli_push_rejection_shows_reason(push_pair, monkeypatch, capsys):
    pair = push_pair
    _commit(pair.remote_store, pair.remote_commits, {"other.txt": b"x"}, [])
    _commit(pair.local_store, pair.local_commits, {"a.txt": b"1"}, [])
    monkeypatch.chdir(pair.client.repo_path)

    assert main(["push", pair.address, "main", "--token", "tok"]) == 1
    err = capsys.readouterr().err
    assert "push rejected" in err
    assert "pull first" in err
    assert "tok" not in err


# --- fetch / pull / multi-ref (#41) ---


def _commit_on(
    commits: CommitManager, branch: str, files: dict, parents: list, message: str
) -> str:
    """Write a commit with fixed metadata straight onto `branch`, without touching HEAD."""

    tree = _write_tree(commits.store, files)
    lines = [f"tree {tree}", *(f"parent {p}" for p in parents)]
    lines += ["author tester <t@example.com> 0", "committer tester <t@example.com> 0", "", message]
    commit_hash = commits.store.write_object("\n".join(lines).encode(), "commit")
    commits.write_ref(branch, commit_hash)
    return commit_hash


class RecordingCommitManager(CommitManager):
    """Run the real merge engine and record whether fetched history was complete."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.calls: list[tuple[str, str, bool]] = []

    def integrate_fetched_tip(self, branch: str, fetched_hash: str) -> str | None:
        self._validate_fetched_history(fetched_hash)
        self.calls.append((branch, fetched_hash, True))
        return super().integrate_fetched_tip(branch, fetched_hash)


@dataclass
class PullPair:
    client: RemoteClient
    local: RecordingCommitManager
    remote: CommitManager
    address: str
    local_root: Path


@pytest.fixture
def pull_pair(tmp_path, push_pair):
    """Use the real commit manager in a fresh local repository."""

    root = tmp_path / "puller"
    root.mkdir()
    store = ObjectStore(str(root))
    local = RecordingCommitManager(str(root), store=store)
    client = RemoteClient(repo_path=str(root), store=store, commits=local)
    return PullPair(client, local, push_pair.remote_commits, push_pair.address, root)


def _count_receives(monkeypatch) -> list:
    """Record every object payload `RemoteClient` receives, while still receiving it."""

    calls = []
    original = RemoteClient._receive_object

    def counting(self, sock, buf, expected_hash, expected_type=None):
        calls.append(expected_hash)
        return original(self, sock, buf, expected_hash, expected_type)

    monkeypatch.setattr(RemoteClient, "_receive_object", counting)
    return calls


def _scripted_client(monkeypatch, tmp_path, script: bytes) -> RemoteClient:
    """A real client whose socket replays `script` as the server's side of the session."""

    class ScriptedSocket(FakeReceiveSocket):
        def sendall(self, data):
            pass

        def close(self):
            pass

    sock = ScriptedSocket(script)
    monkeypatch.setattr(socket, "create_connection", lambda *args, **kwargs: sock)
    return RemoteClient(tmp_path)


def _merge_history(remote: CommitManager) -> tuple[str, str, str]:
    """base <- left <- merge(left, base): a merge whose second parent is the common ancestor."""

    files = {"a.txt": b"base\n", "bin/data.bin": bytes(range(256)), "d/e/f.txt": b"deep\n"}
    base = _commit_on(remote, "main", files, [], "base")
    left = _commit_on(remote, "main", {**files, "a.txt": b"left\n"}, [base], "left")
    merge = _commit_on(remote, "main", {**files, "a.txt": b"merged\n"}, [left, base], "merge")
    return base, left, merge


def test_fetch_downloads_full_history_without_touching_refs(push_pair):
    pair = push_pair
    base, left, merge = _merge_history(pair.remote_commits)

    assert pair.client.fetch(pair.address, "main", "tok") == merge

    remote_client = RemoteClient(
        pair.server.repo_path, store=pair.remote_store, commits=pair.remote_commits
    )
    expected = remote_client._collect_from(merge)
    for obj_hash in expected:
        assert pair.local_store.read_object(obj_hash) == pair.remote_store.read_object(obj_hash)
    assert set(pair.local_commits.walk_history(merge)) == {base, left, merge}
    assert pair.local_commits.read_ref("main") is None
    assert not (Path(pair.client.repo_path) / ".minigit" / "HEAD").exists()


def test_repeat_fetch_receives_no_objects(push_pair, monkeypatch):
    pair = push_pair
    _merge_history(pair.remote_commits)
    pair.client.fetch(pair.address, "main", "tok")

    received = _count_receives(monkeypatch)
    pair.client.fetch(pair.address, "main", "tok")
    assert received == []


def test_fetch_requests_missing_tree_under_present_commit(push_pair, monkeypatch):
    """An interrupted fetch can leave a commit stored without its tree."""

    pair = push_pair
    tip = _commit_on(pair.remote_commits, "main", {"a.txt": b"1"}, [], "c")
    obj_type, content = pair.remote_store.read_object(tip)
    pair.local_store.write_object(content, obj_type)  # commit present, tree + blob missing

    received = _count_receives(monkeypatch)
    pair.client.fetch(pair.address, "main", "tok")
    tree = pair.remote_commits.read_commit(tip).tree
    assert tip not in received
    assert tree in received
    assert len(received) == 2


def test_fetch_shared_objects_across_branches_transfer_once(push_pair, monkeypatch):
    pair = push_pair
    shared = {"shared.bin": b"\x00" * 1000, "lib/util.txt": b"util\n"}
    _commit_on(pair.remote_commits, "main", {**shared, "m.txt": b"m"}, [], "m")
    _commit_on(pair.remote_commits, "feature", {**shared, "f.txt": b"f"}, [], "f")

    received = _count_receives(monkeypatch)
    pair.client.fetch(pair.address, "main", "tok")
    pair.client.fetch(pair.address, "feature", "tok")
    assert len(received) == len(set(received))
    assert pair.local_store.hash_object(b"\x00" * 1000, "blob") in received


def test_fetch_missing_branch_raises(push_pair):
    with pytest.raises(NetworkProtocolError, match="no branch 'nope'"):
        push_pair.client.fetch(push_pair.address, "nope", "tok")


@pytest.mark.parametrize("branch", ["../escape", "a b", "", "x/../y"])
def test_fetch_invalid_branch_name_raises(push_pair, branch):
    with pytest.raises(NetworkProtocolError, match="invalid branch"):
        push_pair.client.fetch(push_pair.address, branch, "tok")


def test_bad_token_is_rejected_and_never_echoed(push_pair):
    _commit_on(push_pair.remote_commits, "main", {"a.txt": b"1"}, [], "c")
    for call in (
        lambda: push_pair.client.fetch(push_pair.address, "main", "s3cret-xyz"),
        lambda: push_pair.client.list_refs(push_pair.address, "s3cret-xyz"),
    ):
        with pytest.raises(NetworkProtocolError, match="authentication failed") as info:
            call()
        assert "s3cret" not in str(info.value)


def test_command_before_auth_is_refused_and_server_survives(push_pair):
    pair = push_pair
    for first_line in ["REF main", "REFS", f"WANT {'a' * 40}"]:
        with socket.create_connection(("127.0.0.1", pair.server.port), timeout=2) as sock:
            buf = bytearray()
            send_line(sock, first_line)
            assert receive_line(sock, buf) == "ERR bad auth"
    assert pair.client.list_refs(pair.address, "tok") == {}


def _blob_hash(data: bytes) -> str:
    return hashlib.sha1(f"blob {len(data)}".encode() + b"\0" + data).hexdigest()


@pytest.mark.parametrize(
    "reply, message",
    [
        (b"OBJ blob 1\nx", "is a blob, expected a commit"),
        (b"OBJ commit 3\nabc", "failed hash verification"),
        (b"OBJ commit 50\nabc", "connection closed"),
        (b"ERR unknown object\n", "could not provide"),
        (b"garbage\n", "expected OBJ"),
    ],
)
def test_fetch_bad_object_replies_raise(monkeypatch, tmp_path, reply, message):
    tip = _blob_hash(b"x")  # a valid hash the scripted server lies about
    script = b"OK\nREF main " + tip.encode() + b"\n" + reply
    client = _scripted_client(monkeypatch, tmp_path, script)
    with pytest.raises(NetworkProtocolError, match=message):
        client.fetch("localhost:9418", "main", "tok")
    assert not client.store.has_object(tip)


@pytest.mark.parametrize(
    "script",
    [
        b"OK\nREF main zzz\nEND\n",
        b"OK\nREF ../x " + b"a" * 40 + b"\nEND\n",
        b"OK\nREF main " + b"a" * 40 + b"\nREF main " + b"b" * 40 + b"\nEND\n",
        b"OK\nERR expected REF, WANT, PUSH, or DONE\n",
        b"OK\nREF main " + b"a" * 40 + b"\n",  # no END before the connection closes
    ],
)
def test_list_refs_malformed_replies_raise(monkeypatch, tmp_path, script):
    client = _scripted_client(monkeypatch, tmp_path, script)
    with pytest.raises(NetworkProtocolError):
        client.list_refs("localhost:9418", "tok")


def test_list_refs_lists_every_branch_and_skips_non_refs(push_pair):
    pair = push_pair
    assert pair.client.list_refs(pair.address, "tok") == {}
    main_tip = _commit_on(pair.remote_commits, "main", {"a.txt": b"1"}, [], "m")
    feature_tip = _commit_on(pair.remote_commits, "feature", {"b.txt": b"2"}, [], "f")
    (Path(pair.server.repo_path) / ".minigit" / "refs" / "heads" / "nested").mkdir()

    assert pair.client.list_refs(pair.address, "tok") == {"main": main_tip, "feature": feature_tip}


def test_interrupted_fetch_keeps_state_and_retry_completes(push_pair, monkeypatch):
    pair = push_pair
    _, _, merge = _merge_history(pair.remote_commits)
    total = len(
        RemoteClient(
            pair.server.repo_path, store=pair.remote_store, commits=pair.remote_commits
        )._collect_from(merge)
    )

    original = RemoteClient._receive_object
    calls = []

    def flaky(self, sock, buf, expected_hash, expected_type=None):
        if len(calls) == 3:
            raise NetworkProtocolError("connection dropped")
        calls.append(expected_hash)
        return original(self, sock, buf, expected_hash, expected_type)

    monkeypatch.setattr(RemoteClient, "_receive_object", flaky)
    with pytest.raises(NetworkProtocolError):
        pair.client.fetch(pair.address, "main", "tok")
    assert pair.local_commits.read_ref("main") is None
    minigit_dir = Path(pair.client.repo_path) / ".minigit"
    assert not (minigit_dir / "HEAD").exists() and not (minigit_dir / "index").exists()

    monkeypatch.setattr(RemoteClient, "_receive_object", original)
    received = _count_receives(monkeypatch)
    assert pair.client.fetch(pair.address, "main", "tok") == merge
    assert len(received) == total - 3


def test_pull_unborn_then_fast_forward_checks_out_files(pull_pair, capsys):
    pair = pull_pair
    base = _commit_on(pair.remote, "main", {"a.txt": b"one\n"}, [], "base")
    pair.client.pull(pair.address, "main", "tok")
    assert pair.local.read_ref("main") == base
    assert (pair.local_root / "a.txt").read_bytes() == b"one\n"

    ahead = _commit_on(pair.remote, "main", {"a.txt": b"two\n"}, [base], "ahead")
    capsys.readouterr()
    pair.client.pull(pair.address, "main", "tok")
    assert pair.local.read_ref("main") == ahead
    assert (pair.local_root / "a.txt").read_bytes() == b"two\n"
    assert f"main now at {ahead}" in capsys.readouterr().out
    assert pair.local.calls == [("main", base, True), ("main", ahead, True)]


def test_pull_up_to_date_and_local_ahead_change_nothing(pull_pair, capsys, monkeypatch):
    pair = pull_pair
    base = _commit_on(pair.remote, "main", {"a.txt": b"one\n"}, [], "base")
    pair.client.pull(pair.address, "main", "tok")

    received = _count_receives(monkeypatch)
    capsys.readouterr()
    pair.client.pull(pair.address, "main", "tok")
    assert "main is up to date" in capsys.readouterr().out
    assert received == []

    local_tip = _commit_on(pair.local, "main", {"a.txt": b"local\n"}, [base], "local")
    pair.client.pull(pair.address, "main", "tok")
    assert pair.local.read_ref("main") == local_tip
    assert "main is up to date" in capsys.readouterr().out


def test_pull_diverged_reports_merge_commit(pull_pair, capsys):
    pair = pull_pair
    base = _commit_on(pair.remote, "main", {"a.txt": b"base\n"}, [], "base")
    pair.client.pull(pair.address, "main", "tok")
    local_tip = _commit_on(pair.local, "main", {"a.txt": b"base\n", "l.txt": b"l"}, [base], "l")
    remote_tip = _commit_on(pair.remote, "main", {"a.txt": b"base\n", "r.txt": b"r"}, [base], "r")
    pair.local.tree.checkout(pair.local.read_commit(local_tip).tree)

    capsys.readouterr()
    pair.client.pull(pair.address, "main", "tok")
    merge = pair.local.read_ref("main")
    assert pair.local.read_commit(merge).parents == [local_tip, remote_tip]
    assert f"main: merged {remote_tip[:7]} as {merge[:7]}" in capsys.readouterr().out


def test_pull_conflict_propagates_paths_and_keeps_ref(pull_pair):
    pair = pull_pair
    base = _commit_on(pair.remote, "main", {"a.txt": b"base\n"}, [], "base")
    pair.client.pull(pair.address, "main", "tok")
    local_tip = _commit_on(pair.local, "main", {"a.txt": b"ours\n"}, [base], "ours")
    _commit_on(pair.remote, "main", {"a.txt": b"theirs\n"}, [base], "theirs")
    pair.local.tree.checkout(pair.local.read_commit(local_tip).tree)

    with pytest.raises(MergeConflictError) as info:
        pair.client.pull(pair.address, "main", "tok")
    assert info.value.paths == ["a.txt"]
    assert pair.local.read_ref("main") == local_tip


def test_pull_never_integrates_after_failed_fetch(pull_pair):
    pair = pull_pair
    tip = _commit_on(pair.remote, "main", {"a.txt": b"1"}, [], "c")
    blob = pair.remote.store.hash_object(b"1", "blob")
    pair.remote.store._object_path(blob).write_bytes(b"corrupt")

    with pytest.raises(NetworkProtocolError, match="corrupt object"):
        pair.client.pull(pair.address, "main", "tok")
    assert pair.local.calls == []
    assert pair.local.read_ref("main") is None
    assert pair.local.store.has_object(tip)  # complete objects kept for a retry


def test_pull_creates_and_fast_forwards_inactive_branch(push_pair):
    pair = push_pair
    f1 = _commit_on(pair.remote_commits, "feature", {"f.txt": b"1"}, [], "f1")
    pair.client.pull(pair.address, "feature", "tok")
    assert pair.local_commits.read_ref("feature") == f1

    f2 = _commit_on(pair.remote_commits, "feature", {"f.txt": b"2"}, [f1], "f2")
    pair.client.pull(pair.address, "feature", "tok")
    assert pair.local_commits.read_ref("feature") == f2
    assert not (Path(pair.client.repo_path) / "f.txt").exists()  # no checkout


def test_pull_rejects_diverged_inactive_branch(push_pair):
    pair = push_pair
    base = _commit_on(pair.remote_commits, "feature", {"f.txt": b"base"}, [], "base")
    pair.client.pull(pair.address, "feature", "tok")
    local_tip = _commit_on(pair.local_commits, "feature", {"f.txt": b"ours"}, [base], "ours")
    _commit_on(pair.remote_commits, "feature", {"f.txt": b"theirs"}, [base], "theirs")

    with pytest.raises(MiniGitError, match="check out that branch and pull again"):
        pair.client.pull(pair.address, "feature", "tok")
    assert pair.local_commits.read_ref("feature") == local_tip


def test_pull_into_current_branch_uses_real_integration(push_pair):
    pair = push_pair
    tip = _commit_on(pair.remote_commits, "main", {"a.txt": b"1"}, [], "c")
    pair.client.pull(pair.address, "main", "tok")
    assert pair.local_commits.read_ref("main") == tip
    assert (Path(pair.client.repo_path) / "a.txt").read_bytes() == b"1"


def test_cli_ls_remote_and_push_all(push_pair, monkeypatch, capsys):
    pair = push_pair
    main_tip = _commit_on(pair.local_commits, "main", {"a.txt": b"1"}, [], "m")
    feature_tip = _commit_on(pair.local_commits, "feature", {"b.txt": b"2"}, [main_tip], "f")
    monkeypatch.chdir(pair.client.repo_path)

    assert main(["push", pair.address, "--all", "--token", "tok"]) == 0
    assert pair.remote_commits.read_ref("main") == main_tip
    assert pair.remote_commits.read_ref("feature") == feature_tip

    capsys.readouterr()
    assert main(["ls-remote", pair.address, "--token", "tok"]) == 0
    assert capsys.readouterr().out == f"{feature_tip}\tfeature\n{main_tip}\tmain\n"


def test_cli_pull_all_reports_each_branch(push_pair, monkeypatch, capsys):
    pair = push_pair
    _commit_on(pair.remote_commits, "main", {"a.txt": b"1"}, [], "m")
    feature_tip = _commit_on(pair.remote_commits, "feature", {"b.txt": b"2"}, [], "f")
    monkeypatch.chdir(pair.client.repo_path)

    assert main(["pull", pair.address, "--all", "--token", "tok"]) == 0
    out, err = capsys.readouterr()
    assert pair.local_commits.read_ref("feature") == feature_tip
    assert f"feature now at {feature_tip}" in out
    assert pair.local_commits.read_ref("main") == pair.remote_commits.read_ref("main")
    assert (Path(pair.client.repo_path) / "a.txt").read_bytes() == b"1"
    assert err == ""


@pytest.mark.parametrize("args", [["push", "--all", "main"], ["push"], ["pull"]])
def test_cli_branch_or_all_required(push_pair, monkeypatch, capsys, args):
    monkeypatch.chdir(push_pair.client.repo_path)
    command, *rest = args
    assert main([command, push_pair.address, *rest, "--token", "tok"]) == 1
    assert "either a branch name or --all" in capsys.readouterr().err
