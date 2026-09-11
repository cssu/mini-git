# Run to test: scripts/test.sh tests/test_commits.py
# Testing for Module 3

import pytest

from minigit.commits import CommitManager
from minigit.errors import RefExistsError, RefNotFoundError
from minigit.objects import ObjectStore


class FakeWorkingTree:
    """Minimal stand-in for WorkingTree. No filesystem operations."""

    def checkout(self, tree_hash: str) -> None:
        """checkout call"""


def make_manager(temp_path):
    """Return CommitManager for testing"""
    return CommitManager(
        repo_path=str(temp_path), store=ObjectStore(temp_path), tree=FakeWorkingTree()
    )


# testing commits


def test_contains_tree_line(tmp_path):
    m = make_manager(tmp_path)
    body = m._format_commit("abc" * 13 + "a", [], "Daniel <Daniel@example.com>", "init")
    assert body.startswith("tree ")


def test_contains_author_line(tmp_path):
    m = make_manager(tmp_path)
    body = m._format_commit("a" * 40, [], "Daniel <Daniel@example.com>", "init")
    assert any(line.startswith("author ") for line in body.splitlines())


def test_blank_line_before_message(tmp_path):
    m = make_manager(tmp_path)
    body = m._format_commit("a" * 40, [], "Daniel <Daniel@example.com>", "hello")
    lines = body.splitlines()
    assert lines[-2] == ""
    assert lines[-1] == "hello"


def test_root_commit_no_parent_lines(tmp_path):
    m = make_manager(tmp_path)
    body = m._format_commit("a" * 40, [], "Daniel <Daniel@example.com>", "root")
    assert "parent" not in body


def test_normal_commit_one_parent_line(tmp_path):
    m = make_manager(tmp_path)
    body = m._format_commit("a" * 40, ["b" * 40], "Daniel <Daniel@example.com>", "second")
    parent_lines = [line for line in body.splitlines() if line.startswith("parent ")]
    assert len(parent_lines) == 1
    assert "b" * 40 in parent_lines[0]


def test_merge_commit_two_parent_lines_in_order(tmp_path):
    m = make_manager(tmp_path)
    p1 = "1" * 40
    p2 = "2" * 40
    body = m._format_commit("0" * 40, [p1, p2], "Daniel <Daniel@example.com>", "merge")
    parent_lines = [line for line in body.splitlines() if line.startswith("parent ")]
    assert len(parent_lines) == 2
    assert p1 in parent_lines[0]
    assert p2 in parent_lines[1]


# testing create_commit + refs
def test_create_commit_returns_hash(tmp_path):
    m = make_manager(tmp_path)
    result = m.create_commit("0" * 40, [], "Daniel <Daniel@example.com>", "init")
    assert len(result) == 40


def test_first_commit_has_no_parent_lines(tmp_path):
    m = make_manager(tmp_path)
    commit_hash = m.create_commit("a" * 40, [], "Daniel <d@example.com>", "init")
    _, body = m.store.read_object(commit_hash)
    assert "parent" not in body.decode()


def test_first_commit_creates_ref_file(tmp_path):
    m = make_manager(tmp_path)
    m.create_commit("a" * 40, [], "Daniel <d@example.com>", "init")
    ref_file = tmp_path / ".minigit" / "refs" / "heads" / "main"
    assert ref_file.exists()


def test_second_commit_has_one_parent_line_pointing_at_first(tmp_path):
    m = make_manager(tmp_path)
    first = m.create_commit("a" * 40, [], "Daniel <d@example.com>", "init")
    second = m.create_commit("b" * 40, [first], "Daniel <d@example.com>", "second")
    _, body = m.store.read_object(second)
    parent_lines = [line for line in body.decode().splitlines() if line.startswith("parent ")]
    assert len(parent_lines) == 1
    assert first in parent_lines[0]


def test_second_commit_moves_the_ref_file(tmp_path):
    m = make_manager(tmp_path)
    first = m.create_commit("a" * 40, [], "Daniel <d@example.com>", "init")
    second = m.create_commit("b" * 40, [first], "Daniel <d@example.com>", "second")
    ref_file = tmp_path / ".minigit" / "refs" / "heads" / "main"
    assert ref_file.read_text() == second + "\n"
    assert ref_file.read_text() != first


# testing branches
def test_create_and_list_branches(tmp_path):
    m = make_manager(tmp_path)
    m.create_branch("feature1", "a" * 40)
    m.create_branch("feature2", "b" * 40)
    assert m.list_branches() == ["feature1", "feature2"]


def test_create_branch_twice_raises(tmp_path):
    m = make_manager(tmp_path)
    m.create_branch("feature", "a" * 40)
    with pytest.raises(RefExistsError):
        m.create_branch("feature", "b" * 40)


def test_switch_branch_unknown_raises(tmp_path):
    m = make_manager(tmp_path)
    with pytest.raises(RefNotFoundError):
        m.switch_branch("nope")


def test_switch_branch_updates_current_branch(tmp_path):
    m = make_manager(tmp_path)
    m.create_branch("feature", "a" * 40)
    m.switch_branch("feature")
    assert m._current_branch() == "feature"


def test_branches_point_at_different_hashes_after_switch(tmp_path):
    m = make_manager(tmp_path)
    first = m.create_commit("a" * 40, [], "Daniel <d@example.com>", "on main")
    m.create_branch("feature", first)
    m.switch_branch("feature")
    second = m.create_commit("b" * 40, [first], "Daniel <d@example.com>", "on feature")
    m.switch_branch("main")
    main_ref = tmp_path / ".minigit" / "refs" / "heads" / "main"
    feature_ref = tmp_path / ".minigit" / "refs" / "heads" / "feature"
    assert main_ref.read_text() == first + "\n"
    assert feature_ref.read_text() == second + "\n"
    assert main_ref.read_text() != feature_ref.read_text()


# testing log
def test_log_has_one_line_per_commit_newest_first(tmp_path):
    m = make_manager(tmp_path)
    first = m.create_commit("a" * 40, [], "Daniel <d@example.com>", "first")
    m.create_commit("b" * 40, [first], "Daniel <d@example.com>", "second")
    lines = m.log()
    assert len(lines) == 2
    assert "second" in lines[0]
    assert "first" in lines[1]
