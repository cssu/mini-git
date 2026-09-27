"""Object tests: Tests for module 1 - object storage."""

import zlib
from pathlib import Path

import pytest

from minigit.cli import main
from minigit.errors import ObjectCorruptError, ObjectNotFoundError
from minigit.objects import ObjectStore, TreeEntry


def test_round_trip(tmp_path: Path) -> None:
    """
    Test that we can write an object and then read it back.
    """

    store = ObjectStore(tmp_path)
    obj_hash = store.write_object(b"hi", "blob")
    assert store.read_object(obj_hash) == ("blob", b"hi")


@pytest.mark.parametrize(
    ("data", "obj_type"),
    [
        (b"", "blob"),
        (b"\x00\xff", "blob"),
        ("café".encode(), "blob"),
        (b"", "tree"),
        (b"message", "commit"),
    ],
)
def test_round_trip_with_fresh_store(tmp_path: Path, data: bytes, obj_type: str) -> None:
    """Round-trip empty, binary, Unicode, tree, and commit data via a new store."""
    first_store = ObjectStore(tmp_path)
    obj_hash = first_store.write_object(data, obj_type)

    second_store = ObjectStore(tmp_path)

    assert second_store.read_object(obj_hash) == (obj_type, data)


@pytest.mark.parametrize("invalid_hash", ["", "a" * 39, "A" * 40, "g" * 40, "a" * 41])
def test_invalid_hash_is_not_found(tmp_path: Path, invalid_hash: str) -> None:
    """Reject hashes that are not exactly 40 lowercase hexadecimal characters."""
    with pytest.raises(ObjectNotFoundError):
        ObjectStore(tmp_path).read_object(invalid_hash)


@pytest.mark.parametrize(
    "raw_object",
    [
        b"unknown 0\0",
        b"blob nope\0",
        b"blob 2\0x",
        b"blob 1\0xy",
        b"blob 1",
    ],
)
def test_malformed_object_is_corrupt(tmp_path: Path, raw_object: bytes) -> None:
    """Reject malformed headers, unknown types, and incorrect byte lengths."""
    store = ObjectStore(tmp_path)
    obj_hash = "a" * 40
    object_path = store._object_path(obj_hash)
    object_path.parent.mkdir(parents=True)
    object_path.write_bytes(zlib.compress(raw_object))

    with pytest.raises(ObjectCorruptError):
        store.read_object(obj_hash)


def test_trailing_compressed_data_is_corrupt(tmp_path: Path) -> None:
    """Reject compressed streams with trailing data after the object stream."""
    store = ObjectStore(tmp_path)
    obj_hash = store.hash_object(b"x", "blob")
    object_path = store._object_path(obj_hash)
    object_path.parent.mkdir(parents=True)
    object_path.write_bytes(zlib.compress(b"blob 1\0x") + b"trailing")

    with pytest.raises(ObjectCorruptError):
        store.read_object(obj_hash)


def test_duplicate_corrupt_object_raises(tmp_path: Path) -> None:
    """Validate an existing duplicate and raise when its bytes are corrupt."""
    store = ObjectStore(tmp_path)
    obj_hash = store.write_object(b"hi", "blob")
    store._object_path(obj_hash).write_bytes(b"corrupt")

    with pytest.raises(ObjectCorruptError):
        store.write_object(b"hi", "blob")


def test_failed_write_cleans_up_and_can_retry(tmp_path: Path, monkeypatch) -> None:
    """Remove a failed temporary write and allow the object to be retried."""
    store = ObjectStore(tmp_path)

    def fail_replace(source, destination):
        raise OSError("simulated failure")

    monkeypatch.setattr("minigit.objects.os.replace", fail_replace)
    with pytest.raises(OSError):
        store.write_object(b"retry", "blob")

    obj_hash = store.hash_object(b"retry", "blob")
    assert not store._object_path(obj_hash).exists()
    assert list(store.objects_dir.rglob("*")) == [store._object_path(obj_hash).parent]

    monkeypatch.undo()
    assert store.write_object(b"retry", "blob") == obj_hash


def test_has_object_distinguishes_missing_valid_and_corrupt(tmp_path: Path) -> None:
    """Return false only for missing objects and propagate corruption errors."""
    store = ObjectStore(tmp_path)
    obj_hash = store.write_object(b"valid", "blob")

    assert store.has_object(obj_hash)
    assert not store.has_object("a" * 40)

    store._object_path(obj_hash).write_bytes(b"corrupt")
    with pytest.raises(ObjectCorruptError):
        store.has_object(obj_hash)


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
    """
    Test that writing the same object twice leaves only one object file in the object store.
    """
    store = ObjectStore(tmp_path)

    obj_hash = store.write_object(b"hi", "blob")
    assert store.write_object(b"hi", "blob") == obj_hash

    assert list((tmp_path / ".minigit" / "objects").rglob("*")) == [
        store._object_path(obj_hash).parent,
        store._object_path(obj_hash),
    ]


