"""Module 3 - Commits and branching.

Owns turning a staged tree into permanent history, plus every ref and branch
operation: commit creation, branch create/list/switch, and merging.

Depends on: modules 1 and 2.
Serves: module 4.

Build the `CommitManager` class here, per the interface contract.
"""

import os
import tempfile
import time
from dataclasses import dataclass

from minigit.errors import (
    MergeConflictError,
    MiniGitError,
    ObjectCorruptError,
    RefExistsError,
    RefNotFoundError,
)
from minigit.index import WorkingTree, myers_diff, split_lines
from minigit.objects import ObjectStore, TreeEntry


def _cmd_commit(args) -> int:
    """Handle the 'minigit commit -m <message>' command"""
    manager = CommitManager()
    pending = manager._read_pending_merge()
    if pending is not None:
        _, _, _, unresolved = pending
        if unresolved:
            raise MiniGitError("unresolved merge paths: " + ", ".join(unresolved))
    tree_hash = manager.tree.build_tree_from_index()
    branch = manager._current_branch()
    if pending is None:
        last_commit = manager.read_ref(branch)
        parents = [last_commit] if last_commit else []
    else:
        ours, theirs, _, _ = pending
        parents = [ours, theirs]
    message = args.message
    author = manager.read_author()
    commit_hash = manager.create_commit(
        tree_hash,
        parents,
        author,
        message,
    )
    if pending is not None:
        manager._clear_pending_merge()
    print(f"[{branch} {commit_hash[:7]}] {message}")
    return 0


def _cmd_branch(args) -> int:
    """Handle 'minigit branch <name>' command"""
    manager = CommitManager()
    current = manager._current_branch()
    if args.name:
        commit_hash = manager.read_ref(current)
        if commit_hash is None:
            raise MiniGitError(f"branch '{args.name}': '{current}' has no commits yet")
        manager.create_branch(args.name, commit_hash)
    else:
        for branch in manager.list_branches():
            if branch == current:
                marker = "* "
            else:
                marker = "  "
            print(f"{marker}{branch}")
    return 0


def _cmd_log(args) -> int:
    """Handle the log command"""
    m = CommitManager()
    lines = m.log()
    if not lines:
        print("no commits yet")
        return 0
    for line in lines:
        print(line)
    return 0


def _cmd_checkout(args) -> int:
    """
    Handle 'minigit checkout <name> command'
    """
    manager = CommitManager()
    manager.switch_branch(args.name)
    print(f"Switched to branch '{args.name}'")
    return 0


def _cmd_merge(args) -> int:
    manager = CommitManager()
    manager.merge(args.branch)
    print(f"Merged '{args.branch}' into '{manager._current_branch()}'")
    return 0


def register_subcommands(subparsers) -> None:
    """
    Register the three commands: commit, branch, checkout
    """
    commit_parser = subparsers.add_parser("commit", help="record changes to the repository")
    commit_parser.add_argument("-m", dest="message", required=True, help="commit message")
    commit_parser.set_defaults(handler=_cmd_commit)

    branch_parser = subparsers.add_parser("branch", help="create or list branches")
    branch_parser.add_argument("name", nargs="?", help="branch name to create")
    branch_parser.set_defaults(handler=_cmd_branch)

    checkout_parser = subparsers.add_parser("checkout", help="switch branches")
    checkout_parser.add_argument("name", help="branch name to switch to")
    checkout_parser.set_defaults(handler=_cmd_checkout)

    log_parser = subparsers.add_parser("log", help="show commit history")
    log_parser.set_defaults(handler=_cmd_log)

    merge_parser = subparsers.add_parser("merge", help="merge a branch into the current branch")
    merge_parser.add_argument("branch", help="branch name to merge in")
    merge_parser.set_defaults(handler=_cmd_merge)


class CommitData:
    def __init__(self, tree: str, parents: list[str], author: str, committer: str, message: str):
        self.tree = tree
        self.parents = parents
        self.author = author
        self.committer = committer
        self.message = message


