# Run to test: scripts/test.sh tests/test_commits.py
# Testing for Module 3

import pytest

from minigit.commits import CommitManager
from minigit.errors import RefNotFoundError


class FakeObjectStore:
    def __init__(self):
        """Initialize with an empty record of written objects."""
        self.written = []

    def write_object(self, data: bytes, obj_type: str) -> str:
        """Record the write and return a deterministic fake hash."""
        self.written.append((obj_type, data))
        return "a" * 40


class FakeWorkingTree:
    """Minimal stand-in for WorkingTree. No filesystem operations."""

    def checkout(self, tree_hash: str) -> None:
        """Accept a checkout call without doing anything."""


def make_manager():
    """Return CommitManager for testing"""
    return CommitManager(store=FakeObjectStore(), tree=FakeWorkingTree())


# testing commits


def test_contains_tree_line():
    m = make_manager()
    body = m._format_commit("abc" * 13 + "a", [], "Daniel <Daniel@example.com>", "init")
    assert body.startswith("tree ")


def test_contains_author_line():
    m = make_manager()
    body = m._format_commit("a" * 40, [], "Daniel <Daniel@example.com>", "init")
    assert any(line.startswith("author ") for line in body.splitlines())


def test_blank_line_before_message():
    m = make_manager()
    body = m._format_commit("a" * 40, [], "Daniel <Daniel@example.com>", "hello")
    lines = body.splitlines()
    assert lines[-2] == ""
    assert lines[-1] == "hello"


def test_root_commit_no_parent_lines():
    m = make_manager()
    body = m._format_commit("a" * 40, [], "Daniel <Daniel@example.com>", "root")
    assert "parent" not in body


def test_normal_commit_one_parent_line():
    m = make_manager()
    body = m._format_commit("a" * 40, ["b" * 40], "Daniel <Daniel@example.com>", "second")
    parent_lines = [line for line in body.splitlines() if line.startswith("parent ")]
    assert len(parent_lines) == 1
    assert "b" * 40 in parent_lines[0]


def test_merge_commit_two_parent_lines_in_order():
    m = make_manager()
    p1 = "1" * 40
    p2 = "2" * 40
    body = m._format_commit("0" * 40, [p1, p2], "Daniel <Daniel@example.com>", "merge")
    parent_lines = [line for line in body.splitlines() if line.startswith("parent ")]
    assert len(parent_lines) == 2
    assert p1 in parent_lines[0]
    assert p2 in parent_lines[1]


def test_create_commit_returns_hash():
    m = make_manager()
    result = m.create_commit("0" * 40, [], "Daniel <Daniel@example.com>", "init")
    assert len(result) == 40


# branches test:


def test_create_and_list_branches():
    m = make_manager()
    m.create_branch("feature1", "a" * 40)
    m.create_branch("feature2", "b" * 40)
    assert m.list_branches() == ["feature1", "feature2"]


def test_switch_branch_unknown_raises():
    m = make_manager()
    with pytest.raises(RefNotFoundError):
        m.switch_branch("nope")


def test_switch_branch_updates_head():
    m = make_manager()
    m.create_branch("feature", "a" * 40)
    m.switch_branch("feature")
    assert m._head == "feature"