def test_writing_empty_tree(tmp_path):
    """
    Tests that writing an empty tree returns a valid hash and can be read back.
    """
    store = ObjectStore(tmp_path)

    tree_hash = store.write_tree([])

    assert store.read_object(tree_hash) == ("tree", b"")
    assert store.read_tree(tree_hash) == []


def test_tree_round_trip_supports_nested_references_executable_and_spaces(tmp_path):
    """
    Tests that writing a tree with nested references,
    executable files, and spaces in names can be read back correctly
    """
    store = ObjectStore(tmp_path)
    blob_hash = "a" * 40
    nested_hash = store.write_tree([TreeEntry("100644", "blob", blob_hash, "child")])
    entries = [
        TreeEntry("100755", "blob", blob_hash, "run script"),
        TreeEntry("40000", "tree", nested_hash, "nested"),
    ]

    tree_hash = store.write_tree(entries)

    assert store.read_tree(tree_hash) == [entries[1], entries[0]]


def test_tree_hash_is_deterministic_across_input_order(tmp_path):
    """
    Tests that writing a tree with the same entries in different orders produces the same hash.
    """
    store = ObjectStore(tmp_path)
    entries = [
        TreeEntry("100644", "blob", "a" * 40, "z.txt"),
        TreeEntry("100644", "blob", "b" * 40, "a.txt"),
    ]

    assert store.write_tree(entries) == store.write_tree(list(reversed(entries)))


@pytest.mark.parametrize(
    "entry",
    [
        TreeEntry("100600", "blob", "a" * 40, "file"),
        TreeEntry("100644", "commit", "a" * 40, "file"),
        TreeEntry("100644", "blob", "a" * 39, "file"),
        TreeEntry("100644", "blob", "g" * 40, "file"),
        TreeEntry("100644", "blob", "a" * 40, ""),
        TreeEntry("100644", "blob", "a" * 40, "dir/file"),
        TreeEntry("100644", "blob", "a" * 40, "."),
        TreeEntry("100644", "blob", "a" * 40, ".."),
        TreeEntry("100644", "blob", "a" * 40, "has\t tab"),
        TreeEntry("100644", "blob", "a" * 40, "has\nnewline"),
    ],
)
def test_write_tree_rejects_malformed_entries(tmp_path, entry):
    """
    Tests that writing a tree with incorrectly formatted entries raises a ValueError.
    """
    with pytest.raises(ValueError):
        ObjectStore(tmp_path).write_tree([entry])


@pytest.mark.parametrize(
    "tree_data",
    [
        b"100644 blob aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa no-tab\n",
        b"100644 blob aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\t\n",
        b"100644 blob aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\tdup\n"
        b"100644 blob bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb\tdup\n",
        b"100644 blob aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\tfile\n",
        b"100644 blob aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\tfile",
        b"100644 blob aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\tfile\r\n",
        b"\xff",
    ],
)
def test_read_tree_rejects_malformed_data(tmp_path, tree_data):
    """
    Tests that reading a tree with incorrectly formatted data raises an ObjectCorruptError
    """
    store = ObjectStore(tmp_path)
    tree_hash = store.write_object(tree_data, "tree")

    with pytest.raises(ObjectCorruptError):
        store.read_tree(tree_hash)


def test_read_tree_rejects_non_tree_object(tmp_path):
    """
    Tests that reading a tree from a non-tree object raises an ObjectCorruptError
    """
    store = ObjectStore(tmp_path)
    blob_hash = store.write_object(b"content", "blob")

    with pytest.raises(ObjectCorruptError):
        store.read_tree(blob_hash)


def test_read_tree_missing_object(tmp_path):
    """
    Tests that reading a non-existent tree raises an ObjectNotFoundError.
    """
    with pytest.raises(ObjectNotFoundError):
        ObjectStore(tmp_path).read_tree("a" * 40)


def test_tree_round_trip_with_new_store(tmp_path):
    """
    Tests that writing a tree and reading it back with a new ObjectStore instance works correctly.
    """
    entries = [TreeEntry("100644", "blob", "a" * 40, "file")]
    first_store = ObjectStore(tmp_path)
    tree_hash = first_store.write_tree(entries)

    second_store = ObjectStore(tmp_path)

    assert second_store.read_tree(tree_hash) == entries


@pytest.mark.parametrize("name", ["vertical\vtab", "form\ffeed", "unicode\u2028separator"])
def test_tree_round_trip_preserves_non_delimiter_characters(tmp_path, name):
    store = ObjectStore(tmp_path)
    entries = [TreeEntry("100644", "blob", "a" * 40, name)]
    assert store.read_tree(store.write_tree(entries)) == entries


def test_tree_rejects_null_in_filename(tmp_path):
    with pytest.raises(ValueError):
        ObjectStore(tmp_path).write_tree([TreeEntry("100644", "blob", "a" * 40, "bad\0name")])
