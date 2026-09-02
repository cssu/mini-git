"""Module 2 - Index and working tree.

Owns the bridge between live files on disk and the immutable object store:
the index file, staging, diffing, and checkout.

Depends on: module 1.
Serves: module 3.

Build the `WorkingTree` class here, per the interface contract.
"""

from dataclasses import dataclass
from typing import NamedTuple


class IndexEntry(NamedTuple):
    mode: str
    hash: str
    path: str


@dataclass
class DiffResult:
    added: list
    deleted: list
    modified: list


class WorkingTree:
    def __init__(self, repo_path=".", store=None):
        self.root = repo_path
        self.index_path = f"{repo_path}/.minigit/index"

        if store is None:
            from minigit.objects import ObjectStore

            store = ObjectStore(repo_path)

        self.store = store
        self._entries: list[IndexEntry] = []

    def read_index(self) -> list[IndexEntry]:
        return sorted(self._entries, key=lambda e: e.path)

    def write_index(self, entries) -> None:
        self._entries = sorted(entries, key=lambda e: e.path)

    def stage_file(self, path) -> None:
        with open(f"{self.root}/{path}", "rb") as f:
            data = f.read()

        blob_hash = self.store.write_object(data, "blob")

        self._entries = [e for e in self._entries if e.path != path]

        self._entries.append(IndexEntry("100644", blob_hash, path))
        self._entries.sort(key=lambda e: e.path)

    def build_tree_from_index(self) -> str:
        return self.store.write_object(b"", "tree")

    def diff_working_tree_vs(self, tree_hash) -> DiffResult:
        return DiffResult([], [], [])

    def checkout(self, tree_hash) -> None:
        pass


def cmd_add(args) -> int:
    wt = WorkingTree()
    wt.stage_file(args.path)
    return 0


def cmd_status(args) -> int:
    wt = WorkingTree()
    for entry in wt.read_index():
        print(entry.path)
    return 0


def register_index_commands(subparsers) -> None:
    add_parser = subparsers.add_parser("add", help="stage a file")
    add_parser.add_argument("path")
    add_parser.set_defaults(handler=cmd_add)

    status_parser = subparsers.add_parser("status", help="show staged files")
    status_parser.set_defaults(handler=cmd_status)
