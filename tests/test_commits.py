# Run to test: scripts/test.sh tests/test_commits.py
# Testing for Module 3

import pytest

from minigit.commits import CommitManager
from minigit.errors import ObjectCorruptError, ObjectNotFoundError, RefExistsError, RefNotFoundError
from minigit.objects import ObjectStore

AUTHOR = "Daniel <d@example.com>"


class FakeWorkingTree:
    """Minimal stand-in for WorkingTree. No filesystem operations."""

    def checkout(self, tree_hash: str) -> None:
        """checkout call"""


def make_manager(temp_path):
    """Return CommitManager for testing"""
    return CommitManager(
        repo_path=str(temp_path), store=ObjectStore(temp_path), tree=FakeWorkingTree()
    )


def make_tree(m) -> str:
    """Write a real (empty) tree object so create_commit's validation passes."""
    return m.store.write_object(b"", "tree")


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
    result = m.create_commit(make_tree(m), [], AUTHOR, "init")
    assert len(result) == 40


def test_first_commit_has_no_parent_lines(tmp_path):
    m = make_manager(tmp_path)
    commit_hash = m.create_commit(make_tree(m), [], AUTHOR, "init")
    _, body = m.store.read_object(commit_hash)
    assert "parent" not in body.decode()


def test_first_commit_creates_ref_file(tmp_path):
    m = make_manager(tmp_path)
    m.create_commit(make_tree(m), [], AUTHOR, "init")
    ref_file = tmp_path / ".minigit" / "refs" / "heads" / "main"
    assert ref_file.exists()


def test_second_commit_has_one_parent_line_pointing_at_first(tmp_path):
    m = make_manager(tmp_path)
    tree = make_tree(m)
    first = m.create_commit(tree, [], AUTHOR, "init")
    second = m.create_commit(tree, [first], AUTHOR, "second")
    _, body = m.store.read_object(second)
    parent_lines = [line for line in body.decode().splitlines() if line.startswith("parent ")]
    assert len(parent_lines) == 1
    assert first in parent_lines[0]


def test_second_commit_moves_the_ref_file(tmp_path):
    m = make_manager(tmp_path)
    tree = make_tree(m)
    first = m.create_commit(tree, [], AUTHOR, "init")
    second = m.create_commit(tree, [first], AUTHOR, "second")
    ref_file = tmp_path / ".minigit" / "refs" / "heads" / "main"
    assert ref_file.read_text() == second + "\n"
    assert ref_file.read_text() != first


def test_create_commit_rejects_non_tree_and_leaves_ref_unchanged(tmp_path):
    m = make_manager(tmp_path)
    first = m.create_commit(make_tree(m), [], AUTHOR, "init")
    blob = m.store.write_object(b"hello", "blob")
    with pytest.raises(ObjectCorruptError):
        m.create_commit(blob, [first], AUTHOR, "bad")
    assert m.read_ref("main") == first


def test_create_commit_missing_tree_raises(tmp_path):
    m = make_manager(tmp_path)
    with pytest.raises(ObjectNotFoundError):
        m.create_commit("f" * 40, [], AUTHOR, "bad")


# testing read_commit
def test_read_commit_round_trips_fields(tmp_path):
    m = make_manager(tmp_path)
    tree = make_tree(m)
    h = m.create_commit(tree, [], AUTHOR, "init")
    c = m.read_commit(h)
    assert c.tree == tree
    assert c.parents == []
    assert c.author == AUTHOR
    assert c.committer == AUTHOR
    assert c.message == "init"


def test_read_commit_keeps_all_parents_in_order(tmp_path):
    m = make_manager(tmp_path)
    tree = make_tree(m)
    p1 = m.create_commit(tree, [], AUTHOR, "one")
    p2 = m.create_commit(tree, [], AUTHOR, "two")
    merge = m.create_commit(tree, [p1, p2], AUTHOR, "merge")
    assert m.read_commit(merge).parents == [p1, p2]


def test_read_commit_preserves_multiline_message(tmp_path):
    m = make_manager(tmp_path)
    msg = "summary\n\nlonger body\nwith two lines"
    h = m.create_commit(make_tree(m), [], AUTHOR, msg)
    assert m.read_commit(h).message == msg


