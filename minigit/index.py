"""Module 2 - Index and working tree.

Owns the bridge between live files on disk and the immutable object store:
the index file, staging, diffing, and checkout.

Depends on: module 1.
Serves: module 3.

Build the `WorkingTree` class here, per the interface contract.
"""

import os
from dataclasses import dataclass
from typing import NamedTuple

from minigit.errors import MiniGitError


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

    def read_index(self) -> list[IndexEntry]:
        if not os.path.exists(self.index_path):
            return []

        entries = []
        with open(self.index_path, encoding="utf-8") as f:
            for line in f:
                line = line.rstrip("\n")
                if not line:
                    continue
                mode, hash_, path = line.split(" ", 2)
                entries.append(IndexEntry(mode, hash_, path))

        return sorted(entries, key=lambda e: e.path)

    def write_index(self, entries) -> None:
        entries = sorted(entries, key=lambda e: e.path)
        lines = [f"{e.mode} {e.hash} {e.path}" for e in entries]
        content = "\n".join(lines)
        if content:
            content += "\n"

        os.makedirs(os.path.dirname(self.index_path), exist_ok=True)
        with open(self.index_path, "w", encoding="utf-8") as f:
            f.write(content)

    def stage_file(self, path) -> None:
        full_path = os.path.join(self.root, path)

        if not os.path.isfile(full_path):
            raise MiniGitError(f"No such file: {path}")

        rel_path = os.path.relpath(full_path, self.root)
        rel_path = rel_path.replace(os.sep, "/")

        with open(full_path, "rb") as f:
            data = f.read()

        blob_hash = self.store.write_object(data, "blob")

        if os.access(full_path, os.X_OK):
            mode = "100755"
        else:
            mode = "100644"

        entries = [e for e in self.read_index() if e.path != path]
        entries.append(IndexEntry(mode, blob_hash, path))
        self.write_index(entries)

    def build_tree_from_index(self) -> str:
        return self.store.write_object(b"", "tree")

    def diff_working_tree_vs(self, tree_hash) -> DiffResult:
        return DiffResult([], [], [])

    def _working_status(self) -> DiffResult:
        result = DiffResult([], [], [])
        entries = self.read_index()
        known_paths = {e.path for e in entries}

        # check staged entries against disk
        for entry in entries:
            full_path = f"{self.root}/{entry.path}"
            if not os.path.isfile(full_path):
                result.deleted.append(entry.path)
            else:
                with open(full_path, "rb") as f:
                    data = f.read()
                current_hash = self.store.write_object(data, "blob")
                if current_hash != entry.hash:
                    result.modified.append(entry.path)

        # walk the working tree for untracked files
        for dirpath, dirnames, filenames in os.walk(self.root):
            dirnames[:] = [d for d in dirnames if d != ".minigit"]
            for filename in filenames:
                full_path = os.path.join(dirpath, filename)
                rel_path = os.path.relpath(full_path, self.root)
                if rel_path not in known_paths:
                    result.added.append(rel_path)

        return result

    def checkout(self, tree_hash) -> None:
        pass


def cmd_add(args) -> int:
    wt = WorkingTree()
    wt.stage_file(args.path)
    return 0


def cmd_status(args) -> int:
    wt = WorkingTree()
    result = wt._working_status()
    entries = wt.read_index()

    # build each category once, upfront
    changed_paths = result.modified + result.deleted
    staged = [e.path for e in entries if e.path not in changed_paths]
    not_staged = sorted(changed_paths)
    untracked = sorted(result.added)

    printed_anything = False

    if staged:
        print("staged:")
        for path in staged:
            print(f"  {path}")
        printed_anything = True

    if not_staged:
        print("not staged:")
        for path in not_staged:
            print(f"  {path}")
        printed_anything = True

    if untracked:
        print("untracked:")
        for path in untracked:
            print(f"  {path}")
        printed_anything = True

    if not printed_anything:
        print("clean")

    return 0

def register_index_commands(subparsers) -> None:
    add_parser = subparsers.add_parser("add", help="stage a file")
    add_parser.add_argument("path")
    add_parser.set_defaults(handler=cmd_add)

    status_parser = subparsers.add_parser("status", help="show staged files")
    status_parser.set_defaults(handler=cmd_status)