class CommitManager:
    def __init__(self, repo_path=".", store=None, tree=None):
        self.root = os.path.abspath(repo_path)
        self.store = store if store is not None else ObjectStore(repo_path)
        self.tree = tree if tree is not None else WorkingTree(repo_path, store=self.store)

    def _format_commit(self, tree_hash, parents, author, message) -> str:
        """Format commit object as a string"""
        lines = []
        timestamp = int(time.time())
        lines.append(f"tree {tree_hash}")
        for parent in parents:
            lines.append(f"parent {parent}")
        lines.append(f"author {author} {timestamp}")
        lines.append(f"committer {author} {timestamp}")
        lines.append("")
        lines.append(message)
        return "\n".join(lines)

    def _config_path(self) -> str:
        """Return path of the repo-local config file"""
        return os.path.join(self.root, ".minigit", "config")

    def read_author(self) -> str:
        """Return the configured author string, or a default if no config exists.

        The config file, if present, contains the author string on its own
        (e.g. "Daniel <daniel@example.com>"), stripped of surrounding whitespace.
        """
        config_path = self._config_path()
        if not os.path.exists(config_path):
            return "minigit <minigit@local>"
        with open(config_path) as f:
            author = f.read().strip()
        return author or "minigit <minigit@local>"

    def _refs_dir(self) -> str:
        """Return the dir that holds one file per branch"""
        return os.path.join(self.root, ".minigit", "refs", "heads")

    def read_ref(self, name: str) -> str | None:
        """Return the commit hash a branch points at"""
        ref_path = self._ref_path(name)
        if not os.path.exists(ref_path):
            return None
        with open(ref_path) as f:
            return f.read().strip()

    def validate_branch_name(self, name: str) -> None:
        """Reject a branch name that escapes refs/heads or breaks the ref line."""
        if (
            not name
            or any(part in {"", ".", ".."} for part in name.split("/"))
            or any(char in name for char in "\0\\\r\n")
        ):
            raise MiniGitError(f"invalid branch name: {name!r}")

    def _validate_fetched_history(self, commit_hash: str) -> None:
        """Require every commit, tree, and blob reachable from commit_hash.

        Both parents are walked. A missing or corrupt object raises before a ref
        or the work tree is updated.
        """
        visited_trees: set[str] = set()
        for current in self.walk_history(commit_hash):
            commit = self.read_commit(current)
            self._validate_fetched_tree(commit.tree, visited_trees)

    def _validate_fetched_tree(self, tree_hash: str, visited: set[str]) -> None:
        """Check one tree and everything stored under it, once."""
        if tree_hash in visited:
            return
        visited.add(tree_hash)
        for entry in self.store.read_tree(tree_hash):
            if entry.type == "tree":
                self._validate_fetched_tree(entry.hash, visited)
            else:
                obj_type, _ = self.store.read_object(entry.hash)
                if obj_type != "blob":
                    raise ObjectCorruptError(entry.hash)

    def _ref_path(self, name: str) -> str:
        """Return the file path for a single branch's ref."""
        self.validate_branch_name(name)
        return os.path.join(self._refs_dir(), name)

    def write_ref(self, name: str, commit_hash: str) -> None:
        """Point a branch ref at commit_hash.

        The new contents are written to a temporary file in the ref directory
        and then published with os.replace. A failed publish leaves the previous
        ref readable and removes the temporary file.
        """
        ref_path = self._ref_path(name)
        os.makedirs(os.path.dirname(ref_path), exist_ok=True)
        temporary_path = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                dir=os.path.dirname(ref_path),
                prefix=f".{os.path.basename(ref_path)}.",
                delete=False,
            ) as temporary_file:
                temporary_path = temporary_file.name
                temporary_file.write(f"{commit_hash}\n")
                temporary_file.flush()
            os.replace(temporary_path, ref_path)
            temporary_path = None
        finally:
            if temporary_path is not None:
                os.unlink(temporary_path)

    def _head_path(self) -> str:
        """Return path of file that is the current branch"""
        return os.path.join(self.root, ".minigit", "HEAD")

    def read_head(self) -> str | None:
        """Return the current branch name
        Returns None if HEAD not written yet (before the first commit/checkout).
        """
        if not os.path.exists(self._head_path()):
            return None
        with open(self._head_path()) as f:
            content = f.read().strip()
        if content.startswith("ref: refs/heads/"):
            return content[len("ref: refs/heads/") :]
        return content  # detached HEAD: a raw commit hash

    def write_head(self, name: str) -> None:
        """Point HEAD at the given branch."""
        with open(self._head_path(), "w") as f:
            f.write(f"ref: refs/heads/{name}\n")

    def _current_branch(self) -> str:
        """Return the name of the branch HEAD currently points at."""
        return self.read_head() or "main"

    def _empty_tree_hash(self) -> str:
        """Return the hash of empty tree, used to stand in for an unborn HEAD"""
        return self.store.hash_object(b"", "tree")

    def _require_no_staged_changes(self) -> None:
        """Raise MiniGitError if the index differs from the tree HEAD points at.

        An unborn HEAD counts as an empty tree, so anything staged before the
        first commit is a staged change
        """
        head_tree = self.get_head_tree() or self._empty_tree_hash()
        if self.tree.build_tree_from_index() != head_tree:
            raise MiniGitError("staged changes present; commit them first")

    def switch_branch(self, name) -> None:
        """
        Point HEAD at name and restore the working tree from that branch's commit
        """
        if self._read_pending_merge() is not None:
            raise MiniGitError("merge in progress")
        if not os.path.exists(self._ref_path(name)):
            raise RefNotFoundError(name)
        self._require_no_staged_changes()
        commit = self.read_commit(self.read_ref(name))
        self.tree.checkout(commit.tree)
        self.write_head(name)

    def read_commit(self, commit_hash: str) -> CommitData:
        obj_type, data = self.store.read_object(commit_hash)
        if obj_type != "commit":
            raise ObjectCorruptError(commit_hash)
        try:
            body = data.decode()
            header, message = body.split("\n\n", 1)
            lines = header.splitlines()

            if not lines[0].startswith("tree "):
                raise ValueError("missing tree line")
            tree = lines[0][len("tree ") :]

            i = 1
            parents = []
            while lines[i].startswith("parent "):
                parents.append(lines[i][len("parent ") :])
                i += 1
            if not lines[i].startswith("author "):
                raise ValueError("missing author line")
            author = lines[i][len("author ") :].rsplit(" ", 1)[0]
            i += 1

            if not lines[i].startswith("committer "):
                raise ValueError("missing committer line")
            committer = lines[i][len("committer ") :].rsplit(" ", 1)[0]
            if i != len(lines) - 1:
                raise ValueError("unexpected commit headers")
            for object_hash in [tree, *parents]:
                if len(object_hash) != 40 or any(c not in "0123456789abcdef" for c in object_hash):
                    raise ValueError("invalid object hash")
            for identity_line in lines[-2:]:
                identity, timestamp = identity_line.split(" ", 1)[1].rsplit(" ", 1)
                if not identity.strip():
                    raise ValueError("missing identity")
                int(timestamp)

        except (UnicodeDecodeError, IndexError, ValueError) as error:
            raise ObjectCorruptError(commit_hash) from error

        return CommitData(tree, parents, author, committer, message)

    def get_head_tree(self) -> str | None:
        head = self._current_branch()
        if os.path.exists(self._ref_path(head)):
            commit_hash = self.read_ref(head)
        elif len(head) == 40:
            commit_hash = head
        else:
            return None

        return self.read_commit(commit_hash).tree

    def walk_history(self, start_hash: str) -> list[str]:
        visited = []
        seen = set()
        stack = [start_hash]

        while stack:
            commit_hash = stack.pop()
            if commit_hash in seen:
                continue
            seen.add(commit_hash)
            visited.append(commit_hash)

            parents = self.read_commit(commit_hash).parents
            stack.extend(reversed(parents))

        return visited

    def is_ancestor(self, ancestor_hash: str, descendant_hash: str) -> bool:
        """Return True if ancestor_hash is reachable from descendant_hash"""
        return ancestor_hash in self.walk_history(descendant_hash)

    def create_commit(self, tree_hash, parents, author, message) -> str:
        """
        Create a new commit object, write it to the object store, and advance
        the current branch's ref to point at the new commit.

        The caller is responsible for resolving `parents` (e.g. via `read_ref`
        on the current branch, or `[]` for a root commit).
        """
        obj_type, _ = self.store.read_object(tree_hash)
        if obj_type != "tree":
            raise ObjectCorruptError(tree_hash)

        branch = self._current_branch()
        body = self._format_commit(tree_hash, parents, author, message)
        commit_hash = self.store.write_object(body.encode(), "commit")
        self.write_ref(branch, commit_hash)
        return commit_hash

    def create_branch(self, name, commit_hash) -> None:
        """Create a new branch that points at commit_hash"""
        if os.path.exists(self._ref_path(name)):
            raise RefExistsError(name)
        self.write_ref(name, commit_hash)

    def list_branches(self) -> list[str]:
        if not os.path.isdir(self._refs_dir()):
            return []
        else:
            return sorted(os.listdir(self._refs_dir()))

    def merge(self, branch_name) -> str | None:
        """Fast-forward or three-way merge `branch_name` into the current branch.

        Fast-forward and no-op cases return None. A clean three-way merge returns
        the new commit hash. A text conflict raises MergeConflictError.
        """
        if self._read_pending_merge() is not None:
            raise MiniGitError("merge in progress")
        if not os.path.exists(self._ref_path(branch_name)):
            raise RefNotFoundError(branch_name)

        target = self.read_ref(branch_name)
        current = self._current_branch()
        current_hash = self.read_ref(current)

        if current_hash is not None and (
            target == current_hash or self.is_ancestor(target, current_hash)
        ):
            return None
        if current_hash is None or self.is_ancestor(current_hash, target):
            self._require_no_staged_changes()
            self.tree.checkout(self.read_commit(target).tree)
            self.write_ref(current, target)
            return None

        return self._three_way_merge(current_hash, target, branch_name)

    def integrate_fetched_tip(self, branch_name: str, commit_hash: str) -> str | None:
        """Integrate one fetched commit into a named local branch.

        Inputs:
            branch_name: the local branch to update.
            commit_hash: the fetched commit, already stored in this repo.

        Returns:
            None when the ref is created, fast-forwarded, or left unchanged.
            The new commit hash when the checked-out branch has a clean merge.

        Errors:
            MiniGitError for an invalid branch name, a merge already in progress,
            staged changes on a checked-out update, unrelated history, multiple
            merge bases, an unsupported conflict, or a diverged branch that is
            not checked out. MergeConflictError for a text conflict on the
            checked-out branch. ObjectNotFoundError or ObjectCorruptError when
            the fetched history is missing or corrupt.

        Local state:
            Checked-out unborn branch, or local tip is an ancestor of the fetched
            commit: check out the fetched tree, then move that branch ref. HEAD
            stays on the branch.
            Checked-out equal tips, or fetched commit is an ancestor of the local
            tip: change nothing.
            Checked-out divergence: the existing three-way merge. A clean result
            has parents [local tip, fetched tip]. A text conflict leaves both
            tips in place and saves both parents and the unresolved paths.
            Absent branch that is not checked out: create its ref after history
            validation. HEAD, files, and the index stay as they are.
            Existing branch that is not checked out: fast-forward that ref, or
            leave it, without changing HEAD, files, or the index.
            Diverged branch that is not checked out: raise, with an instruction
            to check out that branch and pull again.
        """
        self.validate_branch_name(branch_name)
        if self._read_pending_merge() is not None:
            raise MiniGitError("merge in progress")
        self._validate_fetched_history(commit_hash)

        local_hash = self.read_ref(branch_name)
        checked_out = branch_name == self._current_branch()

        if checked_out:
            if local_hash is not None and (
                commit_hash == local_hash or self.is_ancestor(commit_hash, local_hash)
            ):
                return None
            if local_hash is None or self.is_ancestor(local_hash, commit_hash):
                self._require_no_staged_changes()
                self.tree.checkout(self.read_commit(commit_hash).tree)
                self.write_ref(branch_name, commit_hash)
                return None
            return self._three_way_merge(local_hash, commit_hash, branch_name)

        if local_hash is None:
            self.write_ref(branch_name, commit_hash)
            return None
        if commit_hash == local_hash or self.is_ancestor(commit_hash, local_hash):
            return None
        if self.is_ancestor(local_hash, commit_hash):
            self.write_ref(branch_name, commit_hash)
            return None
        raise MiniGitError(
            f"branch '{branch_name}' has diverged; check out that branch and pull again"
        )

    def find_merge_base(self, ours: str, theirs: str) -> str:
        """Return the single lowest common ancestor of two commits."""
        common = set(self.walk_history(ours)) & set(self.walk_history(theirs))
        if not common:
            raise MiniGitError("no common ancestor")
        lowest = [
            commit
            for commit in common
            if not any(other != commit and self.is_ancestor(commit, other) for other in common)
        ]
        if len(lowest) != 1:
            raise MiniGitError("multiple merge bases")
        return lowest[0]

    def mark_path_resolved(self, path: str) -> None:
        """Drop a path from MERGE_UNRESOLVED after an explicit add."""
        pending = self._read_pending_merge()
        if pending is None:
            return
        ours, theirs, message, unresolved = pending
        remaining = [item for item in unresolved if item != path]
        self._write_pending_merge(ours, theirs, message, remaining)

    def _three_way_merge(self, current_hash: str, target: str, branch_name: str) -> str:
        """Merge two diverged commits and either commit or raise MergeConflictError."""
        base_hash = self.find_merge_base(current_hash, target)
        base_map = self._flatten(self.read_commit(base_hash).tree)
        ours_map = self._flatten(self.read_commit(current_hash).tree)
        theirs_map = self._flatten(self.read_commit(target).tree)

        result_files: dict[str, tuple[str, str]] = {}
        conflict_paths: list[str] = []
        for path in sorted(set(base_map) | set(ours_map) | set(theirs_map)):
            base = base_map.get(path)
            ours = ours_map.get(path)
            theirs = theirs_map.get(path)
            taken = self._take_side(base, ours, theirs)
            if taken == "conflict":
                conflict_paths.append(path)
                marker = self._conflict_text(
                    self._decode_entry(ours), self._decode_entry(theirs), branch_name
                )
                mode = ours[0] if ours else theirs[0]
                result_files[path] = (mode, self.store.write_object(marker.encode(), "blob"))
            elif taken == "merge-text":
                merged = self._merge_blob_pair(base, ours, theirs)
                if merged is None:
                    conflict_paths.append(path)
                    marker = self._conflict_text(
                        self._decode_entry(ours), self._decode_entry(theirs), branch_name
                    )
                    result_files[path] = (ours[0], self.store.write_object(marker.encode(), "blob"))
                else:
                    result_files[path] = (ours[0], self.store.write_object(merged.encode(), "blob"))
            elif taken is not None:
                result_files[path] = taken

        if self._has_file_directory_conflict(result_files):
            raise MiniGitError("file/directory conflict")
        self._require_no_staged_changes()
        result_tree = self._build_tree_from_paths(result_files)
        message = f"Merge branch '{branch_name}'"

        if not conflict_paths:
            self.tree.checkout(result_tree)
            return self.create_commit(
                result_tree, [current_hash, target], self.read_author(), message
            )

        pre_index = {entry.path: entry for entry in self.tree.read_index()}
        self.tree.checkout(result_tree)
        restored = []
        for entry in self.tree.read_index():
            if entry.path in conflict_paths:
                if entry.path in pre_index:
                    restored.append(pre_index[entry.path])
                continue
            restored.append(entry)
        for path in conflict_paths:
            if path in pre_index and all(entry.path != path for entry in restored):
                restored.append(pre_index[path])
        self.tree.write_index(restored)
        self._write_pending_merge(current_hash, target, message, conflict_paths)
        raise MergeConflictError(sorted(conflict_paths))

    def _flatten(self, tree_hash: str) -> dict[str, tuple[str, str]]:
        """Return path -> (mode, hash) using M2."""
        return {e.path: (e.mode, e.hash) for e in self.tree.read_tree_entries(tree_hash)}

    def _take_side(self, base, ours, theirs):
        """Return the winning (mode, hash), None to delete, or a conflict/text-merge sentinel."""
        if ours == theirs:
            return ours
        if ours == base:
            return theirs
        if theirs == base:
            return ours
        if ours is None or theirs is None:
            return "conflict"
        return "merge-text"

    def _blob_bytes(self, blob_hash: str) -> bytes:
        """Read a blob through M1."""
        obj_type, data = self.store.read_object(blob_hash)
        if obj_type != "blob":
            raise ObjectCorruptError(blob_hash)
        return data

    def _decode_entry(self, entry: tuple[str, str] | None) -> str:
        """Return blob text for a flattened entry, or empty string if the path is missing."""
        if entry is None:
            return ""
        data = self._blob_bytes(entry[1])
        if b"\0" in data:
            raise MiniGitError("binary file cannot be merged")
        try:
            return data.decode()
        except UnicodeDecodeError as error:
            raise MiniGitError("binary file cannot be merged") from error

    def _merge_blob_pair(self, base, ours, theirs) -> str | None:
        """Merge two changed blobs against base. None means a text conflict."""
        if ours[0] != theirs[0]:
            raise MiniGitError("mode conflict")
        ours_data = self._blob_bytes(ours[1])
        theirs_data = self._blob_bytes(theirs[1])
        base_data = self._blob_bytes(base[1]) if base else b""
        if b"\0" in ours_data or b"\0" in theirs_data or b"\0" in base_data:
            raise MiniGitError("binary file cannot be merged")
        try:
            return _merge_text(base_data.decode(), ours_data.decode(), theirs_data.decode())
        except UnicodeDecodeError as error:
            raise MiniGitError("binary file cannot be merged") from error

    def _has_file_directory_conflict(self, result: dict[str, tuple[str, str]]) -> bool:
        """Return True when a path is both a file and a parent of another path."""
        paths = sorted(result)
        for i, path in enumerate(paths):
            prefix = path + "/"
            for other in paths[i + 1 :]:
                if other.startswith(prefix):
                    return True
        return False

    def _build_tree_from_paths(self, files: dict[str, tuple[str, str]]) -> str:
        """Write the result tree through M1."""
        root: dict = {}
        for path, (mode, blob_hash) in files.items():
            node = root
            parts = path.split("/")
            for part in parts[:-1]:
                node = node.setdefault(part, {})
            node[parts[-1]] = (mode, blob_hash)

        def write_dir(node: dict) -> str:
            """Write one tree object from a nested path map."""
            entries = []
            for name in sorted(node):
                value = node[name]
                if isinstance(value, dict):
                    entries.append(TreeEntry("40000", "tree", write_dir(value), name))
                else:
                    mode, blob_hash = value
                    entries.append(TreeEntry(mode, "blob", blob_hash, name))
            return self.store.write_tree(entries)

        return write_dir(root)

    def _conflict_text(self, ours_text: str, theirs_text: str, branch_name: str) -> str:
        """Build a conflict-marker blob."""
        if ours_text and not ours_text.endswith("\n"):
            ours_text += "\n"
        if theirs_text and not theirs_text.endswith("\n"):
            theirs_text += "\n"
        return f"<<<<<<< HEAD\n{ours_text}=======\n{theirs_text}>>>>>>> {branch_name}\n"

    def _merge_state_paths(self) -> dict[str, str]:
        """Return the pending-merge file paths under `.minigit/`."""
        meta = os.path.join(self.root, ".minigit")
        return {
            "ours": os.path.join(meta, "MERGE_OURS"),
            "theirs": os.path.join(meta, "MERGE_HEAD"),
            "message": os.path.join(meta, "MERGE_MSG"),
            "unresolved": os.path.join(meta, "MERGE_UNRESOLVED"),
        }

    def _write_pending_merge(self, ours: str, theirs: str, message: str, paths: list[str]) -> None:
        """Save both parents, the merge message, and unresolved paths."""
        paths_map = self._merge_state_paths()
        os.makedirs(os.path.join(self.root, ".minigit"), exist_ok=True)
        with open(paths_map["ours"], "w") as handle:
            handle.write(ours + "\n")
        with open(paths_map["theirs"], "w") as handle:
            handle.write(theirs + "\n")
        with open(paths_map["message"], "w") as handle:
            handle.write(message + "\n")
        with open(paths_map["unresolved"], "w") as handle:
            handle.write("".join(path + "\n" for path in sorted(paths)))

    def _read_pending_merge(self) -> tuple[str, str, str, list[str]] | None:
        """Return (ours, theirs, message, unresolved paths), or None if no merge is pending."""
        paths_map = self._merge_state_paths()
        if not os.path.exists(paths_map["theirs"]):
            return None

        def read(path: str) -> str:
            """Read a pending-merge file as text."""
            with open(path) as handle:
                return handle.read()

        unresolved = [line for line in read(paths_map["unresolved"]).split("\n") if line]
        return (
            read(paths_map["ours"]).strip(),
            read(paths_map["theirs"]).strip(),
            read(paths_map["message"]).strip(),
            unresolved,
        )

    def _clear_pending_merge(self) -> None:
        """Delete pending-merge files after a completed merge commit."""
        for path in self._merge_state_paths().values():
            if os.path.exists(path):
                os.remove(path)

    def log(self) -> list[str]:
        """Return one summary line per commit reachable from HEAD, newest first"""
        commit_hash = self.read_ref(self._current_branch())
        if not commit_hash:
            return []

        lines = []
        for i in self.walk_history(commit_hash):
            msg = self.read_commit(i).message
            if msg:
                summary = msg.splitlines()[0]
            else:
                summary = ""
            lines.append(f"{i[:7]} {summary}")

        return lines


