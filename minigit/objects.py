"""Module 1 - Object storage.

Owns the on-disk `.minigit/objects/` directory. The only module that reads or
writes object bytes to disk.

Depends on: nothing.
Serves: modules 2, 3, and 4.

Build the `ObjectStore` class here, per the interface contract.
"""

import hashlib
import os
import struct
import tempfile
import uuid
import zlib
from collections.abc import Iterable
from pathlib import Path
from typing import NamedTuple

from minigit.errors import ObjectCorruptError, ObjectNotFoundError


class TreeEntry(NamedTuple):
    mode: str
    type: str
    hash: str
    name: str


class ObjectStore:
    _OBJECT_TYPES = {"blob", "tree", "commit"}
    _PACK_MAGIC = b"MGPK"
    _INDEX_MAGIC = b"MGIX"
    _PACK_VERSION = 1
    _PACK_HEADER = struct.Struct(">4sII")
    _RECORD_HEADER = struct.Struct(">BQQ")
    _INDEX_HEADER = struct.Struct(">4sII")
    _INDEX_ENTRY = struct.Struct(">20sQQ")

    root: Path
    objects_dir: Path
    pack_dir: Path

    def __init__(self, repo_path=".") -> None:
        """
        Initialize the object store.
        """
        self.root = Path(repo_path)
        self.objects_dir = self.root / ".minigit" / "objects"
        self.pack_dir = self.objects_dir / "pack"

    def hash_object(self, data: bytes, obj_type: str) -> str:
        """
        Return the SHA-1 hash of the object, given its data and type.
        """

        return hashlib.sha1(f"{obj_type} {len(data)}".encode() + b"\0" + data).hexdigest()

    def write_object(self, data: bytes, obj_type: str) -> str:
        """
        Write a compressed object atomically and return its SHA-1 hash.

        Existing objects are validated before duplicate writes return. A
        corrupt existing object raises ObjectCorruptError.

        Returns the hash of the object if it was written or already exists.
        Raises ObjectCorruptError if an existing object is corrupt.
        Raises ValueError for unsupported object types.
        """
        if obj_type not in self._OBJECT_TYPES:
            raise ValueError(f"unsupported object type: {obj_type}")
        obj_hash = self.hash_object(data, obj_type)
        object_path = self._object_path(obj_hash)

        try:
            self.read_object(obj_hash)
        except ObjectNotFoundError:
            pass
        else:
            return obj_hash

        header = f"{obj_type} {len(data)}".encode()
        object_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = None
        try:
            with tempfile.NamedTemporaryFile(
                dir=object_path.parent, prefix=f".{object_path.name}.", delete=False
            ) as temporary_file:
                temporary_path = Path(temporary_file.name)
                temporary_file.write(zlib.compress(header + b"\0" + data))
                temporary_file.flush()
                os.fsync(temporary_file.fileno())
            os.replace(temporary_path, object_path)
            temporary_path = None
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
        return obj_hash

    def read_object(self, hash: str) -> tuple[str, bytes]:
        """
        Read and validate an object, returning a tuple of (type, data).

        Hashes must be full lowercase SHA-1 values. Raise
        ObjectNotFoundError for invalid or missing hashes and
        ObjectCorruptError for invalid compressed data, headers, lengths,
        types, or content hashes.
        """
        if len(hash) != 40 or any(character not in "0123456789abcdef" for character in hash):
            raise ObjectNotFoundError(hash)

        object_path = self._object_path(hash)
        if object_path.exists():
            return self._read_loose_object(hash, object_path)

        packed = self._read_packed_object(hash)
        if packed is None:
            raise ObjectNotFoundError(hash)
        return packed

    def pack_objects(self, hashes: Iterable[str]) -> tuple[Path, Path]:
        """
        Publish selected validated objects as one immutable pack/index pair.
        Returns a tuple of (pack_path, index_path).
        Raises ObjectNotFoundError for invalid or missing hashes
        Raises ObjectCorruptError for invalid compressed data, headers, lengths,
        types, or content hashes.

        """
        selected: dict[str, tuple[str, bytes]] = {}
        for obj_hash in hashes:
            if obj_hash not in selected:
                selected[obj_hash] = self.read_object(obj_hash)

        self.pack_dir.mkdir(parents=True, exist_ok=True)
        pack_id = uuid.uuid4().hex
        pack_path = self.pack_dir / f"pack-{pack_id}.pack"
        index_path = self.pack_dir / f"pack-{pack_id}.idx"
        pack_temp: Path | None = None
        index_temp: Path | None = None
        entries: list[tuple[str, int, int]] = []
        try:  # Try to write pack and index files atomically, cleaning up temp files on error
            with tempfile.NamedTemporaryFile(
                dir=self.pack_dir, prefix=f".pack-{pack_id}.", suffix=".tmp", delete=False
            ) as pack_file:
                pack_temp = Path(pack_file.name)
                pack_file.write(
                    self._PACK_HEADER.pack(self._PACK_MAGIC, self._PACK_VERSION, len(selected))
                )
                for obj_hash, (obj_type, data) in selected.items():
                    offset = pack_file.tell()
                    compressed = zlib.compress(data)
                    type_bytes = obj_type.encode("ascii")
                    pack_file.write(
                        self._RECORD_HEADER.pack(len(type_bytes), len(data), len(compressed))
                    )
                    pack_file.write(type_bytes)
                    pack_file.write(compressed)
                    entries.append(
                        (
                            obj_hash,
                            offset,
                            self._RECORD_HEADER.size + len(type_bytes) + len(compressed),
                        )
                    )
                pack_file.flush()
                os.fsync(pack_file.fileno())

            self._validate_pack(pack_temp)
            os.replace(pack_temp, pack_path)
            pack_temp = None

            with tempfile.NamedTemporaryFile(
                dir=self.pack_dir, prefix=f".pack-{pack_id}.", suffix=".tmp", delete=False
            ) as index_file:
                index_temp = Path(index_file.name)
                index_file.write(
                    self._INDEX_HEADER.pack(self._INDEX_MAGIC, self._PACK_VERSION, len(entries))
                )
                for obj_hash, offset, length in sorted(entries):
                    index_file.write(
                        self._INDEX_ENTRY.pack(bytes.fromhex(obj_hash), offset, length)
                    )
                index_file.flush()
                os.fsync(index_file.fileno())
            self._read_index(index_temp, pack_path)
            os.replace(index_temp, index_path)
            index_temp = None
        finally:
            if pack_temp is not None:
                pack_temp.unlink(missing_ok=True)
            if index_temp is not None:
                index_temp.unlink(missing_ok=True)
        return pack_path, index_path

    def _read_loose_object(self, obj_hash: str, object_path: Path) -> tuple[str, bytes]:
        try:
            compressed_object = object_path.read_bytes()
            decompressor = zlib.decompressobj()
            raw_object = decompressor.decompress(compressed_object) + decompressor.flush()
            if not decompressor.eof or decompressor.unused_data or decompressor.unconsumed_tail:
                raise ObjectCorruptError(obj_hash)
            header, separator, content = raw_object.partition(b"\0")
            if not separator:
                raise ObjectCorruptError(obj_hash)

            header_fields = header.split(b" ")
            if len(header_fields) != 2:
                raise ObjectCorruptError(obj_hash)
            obj_type_bytes, obj_length_bytes = header_fields
            obj_type = obj_type_bytes.decode("ascii")
            if obj_type not in self._OBJECT_TYPES or not obj_length_bytes.isdigit():
                raise ObjectCorruptError(obj_hash)
            if int(obj_length_bytes) != len(content):
                raise ObjectCorruptError(obj_hash)

        except (OSError, UnicodeDecodeError, ValueError, zlib.error) as error:
            raise ObjectCorruptError(obj_hash) from error

        if self.hash_object(content, obj_type) != obj_hash:
            raise ObjectCorruptError(obj_hash)

        return obj_type, content

    def _read_packed_object(self, obj_hash: str) -> tuple[str, bytes] | None:
        """
        Helper function to read a packed object from the pack files.
        Returns a tuple of (type, data) if found, or None if not found.
        Raises ObjectCorruptError if the pack file or index file is invalid or
        does not match the object hash.
        """
        if not self.pack_dir.exists():
            return None
        for index_path in sorted(self.pack_dir.glob("pack-*.idx")):
            entries, pack_path = self._read_index(index_path)
            entry = entries.get(obj_hash)
            if entry is None:
                continue
            offset, record_length = entry
            try:
                with pack_path.open("rb") as pack_file:
                    pack_file.seek(offset)
                    record = pack_file.read(record_length)
            except OSError as error:
                raise ObjectCorruptError(obj_hash) from error
            return self._decode_packed_record(obj_hash, record)
        return None

    def _read_index(
        self, index_path: Path, pack_path: Path | None = None
    ) -> tuple[dict[str, tuple[int, int]], Path]:
        """
        Helper function to read an index file and
        return a dictionary of entries and the corresponding pack file path.
        Raises ObjectCorruptError if the index file is invalid or does not match the pack file.
        Raises ValueError if the index file is too short or has invalid headers or entries.
        """
        try:
            raw = index_path.read_bytes()
            header_size = self._INDEX_HEADER.size
            if len(raw) < header_size:
                raise ValueError
            magic, version, count = self._INDEX_HEADER.unpack(raw[:header_size])
            if magic != self._INDEX_MAGIC or version != self._PACK_VERSION:
                raise ValueError
            expected_size = header_size + count * self._INDEX_ENTRY.size
            if len(raw) != expected_size:
                raise ValueError
            entries: dict[str, tuple[int, int]] = {}
            previous = ""
            for position in range(count):
                start = header_size + position * self._INDEX_ENTRY.size
                raw_hash, offset, length = self._INDEX_ENTRY.unpack(
                    raw[start : start + self._INDEX_ENTRY.size]
                )
                obj_hash = raw_hash.hex()
                if obj_hash <= previous or not length:
                    raise ValueError
                entries[obj_hash] = (offset, length)
                previous = obj_hash
            if pack_path is None:
                pack_path = index_path.with_suffix(".pack")
            self._validate_pack(pack_path, entries)
            return entries, pack_path
        except (OSError, ValueError, struct.error) as error:
            raise ObjectCorruptError(index_path.name) from error

    def _validate_pack(
        self, pack_path: Path, entries: dict[str, tuple[int, int]] | None = None
    ) -> None:
        """
        Helper function to validate a pack file against its header and entries.
        Raises ObjectCorruptError if the pack file is invalid
        or does not match the provided entries.
        Raises ValueError if the pack file is too short or has invalid headers or records.
        """
        try:
            raw = pack_path.read_bytes()
            if len(raw) < self._PACK_HEADER.size:
                raise ValueError
            magic, version, count = self._PACK_HEADER.unpack(raw[: self._PACK_HEADER.size])
            if magic != self._PACK_MAGIC or version != self._PACK_VERSION:
                raise ValueError
            position = self._PACK_HEADER.size
            records = []
            for _ in range(count):
                if position + self._RECORD_HEADER.size > len(raw):
                    raise ValueError
                type_length, data_length, compressed_length = self._RECORD_HEADER.unpack(
                    raw[position : position + self._RECORD_HEADER.size]
                )
                record_length = self._RECORD_HEADER.size + type_length + compressed_length
                if (
                    type_length not in {4, 5, 6}
                    or not compressed_length
                    or position + record_length > len(raw)
                ):
                    raise ValueError
                records.append((position, record_length))
                position += record_length
            if position != len(raw):
                raise ValueError
            if entries is not None:
                valid_records = set(records)
                indexed_records = list(entries.values())
                if any(entry not in valid_records for entry in indexed_records) or len(
                    set(indexed_records)
                ) != len(indexed_records):
                    raise ValueError
        except (OSError, struct.error, ValueError) as error:
            raise ObjectCorruptError(pack_path.name) from error

    def _decode_packed_record(self, obj_hash: str, record: bytes) -> tuple[str, bytes]:
        """
        Helper function to decode a packed record and return a tuple of (type, data).
        Raises ObjectCorruptError if the record is invalid or does not match the object hash.
        Raises ValueError if the record is too short or has invalid headers or compressed data.
        """
        try:
            type_length, data_length, compressed_length = self._RECORD_HEADER.unpack(
                record[: self._RECORD_HEADER.size]
            )
            header_end = self._RECORD_HEADER.size + type_length
            if len(record) != header_end + compressed_length:
                raise ValueError
            obj_type = record[self._RECORD_HEADER.size : header_end].decode("ascii")
            if obj_type not in self._OBJECT_TYPES:
                raise ValueError
            decompressor = zlib.decompressobj()
            content = decompressor.decompress(record[header_end:]) + decompressor.flush()
            if (
                not decompressor.eof
                or decompressor.unused_data
                or decompressor.unconsumed_tail
                or len(content) != data_length
                or self.hash_object(content, obj_type) != obj_hash
            ):
                raise ValueError
        except (UnicodeDecodeError, ValueError, zlib.error, struct.error) as error:
            raise ObjectCorruptError(obj_hash) from error
        return obj_type, content

    def _object_path(self, hash: str) -> Path:
        """
        Return the loose-object path for a validated lowercase SHA-1 hash.

        Raise ObjectNotFoundError before constructing a path for invalid
        hashes.
        """
        if len(hash) != 40 or any(character not in "0123456789abcdef" for character in hash):
            raise ObjectNotFoundError(hash)
        return self.objects_dir / hash[:2] / hash[2:]

    def has_object(self, hash: str) -> bool:
        """Return whether a valid object exists, propagating corruption errors."""
        try:
            self.read_object(hash)
        except ObjectNotFoundError:
            return False
        return True

    @staticmethod
    def _validate_tree_entry(entry: TreeEntry) -> None:
        """
        Helper Method
        Validates a TreeEntry object for correct mode/type, hash, and name.
        """
        if (entry.mode, entry.type) not in {
            ("100644", "blob"),
            ("100755", "blob"),
            ("40000", "tree"),
        }:
            raise ValueError
        if len(entry.hash) != 40 or any(
            character not in "0123456789abcdef" for character in entry.hash
        ):
            raise ValueError
        if entry.name in {"", ".", ".."} or any(
            character in entry.name for character in "/\0\t\r\n"
        ):
            raise ValueError

    def write_tree(self, entries: list[TreeEntry]) -> str:
        """
        Writes a list of TreeEntry objects to the object store in name-sorted order.
        Returns the hash of the tree object.
        Raises ValueError for incorrectly formatted entries.
        """

        sorted_entries = sorted(entries, key=lambda entry: entry.name)

        names = set()
        for entry in sorted_entries:
            self._validate_tree_entry(entry)
            if entry.name in names:
                raise ValueError
            names.add(entry.name)

        tree_data = b"".join(
            f"{entry.mode} {entry.type} {entry.hash}\t{entry.name}\n".encode()
            for entry in sorted_entries
        )
        return self.write_object(tree_data, "tree")

    def read_tree(self, tree_hash: str) -> list[TreeEntry]:
        """
        Reads a tree object from the object store and returns a list of TreeEntry objects.
        Raises ObjectCorruptError if the object is not a tree or is incorrectly formatted.
        """

        obj_type, tree_data = self.read_object(tree_hash)

        if obj_type != "tree":
            raise ObjectCorruptError(tree_hash)

        entries = []

        try:
            text = tree_data.decode("utf-8")
            if text and not text.endswith("\n"):
                raise ValueError
            lines = text.split("\n")[:-1]

            names = set()

            for line in lines:
                if line.endswith("\r"):
                    raise ValueError

                fields, separator, name = line.partition("\t")
                if not separator:
                    raise ValueError

                mode, entry_type, entry_hash = fields.split(" ")
                entry = TreeEntry(mode, entry_type, entry_hash, name)
                self._validate_tree_entry(entry)

                if name in names:
                    raise ValueError

                names.add(name)
                entries.append(entry)

        except (UnicodeDecodeError, ValueError):
            raise ObjectCorruptError(tree_hash) from None

        return entries


def run_hash_object(args) -> int:
    """
    Hash the file at args.path, write it to the object store, and print the hash.
    """
    with open(args.path, "rb") as f:
        data = f.read()
    obj_hash = ObjectStore(".").write_object(data, "blob")
    print(obj_hash)
    return 0


def run_cat_file(args) -> int:
    """
    Print the contents of the object with the given hash.
    """
    _, obj_data = ObjectStore(".").read_object(args.hash)
    print(obj_data.decode("utf-8", errors="replace"), end="")
    return 0


def register_subcommands(subparsers):
    """
    Register the "hash-object" and "cat-file" subcommands with the given subparsers object.
    """
    hash_parser = subparsers.add_parser("hash-object")
    hash_parser.add_argument("path")
    hash_parser.set_defaults(handler=run_hash_object)

    cat_parser = subparsers.add_parser("cat-file")
    cat_parser.add_argument("hash")
    cat_parser.set_defaults(handler=run_cat_file)
