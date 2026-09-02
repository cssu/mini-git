"""Module 3 - Commits and branching.

Owns turning a staged tree into permanent history, plus every ref and branch
operation: commit creation, branch create/list/switch, and merging.

Depends on: modules 1 and 2.
Serves: module 4.

Build the `CommitManager` class here, per the interface contract.
"""

import os
import time

from minigit.errors import RefNotFoundError
from minigit.index import WorkingTree
from minigit.objects import ObjectStore


def _cmd_commit(args) -> int:
    """Handle the 'minigit commit -m <message>' command"""
    manager = CommitManager()
    stub_tree = "0" * 40
    manager.create_commit(
        stub_tree,
        [],
        "Daniel <daniel@example.com>",
        args.message,
    )
    return 0


def _cmd_branch(args) -> int:
    """Handle 'minigit branch <name>' command"""
    manager = CommitManager()
    if args.name:
        manager.create_branch(args.name, "0" * 40)
    else:
        for branch in manager.list_branches():
            if branch == manager._head:
                marker = "* "
            else:
                marker = " "
            print(f"{marker}{branch}")
    return 0


def _cmd_checkout(args) -> int:
    """
    Handle 'minigit checkout <name> command'
    """
    manager = CommitManager()
    manager.switch_branch(args.name)
    print(f"Switched to branch '{args.name}'")
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


class CommitManager:
    def __init__(self, repo_path=".", store=None, tree=None):
        self.root = os.path.abspath(repo_path)
        self._refs = {}
        self._head = "main"
        self.store = store if store is not None else ObjectStore(repo_path)
        self.tree = tree if tree is not None else WorkingTree(repo_path)

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

    def create_commit(self, tree_hash, parents: list[str], author, message) -> str:
        """
        Create a new commit object and write it to the object store
        """
        body = self._format_commit(tree_hash, parents, author, message)
        return self.store.write_object(body.encode(), "commit")

    def create_branch(self, name, commit_hash) -> None:
        """Create a new branch that points at commit_hash"""
        self._refs[name] = commit_hash

    def switch_branch(self, name) -> None:
        """Switch to a branch"""
        if name not in self._refs:
            raise RefNotFoundError(name)
        self._head = name

    def list_branches(self) -> list[str]:
        return sorted(self._refs)

    def merge(self, branch_name) -> str | None:
        return None
