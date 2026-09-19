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

        entries = [e for e in self.read_index() if e.path != rel_path]
        entries.append(IndexEntry(mode, blob_hash, rel_path))
        self.write_index(entries)

    def build_tree_from_index(self) -> str:
        from minigit.objects import TreeEntry

        entries = self.read_index()

        root: dict = {}
        for entry in entries:
            parts = entry.path.split("/")
            current = root
            for part in parts[:-1]:
                current = current.setdefault(part, {})
            current[parts[-1]] = entry

        def write_dir(node: dict) -> str:
            tree_entries = []
            for name in sorted(node.keys()):
                value = node[name]
                if isinstance(value, dict):
                    subtree_hash = write_dir(value)
                    tree_entries.append(
                        TreeEntry(mode="40000", type="tree", hash=subtree_hash, name=name)
                    )
                else:
                    tree_entries.append(
                        TreeEntry(mode=value.mode, type="blob", hash=value.hash, name=name)
                    )
            return self.store.write_tree(tree_entries)

        return write_dir(root)

    def read_tree_entries(self, tree_hash: str) -> list[IndexEntry]:
        entries = []

        def walk(hash_, prefix):
            for te in self.store.read_tree(hash_):
                path = f"{prefix}/{te.name}" if prefix else te.name
                if te.type == "tree":
                    walk(te.hash, path)
                else:
                    entries.append(IndexEntry(te.mode, te.hash, path))

        walk(tree_hash, "")
        return sorted(entries, key=lambda e: e.path)

    def diff_working_tree_vs(self, tree_hash) -> DiffResult:
        result = DiffResult([], [], [])
        tree_entries = self.read_tree_entries(tree_hash)
        known_paths = {e.path for e in tree_entries}

        for entry in tree_entries:
            full_path = os.path.join(self.root, entry.path)
            if not os.path.isfile(full_path):
                result.deleted.append(entry.path)
            else:
                with open(full_path, "rb") as f:
                    data = f.read()
                current_hash = self.store.write_object(data, "blob")
                current_mode = "100755" if os.access(full_path, os.X_OK) else "100644"
                if current_hash != entry.hash or current_mode != entry.mode:
                    result.modified.append(entry.path)

        for dirpath, dirnames, filenames in os.walk(self.root):
            dirnames[:] = [d for d in dirnames if d != ".minigit"]
            for filename in filenames:
                full_path = os.path.join(dirpath, filename)
                rel_path = os.path.relpath(full_path, self.root).replace(os.sep, "/")
                if rel_path not in known_paths:
                    result.added.append(rel_path)

        result.added.sort()
        result.deleted.sort()
        result.modified.sort()
        return result

    def _working_status(self) -> DiffResult:
        result = DiffResult([], [], [])
        entries = self.read_index()
        known_paths = {e.path for e in entries}

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
    index_entries = wt.read_index()
    index_by_path = {e.path: e for e in index_entries}

    from minigit.commits import get_head_tree

    head_tree_hash = get_head_tree()
    head_entries = wt.read_tree_entries(head_tree_hash) if head_tree_hash else []
    head_by_path = {e.path: e for e in head_entries}

    # staged: HEAD tree vs index
    staged_paths = set()
    for path, entry in index_by_path.items():
        head_entry = head_by_path.get(path)
        if head_entry is None or head_entry.hash != entry.hash or head_entry.mode != entry.mode:
            staged_paths.add(path)
    for path in head_by_path:
        if path not in index_by_path:
            staged_paths.add(path)

    # not staged: index vs disk
    not_staged_paths = set()
    for entry in index_entries:
        full_path = os.path.join(wt.root, entry.path)
        if not os.path.isfile(full_path):
            not_staged_paths.add(entry.path)
        else:
            with open(full_path, "rb") as f:
                data = f.read()
            current_hash = wt.store.write_object(data, "blob")
            current_mode = "100755" if os.access(full_path, os.X_OK) else "100644"
            if current_hash != entry.hash or current_mode != entry.mode:
                not_staged_paths.add(entry.path)

    # untracked: on disk, absent from both index and HEAD
    known_paths = set(index_by_path) | set(head_by_path)
    untracked_paths = set()
    for dirpath, dirnames, filenames in os.walk(wt.root):
        dirnames[:] = [d for d in dirnames if d != ".minigit"]
        for filename in filenames:
            full_path = os.path.join(dirpath, filename)
            rel_path = os.path.relpath(full_path, wt.root).replace(os.sep, "/")
            if rel_path not in known_paths:
                untracked_paths.add(rel_path)

    staged = sorted(staged_paths)
    not_staged = sorted(not_staged_paths)
    untracked = sorted(untracked_paths)

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
