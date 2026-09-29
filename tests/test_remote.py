import hashlib
import socket
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

import pytest

from minigit.commits import CommitManager
from minigit.errors import NetworkProtocolError
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
    _commit(pair.local_store, pair.local_commits, {"a.txt": b"1"}, [])

    with pytest.raises(NetworkProtocolError):
        pair.client.push(pair.address, "main", "tok")
    assert pair.remote_commits.read_ref("main") == unrelated


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
