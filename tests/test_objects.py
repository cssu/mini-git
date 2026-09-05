"""Object tests: Tests for module 1 - object storage."""

from pathlib import Path

import pytest

from minigit.cli import main
from minigit.errors import ObjectCorruptError, ObjectNotFoundError
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


def test_hash_correctness(tmp_path):
    """
    Test that the hash of a known object is correct.
    """

    store = ObjectStore(tmp_path)

    assert store.hash_object(b"hi", "blob") == "32f95c0d1244a78b2be1bab8de17906fabb2c4a8"
    assert store.hash_object(b"", "blob") == "e69de29bb2d1d6434b8b29ae775ad8c2e48c5391"


def test_object_compressed(tmp_path):
    """
    Test that the compressed object does not equal the content.
    """

    store = ObjectStore(tmp_path)
    obj_hash = store.write_object(b"hi", "blob")
    object_path = store._object_path(obj_hash)

    compressed_data = object_path.read_bytes()
    assert compressed_data != b"hi"


def test_file_overwrite(tmp_path):
    """
    Test that overwriting an existing object raises ObjectCorruptError.
    """

    store = ObjectStore(tmp_path)
    obj_hash = store.write_object(b"hi", "blob")

    store._object_path(obj_hash).write_bytes(b"junk")

    with pytest.raises(ObjectCorruptError):
        store.read_object(obj_hash)


def test_duplicate_write_leaves_one_object_file(tmp_path):
    store = ObjectStore(tmp_path)

    obj_hash = store.write_object(b"hi", "blob")
    assert store.write_object(b"hi", "blob") == obj_hash

    assert list((tmp_path / ".minigit" / "objects").rglob("*")) == [
        store._object_path(obj_hash).parent,
        store._object_path(obj_hash),
    ]
