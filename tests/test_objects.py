"""Object tests: Tests for module 1 - object storage."""

import os
import zlib
from pathlib import Path

import pytest

from minigit.cli import main
from minigit.commits import CommitManager
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


def test_corrupt_loose_object_wins_over_valid_packed_copy_with_fresh_store(
    tmp_path: Path,
) -> None:
    """A corrupt loose object must not fall back to a valid packed copy."""
    writer = ObjectStore(tmp_path)
    obj_hash = writer.write_object(b"content", "blob")
    writer.pack_objects([obj_hash])
    writer._object_path(obj_hash).write_bytes(b"corrupt")

    reader = ObjectStore(tmp_path)
    with pytest.raises(ObjectCorruptError):
        reader.read_object(obj_hash)
    with pytest.raises(ObjectCorruptError):
        reader.has_object(obj_hash)
    with pytest.raises(ObjectCorruptError):
        reader.write_object(b"content", "blob")


@pytest.mark.parametrize(
    ("storage", "expected"),
    [
        ("loose", ("blob", b"loose")),
        ("packed", ("blob", b"packed")),
        ("mixed", ("blob", b"loose")),
    ],
)
def test_fresh_store_lookup_modes_preserve_missing_and_corrupt_distinctions(
    tmp_path: Path, storage: str, expected: tuple[str, bytes]
) -> None:
    """Fresh stores distinguish valid, missing, and corrupt objects in each mode."""
    writer = ObjectStore(tmp_path)
    data = b"loose" if storage == "mixed" else storage.encode()
    obj_hash = writer.write_object(data, "blob")
    if storage in {"packed", "mixed"}:
        writer.pack_objects([obj_hash])
    if storage == "packed":
        writer._object_path(obj_hash).unlink()

    reader = ObjectStore(tmp_path)
    assert reader.read_object(obj_hash) == expected
    assert reader.has_object(obj_hash)
    assert reader.write_object(data, "blob") == obj_hash
    if storage == "packed":
        assert not reader._object_path(obj_hash).exists()
    with pytest.raises(ObjectNotFoundError):
        reader.read_object("a" * 40)
    assert not reader.has_object("a" * 40)

    reader._object_path(obj_hash).write_bytes(b"corrupt")
    with pytest.raises(ObjectCorruptError):
        reader.read_object(obj_hash)


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
    """Tests that writing a tree with non-delimiter characters
    in names can be read back correctly."""
    store = ObjectStore(tmp_path)
    entries = [TreeEntry("100644", "blob", "a" * 40, name)]
    assert store.read_tree(store.write_tree(entries)) == entries


def test_tree_rejects_null_in_filename(tmp_path):
    """Tests that writing a tree with a null character in a filename raises a ValueError."""
    with pytest.raises(ValueError):
        ObjectStore(tmp_path).write_tree([TreeEntry("100644", "blob", "a" * 40, "bad\0name")])


def test_pack_objects_reads_packed_only_and_deduplicates(tmp_path: Path) -> None:
    """Tests that packing objects reads through the public API,
    deduplicates, and preserves exact bytes."""
    store = ObjectStore(tmp_path)
    objects = [
        (b"", "blob"),
        (b"\x00binary\xff", "blob"),
        ("café".encode(), "tree"),
        (b"tree deadbeef", "commit"),
    ]
    hashes = [store.write_object(data, obj_type) for data, obj_type in objects]

    pack_path, index_path = store.pack_objects([hashes[0], hashes[1], hashes[0], *hashes[2:]])
    assert pack_path.exists()
    assert index_path.exists()
    for obj_hash, (data, obj_type) in zip(hashes, objects, strict=True):
        store._object_path(obj_hash).unlink()
        assert ObjectStore(tmp_path).read_object(obj_hash) == (obj_type, data)