@dataclass
class Hunk:
    """One edit against the base file"""

    base_start: int
    base_count: int
    lines: list[str]


def _diff_lines(base: list[str], other: list[str]) -> list[Hunk]:
    """Adapt M2's Myers edits without changing their base coordinates."""
    return [Hunk(edit.start, edit.old_count, edit.new_lines) for edit in myers_diff(base, other)]


def _ranges_overlap(a: Hunk, b: Hunk) -> bool:
    """True if hunks overlap or insert at the same base index"""
    if a.base_count == 0 and b.base_count == 0:
        return a.base_start == b.base_start
    return a.base_start < b.base_start + b.base_count and b.base_start < a.base_start + a.base_count


def _combine_hunks(ours: list[Hunk], theirs: list[Hunk]) -> list[Hunk] | None:
    """Combine disjoint hunks"""
    merged = []
    for hunk in ours + theirs:
        skip = False
        for existing in merged:
            if (existing.base_start, existing.base_count, existing.lines) == (
                hunk.base_start,
                hunk.base_count,
                hunk.lines,
            ):
                skip = True
                break
            if _ranges_overlap(existing, hunk):
                return None
        if not skip:
            merged.append(hunk)
    return sorted(merged, key=lambda hunk: (hunk.base_start, hunk.base_count))


def _apply_hunks(base_lines: list[str], hunks: list[Hunk]) -> list[str]:
    """Apply combined hunks to base."""
    result = []
    index = 0
    for hunk in hunks:
        result.extend(base_lines[index : hunk.base_start])
        result.extend(hunk.lines)
        index = hunk.base_start + hunk.base_count
    result.extend(base_lines[index:])
    return result


def _merge_text(base_text: str, ours_text: str, theirs_text: str) -> str | None:
    """Diff each side against base. Combine or conflict"""
    base_lines = split_lines(base_text)
    combined = _combine_hunks(
        _diff_lines(base_lines, split_lines(ours_text)),
        _diff_lines(base_lines, split_lines(theirs_text)),
    )
    if combined is None:
        return None
    return "".join(_apply_hunks(base_lines, combined))
