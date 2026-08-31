import pytest

from minigit.errors import NetworkProtocolError
from minigit.remote import RemoteClient


class FakeObjectStore:
    pass


class FakeCommitManager:
    pass


def make_client():
    return RemoteClient(store=FakeObjectStore(), commits=FakeCommitManager())


def test_parse_address_valid():
    client = make_client()
    assert client.parse_address("127.0.0.1:9418") == ("127.0.0.1", 9418)


def test_parse_address_no_colon_raises():
    client = make_client()
    with pytest.raises(NetworkProtocolError):
        client.parse_address("localhost")


def test_parse_address_non_numeric_port_raises():
    client = make_client()
    with pytest.raises(NetworkProtocolError):
        client.parse_address("host:abc")


def test_parse_address_port_zero_raises():
    client = make_client()
    with pytest.raises(NetworkProtocolError):
        client.parse_address("host:0")


def test_parse_address_empty_raises():
    client = make_client()
    with pytest.raises(NetworkProtocolError):
        client.parse_address("")


def test_push_empty_token_raises():
    client = make_client()
    with pytest.raises(NetworkProtocolError):
        client.push("127.0.0.1:9418", "main", "")


def test_push_valid_address_and_token_does_not_raise():
    client = make_client()
    client.push("127.0.0.1:9418", "main", "sometoken")
