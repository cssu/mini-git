"""Module 3 - Commits and branching.

Owns turning a staged tree into permanent history, plus every ref and branch
operation: commit creation, branch create/list/switch, and merging.

Depends on: modules 1 and 2.
Serves: module 4.

Build the `CommitManager` class here, per the interface contract.
"""

import os
import time

from minigit.errors import (
    MiniGitError,
    ObjectCorruptError,
    RefExistsError,
    RefNotFoundError,
)
from minigit.index import WorkingTree
from minigit.objects import ObjectStore


def _cmd_commit(args) -> int:
    """Handle the 'minigit commit -m <message>' command"""
    manager = CommitManager()
    tree_hash = manager.tree.build_tree_from_index()
    branch = manager._current_branch()
    last_commit = manager.read_ref(branch)
    parents = [last_commit] if last_commit else []
    author = manager.read_author()
    commit_hash = manager.create_commit(
        tree_hash,
        parents,
        author,
        args.message,
    )
    print(f"[{branch} {commit_hash[:7]}] {args.message}")
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

    merge_parser = subparsers.add_parser("merge", help="fast-forward the current branch")
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

    def write_ref(self, name: str, commit_hash: str) -> None:
        """Point a branch's ref file at the given commit hash, creating refs/heads/ if needed"""
        os.makedirs(self._refs_dir(), exist_ok=True)
        with open(self._ref_path(name), "w") as f:
            f.write(f"{commit_hash}\n")

    def _ref_path(self, name: str) -> str:
        """Return the file path for a single branch's ref"""
        return os.path.join(self._refs_dir(), name)

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
        # Week 4 fast-forward / Week 5 three-way
        """Fast-forward the current branch to `branch_name` when history allows it

        Raises RefNotFoundError for an
        unknown branch, and MiniGitError when the two histories have diverged.
        """
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

        raise MiniGitError("three-way merge is not implemented yet")

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