def test_pack_objects_mixes_loose_and_packed_reads(tmp_path: Path) -> None:
    """Tests that packing objects mixes loose and packed reads correctly."""
    store = ObjectStore(tmp_path)
    packed_hash = store.write_object(b"packed", "blob")
    loose_hash = store.write_object(b"loose", "blob")
    store.pack_objects([packed_hash])
    store._object_path(packed_hash).unlink()

    assert store.read_object(packed_hash) == ("blob", b"packed")
    assert store.read_object(loose_hash) == ("blob", b"loose")
    assert store.has_object(packed_hash)


def test_fresh_store_reads_multiple_packs_and_object_types(tmp_path: Path) -> None:
    """Read nested trees and a merge commit from overlapping packed and loose storage."""
    writer = ObjectStore(tmp_path)
    commits = CommitManager(tmp_path, store=writer)
    objects = [
        (b"", "blob"),
        (b"\x00binary\xff", "blob"),
        ("café".encode(), "blob"),
    ]
    hashes = [writer.write_object(data, obj_type) for data, obj_type in objects]
    nested_hash = writer.write_tree([TreeEntry("100644", "blob", hashes[1], "binary")])
    root_hash = writer.write_tree(
        [
            TreeEntry("100644", "blob", hashes[0], "empty"),
            TreeEntry("40000", "tree", nested_hash, "nested"),
            TreeEntry("100644", "blob", hashes[2], "unicode"),
        ]
    )
    first_commit = commits.create_commit(root_hash, [], "tester <test@example.com>", "first")
    side_commit = commits.create_commit(root_hash, [], "tester <test@example.com>", "side")
    merge_commit = commits.create_commit(
        root_hash,
        [first_commit, side_commit],
        "tester <test@example.com>",
        "merge",
    )

    first_pack_objects = [hashes[0], nested_hash, root_hash, first_commit]
    second_pack_objects = [root_hash, hashes[1], hashes[2], side_commit, merge_commit]
    writer.pack_objects([*first_pack_objects, first_pack_objects[0]])
    writer.pack_objects([*second_pack_objects, root_hash])
    for obj_hash in set(first_pack_objects + second_pack_objects):
        if obj_hash != hashes[2]:
            writer._object_path(obj_hash).unlink()

    reader = ObjectStore(tmp_path)
    reader_commits = CommitManager(tmp_path, store=reader)
    assert reader.read_object(hashes[0]) == ("blob", b"")
    assert reader.read_object(hashes[1]) == ("blob", b"\x00binary\xff")
    assert reader.read_object(hashes[2]) == ("blob", "café".encode())
    assert reader.read_tree(root_hash) == [
        TreeEntry("100644", "blob", hashes[0], "empty"),
        TreeEntry("40000", "tree", nested_hash, "nested"),
        TreeEntry("100644", "blob", hashes[2], "unicode"),
    ]
    assert reader.read_object(merge_commit)[0] == "commit"
    assert reader_commits.read_commit(merge_commit).parents == [first_commit, side_commit]


def test_packed_index_cache_discovers_new_packs(tmp_path: Path) -> None:
    """A cached lookup still discovers a pack published after the first read."""
    store = ObjectStore(tmp_path)
    first_hash = store.write_object(b"first", "blob")
    second_hash = store.write_object(b"second", "blob")
    store.pack_objects([first_hash])
    store._object_path(first_hash).unlink()

    assert store.read_object(first_hash) == ("blob", b"first")

    store.pack_objects([second_hash])
    store._object_path(second_hash).unlink()
    assert store.read_object(second_hash) == ("blob", b"second")


def test_packed_index_cache_rechecks_changed_pack(tmp_path: Path) -> None:
    """Changing a published pack invalidates its cached validation."""
    store = ObjectStore(tmp_path)
    obj_hash = store.write_object(b"content", "blob")
    pack_path, _ = store.pack_objects([obj_hash])
    store._object_path(obj_hash).unlink()

    assert store.read_object(obj_hash) == ("blob", b"content")
    raw = bytearray(pack_path.read_bytes())
    raw[-1] ^= 0xFF
    pack_path.write_bytes(raw)

    with pytest.raises(ObjectCorruptError):
        store.read_object(obj_hash)


