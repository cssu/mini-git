"""Module 1 - Object storage.

Owns the on-disk `.minigit/objects/` directory. The only module that reads or
writes object bytes to disk.

Depends on: nothing.
Serves: modules 2, 3, and 4.

Build the `ObjectStore` class here, per the interface contract.
"""

import hashlib
import zlib
from pathlib import Path

from minigit.errors import ObjectCorruptError, ObjectNotFoundError


class ObjectStore:
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
        Write the object to the object store and return its hash.
        Allow duplicates to be written.
        """
        obj_hash = self.hash_object(data, obj_type)
        object_path = self._object_path(obj_hash)

        if object_path.exists():
            return obj_hash

        header = f"{obj_type} {len(data)}".encode()
        object_path.parent.mkdir(parents=True, exist_ok=True)
        object_path.write_bytes(zlib.compress(header + b"\0" + data))
        return obj_hash

    def read_object(self, hash: str) -> tuple[str, bytes]:
        """
        Reads the object from the object store and returns a tuple of (type, data).
        Raise ObjectNotFoundError(hash) if the object is not found.
        Raise ObjectCorruptError(hash) if the object is corrupt.
        """
        object_path = self._object_path(hash)

        if not object_path.exists():
            raise ObjectNotFoundError(hash)

        try:
            raw_object = zlib.decompress(object_path.read_bytes())
            header, content = raw_object.split(b"\0", 1)
            obj_type, obj_length = header.decode().split(" ", 1)

            if int(obj_length) != len(content):
                raise ObjectCorruptError(hash)

        except (ValueError, UnicodeDecodeError, zlib.error) as error:
            raise ObjectCorruptError(hash) from error

        if self.hash_object(content, obj_type) != hash:
            raise ObjectCorruptError(hash)

        return obj_type, content

    def _object_path(self, hash: str) -> Path:
        """
        Return the path to the object with the given hash.
        """
        return self.objects_dir / hash[:2] / hash[2:]


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
