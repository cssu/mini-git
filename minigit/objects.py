"""Module 1 - Object storage.

Owns the on-disk `.minigit/objects/` directory. The only module that reads or
writes object bytes to disk.

Depends on: nothing.
Serves: modules 2, 3, and 4.

Build the `ObjectStore` class here, per the interface contract.
"""

import hashlib
import os
import tempfile
import zlib
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

    root: Path
    objects_dir: Path

    def __init__(self, repo_path=".") -> None:
        """
        Initialize the object store.
        """
        self.root = Path(repo_path)
        self.objects_dir = self.root / ".minigit" / "objects"

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
        """
        if obj_type not in self._OBJECT_TYPES:
            raise ValueError(f"unsupported object type: {obj_type}")
        obj_hash = self.hash_object(data, obj_type)
        object_path = self._object_path(obj_hash)

        if object_path.exists():
            self.read_object(obj_hash)
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

        if not object_path.exists():
            raise ObjectNotFoundError(hash)

        try:
            compressed_object = object_path.read_bytes()
            decompressor = zlib.decompressobj()
            raw_object = decompressor.decompress(compressed_object) + decompressor.flush()
            if not decompressor.eof or decompressor.unused_data or decompressor.unconsumed_tail:
                raise ObjectCorruptError(hash)
            header, separator, content = raw_object.partition(b"\0")
            if not separator:
                raise ObjectCorruptError(hash)

            header_fields = header.split(b" ")
            if len(header_fields) != 2:
                raise ObjectCorruptError(hash)
            obj_type_bytes, obj_length_bytes = header_fields
            obj_type = obj_type_bytes.decode("ascii")
            if obj_type not in self._OBJECT_TYPES or not obj_length_bytes.isdigit():
                raise ObjectCorruptError(hash)
            if int(obj_length_bytes) != len(content):
                raise ObjectCorruptError(hash)

        except (OSError, UnicodeDecodeError, ValueError, zlib.error) as error:
            raise ObjectCorruptError(hash) from error

        if self.hash_object(content, obj_type) != hash:
            raise ObjectCorruptError(hash)

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
