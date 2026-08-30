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


_cli_store = ObjectStore()


def run_hash_object(args) -> int:
    """
    Hash the file at args.path, write it to the object store, and print the hash.
    """
    with open(args.path, "rb") as f:
        data = f.read()
    obj_hash = _cli_store.write_object(data, "blob")
    print(obj_hash)
    return 0


def run_cat_file(args) -> int:
    """
    Print the contents of the object with the given hash. 
    """
    _, obj_data = _cli_store.read_object(args.hash)
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