def test_read_commit_wrong_type_raises(tmp_path):
    m = make_manager(tmp_path)
    blob = m.store.write_object(b"hello", "blob")
    with pytest.raises(ObjectCorruptError):
        m.read_commit(blob)


def test_read_commit_malformed_body_raises(tmp_path):
    m = make_manager(tmp_path)
    bad = m.store.write_object(b"not a real commit", "commit")
    with pytest.raises(ObjectCorruptError):
        m.read_commit(bad)


# testing get_head_tree
def test_get_head_tree_unborn_branch_returns_none(tmp_path):
    m = make_manager(tmp_path)
    assert m.get_head_tree() is None


def test_get_head_tree_after_commit(tmp_path):
    m = make_manager(tmp_path)
    tree = make_tree(m)
    m.create_commit(tree, [], AUTHOR, "init")
    assert m.get_head_tree() == tree


def test_get_head_tree_detached_head(tmp_path):
    m = make_manager(tmp_path)
    tree = make_tree(m)
    h = m.create_commit(tree, [], AUTHOR, "init")
    (tmp_path / ".minigit" / "HEAD").write_text(h + "\n")
    assert m.get_head_tree() == tree


def test_get_head_tree_missing_commit_raises(tmp_path):
    m = make_manager(tmp_path)
    m.write_ref("main", "f" * 40)
    with pytest.raises(ObjectNotFoundError):
        m.get_head_tree()


# testing walk_history
def test_walk_history_linear_newest_first(tmp_path):
    m = make_manager(tmp_path)
    tree = make_tree(m)
    a = m.create_commit(tree, [], AUTHOR, "a")
    b = m.create_commit(tree, [a], AUTHOR, "b")
    c = m.create_commit(tree, [b], AUTHOR, "c")
    assert m.walk_history(c) == [c, b, a]


def test_walk_history_merge_visits_both_sides_once(tmp_path):
    m = make_manager(tmp_path)
    tree = make_tree(m)
    base = m.create_commit(tree, [], AUTHOR, "base")
    left = m.create_commit(tree, [base], AUTHOR, "left")
    right = m.create_commit(tree, [base], AUTHOR, "right")
    merge = m.create_commit(tree, [left, right], AUTHOR, "merge")
    result = m.walk_history(merge)
    assert result == [merge, left, base, right]
    assert len(result) == len(set(result))


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
    tree = make_tree(m)
    first = m.create_commit(tree, [], AUTHOR, "on main")
    m.create_branch("feature", first)
    m.switch_branch("feature")
    second = m.create_commit(tree, [first], AUTHOR, "on feature")
    m.switch_branch("main")
    main_ref = tmp_path / ".minigit" / "refs" / "heads" / "main"
    feature_ref = tmp_path / ".minigit" / "refs" / "heads" / "feature"
    assert main_ref.read_text() == first + "\n"
    assert feature_ref.read_text() == second + "\n"
    assert main_ref.read_text() != feature_ref.read_text()


# testing log
def test_log_has_one_line_per_commit_newest_first(tmp_path):
    m = make_manager(tmp_path)
    tree = make_tree(m)
    first = m.create_commit(tree, [], AUTHOR, "first")
    m.create_commit(tree, [first], AUTHOR, "second")
    lines = m.log()
    assert len(lines) == 2
    assert "second" in lines[0]
    assert "first" in lines[1]


def test_log_shows_both_sides_of_merge_without_duplicates(tmp_path):
    m = make_manager(tmp_path)
    tree = make_tree(m)
    base = m.create_commit(tree, [], AUTHOR, "base")
    left = m.create_commit(tree, [base], AUTHOR, "left")
    right = m.create_commit(tree, [base], AUTHOR, "right")
    m.create_commit(tree, [left, right], AUTHOR, "merge")
    lines = m.log()
    assert len(lines) == 4
    assert sum("base" in line for line in lines) == 1


def test_fresh_manager_reads_same_history(tmp_path):
    m1 = make_manager(tmp_path)
    tree = make_tree(m1)
    a = m1.create_commit(tree, [], AUTHOR, "a")
    m1.create_commit(tree, [a], AUTHOR, "b")
    m2 = make_manager(tmp_path)
    assert m2.log() == m1.log()