def test_duplicate_write_validates_packed_only_object(tmp_path: Path) -> None:
    store = ObjectStore(tmp_path)
    obj_hash = store.write_object(b"packed", "blob")
    store.pack_objects([obj_hash])
    store._object_path(obj_hash).unlink()

    assert store.write_object(b"packed", "blob") == obj_hash
    assert not store._object_path(obj_hash).exists()


def test_corrupt_pack_record_is_reported(tmp_path: Path) -> None:
    """Tests that a corrupt pack record raises ObjectCorruptError when reading the object."""
    store = ObjectStore(tmp_path)
    obj_hash = store.write_object(b"content", "blob")
    pack_path, _ = store.pack_objects([obj_hash])
    store._object_path(obj_hash).unlink()
    raw = bytearray(pack_path.read_bytes())
    raw[-1] ^= 0xFF
    pack_path.write_bytes(raw)

    with pytest.raises(ObjectCorruptError):
        store.read_object(obj_hash)


def test_packed_lookup_validates_every_record_before_returning_any_object(tmp_path: Path) -> None:
    """A corrupt non-selected record invalidates the published pack."""
    store = ObjectStore(tmp_path)
    first = store.write_object(b"first", "blob")
    second = store.write_object(b"second", "blob")
    pack_path, _ = store.pack_objects([first, second])
    store._object_path(first).unlink()
    store._object_path(second).unlink()

    raw = bytearray(pack_path.read_bytes())
    first_record_end = store._PACK_HEADER.size + store._RECORD_HEADER.size
    type_length = raw[store._PACK_HEADER.size]
    compressed_length = int.from_bytes(
        raw[store._PACK_HEADER.size + 9 : store._PACK_HEADER.size + 17], "big"
    )
    second_record = first_record_end + type_length + compressed_length
    raw[second_record + store._RECORD_HEADER.size + 4] ^= 0xFF
    pack_path.write_bytes(raw)

    with pytest.raises(ObjectCorruptError):
        ObjectStore(tmp_path).read_object(first)


@pytest.mark.parametrize("header", ["pack", "index"])
def test_published_pack_and_index_versions_are_validated(tmp_path: Path, header: str) -> None:
    store = ObjectStore(tmp_path)
    obj_hash = store.write_object(b"content", "blob")
    pack_path, index_path = store.pack_objects([obj_hash])
    path = pack_path if header == "pack" else index_path
    raw = bytearray(path.read_bytes())
    version_offset = 4
    raw[version_offset : version_offset + 4] = (store._PACK_VERSION + 1).to_bytes(4, "big")
    path.write_bytes(raw)
    store._object_path(obj_hash).unlink()

    with pytest.raises(ObjectCorruptError):
        ObjectStore(tmp_path).read_object(obj_hash)


def test_published_pack_count_and_index_offset_are_validated(tmp_path: Path) -> None:
    store = ObjectStore(tmp_path)
    obj_hash = store.write_object(b"content", "blob")
    pack_path, index_path = store.pack_objects([obj_hash])
    store._object_path(obj_hash).unlink()

    pack = bytearray(pack_path.read_bytes())
    pack[8:12] = (2).to_bytes(4, "big")
    pack_path.write_bytes(pack)
    with pytest.raises(ObjectCorruptError):
        ObjectStore(tmp_path).read_object(obj_hash)

    offset_root = tmp_path / "offset"
    offset_store = ObjectStore(offset_root)
    obj_hash = offset_store.write_object(b"content", "blob")
    pack_path, index_path = offset_store.pack_objects([obj_hash])
    offset_store._object_path(obj_hash).unlink()
    index = bytearray(index_path.read_bytes())
    offset_start = offset_store._INDEX_HEADER.size + 20
    index[offset_start : offset_start + 8] = (1).to_bytes(8, "big")
    index_path.write_bytes(index)
    with pytest.raises(ObjectCorruptError):
        ObjectStore(offset_root).read_object(obj_hash)


