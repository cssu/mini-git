import hashlib
import socket
import threading
from typing import NamedTuple

import pytest

from minigit.errors import NetworkProtocolError
from minigit.objects import ObjectStore
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


def test_push_correct_token_does_not_raise(remote_server):
    client = make_client()
    client.push(f"127.0.0.1:{remote_server.port}", "main", "tok")


def test_push_wrong_token_raises(remote_server):
    client = make_client()
    with pytest.raises(NetworkProtocolError):
        client.push(f"127.0.0.1:{remote_server.port}", "main", "wrong")


def test_push_existing_branch_prints_matching_hash(remote_server, capsys):
    client = make_client()
    client.push(f"127.0.0.1:{remote_server.port}", "main", "tok")
    assert KNOWN_HASH in capsys.readouterr().out


def test_push_missing_branch_prints_dash(remote_server, capsys):
    client = make_client()
    client.push(f"127.0.0.1:{remote_server.port}", "nope", "tok")
    assert "is at -" in capsys.readouterr().out


def test_push_closed_port_raises_network_protocol_error(remote_server):
    port = remote_server.port
    remote_server.close()
    remote_server._thread.join(timeout=2)  # wait for serve_forever() to actually exit
    client = make_client()
    with pytest.raises(NetworkProtocolError):
        client.push(f"127.0.0.1:{port}", "main", "tok")


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
