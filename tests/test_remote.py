import threading

import pytest

from minigit.errors import NetworkProtocolError
from minigit.remote import RemoteClient, RemoteServer, receive_line

KNOWN_HASH = "a" * 40


class FakeObjectStore:
    pass


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
    client = make_client()
    with pytest.raises(NetworkProtocolError):
        client.push(f"127.0.0.1:{port}", "main", "tok")


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
