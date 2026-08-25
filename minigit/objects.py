"""Module 1 - Object storage.

Owns the on-disk `.minigit/objects/` directory. The only module that reads or
writes object bytes to disk.

Depends on: nothing.
Serves: modules 2, 3, and 4.

Build the `ObjectStore` class here, per the interface contract.
"""

import zlib
from pathlib import Path

from minigit.errors import ObjectNotFoundError


class ObjectStore:
    root: Path
    objects_dir: Path
    _fake_store: dict[str, tuple[str, bytes]]

    def __init__(self, repo_path=".") -> None:
        """
        Initialize the object store.
        """
        self.root = Path(repo_path)
        self.objects_dir = self.root / ".minigit" / "objects"
        self._fake_store = {}

    def hash_object(self, data: bytes, obj_type: str) -> str:
        """
        Return the SHA-1 hash of the object, given its data and type.
        """
        # placeholder - real SHA-1 of "<type> <len>\0<content>" lands Week 2
        return f"{zlib.crc32(obj_type.encode() + data):040x}"

    def write_object(self, data: bytes, obj_type: str) -> str:
        """
        Writes the object's hash into self._fake_store and returns the hash.
        Allow duplicates to be written.
        """
        obj_hash = self.hash_object(data, obj_type)
        self._fake_store[obj_hash] = (obj_type, data)
        return obj_hash

    def read_object(self, hash: str) -> tuple[str, bytes]:
        """
        Reads the object from self._fake_store and returns a tuple of (type, data).
        Raise ObjectNotFoundError(hash) if the object is not found.
        """
        if hash not in self._fake_store:
            raise ObjectNotFoundError(hash)

        return self._fake_store[hash]


def register_subcommands(subparsers):
    hash_parser = subparsers.add_parser("hash-object")
    hash_parser.add_argument("path")

    cat_parser = subparsers.add_parser("cat-file")
    cat_parser.add_argument("hash")