def test_bad_pack_index_is_reported(tmp_path: Path) -> None:
    """Tests that a bad pack index raises ObjectCorruptError when reading the object."""
    store = ObjectStore(tmp_path)
    obj_hash = store.write_object(b"content", "blob")
    _, index_path = store.pack_objects([obj_hash])
    store._object_path(obj_hash).unlink()
    raw = bytearray(index_path.read_bytes())
    raw[-1] = 0
    index_path.write_bytes(raw)

    with pytest.raises(ObjectCorruptError):
        store.read_object(obj_hash)


def test_pack_publication_failure_leaves_previous_objects_readable(
    tmp_path: Path, monkeypatch
) -> None:
    """A failed index publication leaves loose objects and no visible pack."""
    store = ObjectStore(tmp_path)
    obj_hash = store.write_object(b"content", "blob")

    original_replace = os.replace
    calls = 0

    def fail_index_publish(source, destination):
        """Helper to simulate a failure during index publication."""
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("simulated index publication failure")
        original_replace(source, destination)

    monkeypatch.setattr("minigit.objects.os.replace", fail_index_publish)
    with pytest.raises(OSError):
        store.pack_objects([obj_hash])

    assert store.read_object(obj_hash) == ("blob", b"content")
    pack_files = sorted(store.pack_dir.glob("pack-*"))
    assert [path.suffix for path in pack_files] == [".pack"]
    assert all(not path.name.startswith(".pack-") for path in pack_files)
    assert not list(store.pack_dir.glob("*.idx"))
    assert not list(store.pack_dir.glob("*.tmp"))

    store._object_path(obj_hash).unlink()
    with pytest.raises(ObjectNotFoundError):
        ObjectStore(tmp_path).read_object(obj_hash)


def test_pack_publication_failure_cleans_temporary_files(tmp_path: Path, monkeypatch) -> None:
    """A failure before pack publication removes both temporary files."""
    store = ObjectStore(tmp_path)
    obj_hash = store.write_object(b"content", "blob")

    def fail_pack_validation(path):
        raise OSError("simulated validation failure")

    monkeypatch.setattr(store, "_validate_pack", fail_pack_validation)
    with pytest.raises(OSError):
        store.pack_objects([obj_hash])

    assert not list(store.pack_dir.glob("pack-*.pack"))
    assert not list(store.pack_dir.glob("pack-*.idx"))
    assert not list(store.pack_dir.glob("*.tmp"))
    assert store.read_object(obj_hash) == ("blob", b"content")


@pytest.mark.parametrize("obj_type", ["unknown", "blob\ncommit", ""])
def test_write_rejects_unsupported_type_without_creating_objects(tmp_path, obj_type):
    """Tests that writing an object with an unsupported type
    raises a ValueError and does not create any object files.
    """
    store = ObjectStore(tmp_path)
    with pytest.raises(ValueError, match="unsupported object type"):
        store.write_object(b"data", obj_type)
    assert not store.objects_dir.exists()


def test_pack_index_must_cover_every_record(tmp_path):
    store = ObjectStore(tmp_path)
    hashes = [store.write_object(data, "blob") for data in (b"one", b"two")]
    _, index = store.pack_objects(hashes)
    raw = index.read_bytes()
    index.write_bytes(
        store._INDEX_HEADER.pack(store._INDEX_MAGIC, store._PACK_VERSION, 1)
        + raw[store._INDEX_HEADER.size : -store._INDEX_ENTRY.size]
    )
    for obj_hash in hashes:
        store._object_path(obj_hash).unlink()
    with pytest.raises(ObjectCorruptError):
        ObjectStore(tmp_path).read_object(hashes[0])
