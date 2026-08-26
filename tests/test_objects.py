"""Object tests: Tests for module 1 - object storage."""

from pathlib import Path

import pytest

from minigit.cli import main
from minigit.errors import ObjectNotFoundError
from minigit.objects import ObjectStore


def test_round_trip(tmp_path: Path) -> None:
    """
    Test that we can write an object and then read it back.
    """

    store = ObjectStore(tmp_path)
    obj_hash = store.write_object(b"hi", "blob")
    assert store.read_object(obj_hash) == ("blob", b"hi")


def test_identical_objects_same_hash(tmp_path: Path):
    """
    Test that writing the same object twice returns the same hash.
    """
    store = ObjectStore(tmp_path)

    hash1 = store.hash_object(b"hi", "blob")
    hash2 = store.hash_object(b"hi", "blob")

    assert hash1 == hash2


def test_different_objects_different_hashes(tmp_path: Path):
    """
    Test that writing different objects returns different hashes.
    """
    store = ObjectStore(tmp_path)

    hash1 = store.hash_object(b"hi", "blob")
    hash2 = store.hash_object(b"hello", "blob")

    assert hash1 != hash2


def test_idempotent_write(tmp_path: Path):
    """
    Test that writing the same object twice returns the same hash and does not raise an error.
    """
    store = ObjectStore(tmp_path)

    hash1 = store.write_object(b"hi", "blob")
    hash2 = store.write_object(b"hi", "blob")

    assert hash1 == hash2
    assert store.read_object(hash1) == ("blob", b"hi")


def test_unknown_hash_raises(tmp_path: Path):
    """
    Test that reading an unknown hash raises ObjectNotFoundError.
    """
    store = ObjectStore(tmp_path)

    with pytest.raises(ObjectNotFoundError):
        store.read_object("does-not-exist")


def test_hash_object_cli_print(tmp_path, capsys):
    """
    Test that the hash-object CLI command prints the correct hash.
    """

    test_file = tmp_path / "test.txt"
    test_file.write_bytes(b"hello minigit")

    assert main(["hash-object", str(test_file)]) == 0

    captured = capsys.readouterr()
    obj_hash = captured.out.strip()

    assert len(obj_hash) == 40  # current placeholder hash has length 40, like SHA-1


def test_cat_file_cli_print(tmp_path, capsys):
    """
    Test that the cat-file CLI command prints the correct object data.
    """

    test_file = tmp_path / "test.txt"
    test_file.write_bytes(b"hello minigit")

    assert main(["hash-object", str(test_file)]) == 0
    obj_hash = capsys.readouterr().out.strip()

    assert main(["cat-file", obj_hash]) == 0
    captured = capsys.readouterr()
    assert captured.out == "hello minigit"
