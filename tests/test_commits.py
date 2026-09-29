# Run to test: scripts/test.sh tests/test_commits.py
# Testing for Module 3

from pathlib import Path

import pytest

from minigit.commits import CommitManager
from minigit.errors import (
    MiniGitError,
    ObjectCorruptError,
    ObjectNotFoundError,
    RefExistsError,
    RefNotFoundError,
)
from minigit.index import WorkingTree
from minigit.objects import ObjectStore

AUTHOR = "Daniel <d@example.com>"


class FakeWorkingTree:
    """Minimal stand-in for WorkingTree. No filesystem operations."""

    def __init__(self, store, index_tree=None, fail=False):
        self.store = store
        self.index_tree = index_tree
        self.fail = fail
        self.checked_out = []

    def build_tree_from_index(self) -> str:
        """
        Return the configured staged tree hash defaulting to empty
        """
        if self.index_tree is None:
            return self.store.hash_object(b"", "tree")
        return self.index_tree

    def checkout(self, tree_hash: str) -> None:
        "record checkout"
        if self.fail:
            raise MiniGitError("checkout failed")
        self.checked_out.append(tree_hash)


def make_manager(temp_path, tree=None):
    """Return CommitManager with real store and fake workign tree"""
    store = ObjectStore(temp_path)
    if tree is None:
        tree = FakeWorkingTree(store)
    return CommitManager(repo_path=str(temp_path), store=store, tree=tree)


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
    commit = m.create_commit(make_tree(m), [], AUTHOR, "init")
    m.create_branch("feature", commit)
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


@pytest.mark.parametrize(
    "header",
    [
        "tree invalid\nauthor Test 1\ncommitter Test 1",
        f"tree {'a' * 40}\nparent invalid\nauthor Test 1\ncommitter Test 1",
        f"tree {'a' * 40}\nauthor Test\ncommitter Test 1",
        f"tree {'a' * 40}\nauthor Test 1\ncommitter Test invalid",
        f"tree {'a' * 40}\nauthor Test 1\ncommitter Test 1\nextra header",
    ],
)
def test_read_commit_rejects_invalid_headers(tmp_path, header):
    manager = make_manager(tmp_path)
    obj_hash = manager.store.write_object((header + "\n\nmessage").encode(), "commit")
    with pytest.raises(ObjectCorruptError):
        manager.read_commit(obj_hash)


# week 4 helpers


def count_object_files(temp_path) -> int:
    """Return how many objects sit on disk, to prove no new commit was written."""
    objects_dir = temp_path / ".minigit" / "objects"
    return len([p for p in objects_dir.rglob("*") if p.is_file()])


def setup_fast_forward(m):
    """Leave 'main' at commit A and 'feature' at its child B, with HEAD on main.

    Moves HEAD with write_head rather than switch_branch, so merge tests do not
    depend on checkout behaviour while setting themselves up.
    """
    tree = make_tree(m)
    a = m.create_commit(tree, [], AUTHOR, "a")
    m.create_branch("feature", a)
    m.write_head("feature")
    b = m.create_commit(tree, [a], AUTHOR, "b")
    m.write_head("main")
    return a, b


def make_real_manager(temp_path):
    """Return a CommitManager wired to a real WorkingTree in an initialized repo."""
    metadata_dir = temp_path / ".minigit"
    (metadata_dir / "objects").mkdir(parents=True)
    (metadata_dir / "refs" / "heads").mkdir(parents=True)
    (metadata_dir / "index").write_bytes(b"")
    (metadata_dir / "HEAD").write_text("ref: refs/heads/main\n")
    store = ObjectStore(temp_path)
    return CommitManager(
        repo_path=str(temp_path), store=store, tree=WorkingTree(str(temp_path), store=store)
    )


def commit_file(m, path, contents, message):
    """Write `contents` to `path`, stage it, and commit on the current branch."""
    (Path(m.root) / path).write_text(contents)
    m.tree.stage_file(path)
    parent = m.read_ref(m._current_branch())
    parents = [parent] if parent else []
    return m.create_commit(m.tree.build_tree_from_index(), parents, AUTHOR, message)


# testing is_ancestor


def test_is_ancestor_of_itself(tmp_path):
    """A commit counts as its own ancestor."""
    m = make_manager(tmp_path)
    a = m.create_commit(make_tree(m), [], AUTHOR, "a")
    assert m.is_ancestor(a, a) is True


def test_is_ancestor_linear_history(tmp_path):
    """An older commit is an ancestor of a newer one, but not the reverse."""
    m = make_manager(tmp_path)
    tree = make_tree(m)
    a = m.create_commit(tree, [], AUTHOR, "a")
    b = m.create_commit(tree, [a], AUTHOR, "b")
    c = m.create_commit(tree, [b], AUTHOR, "c")
    assert m.is_ancestor(a, c) is True
    assert m.is_ancestor(c, a) is False


def test_is_ancestor_through_second_parent(tmp_path):
    """Ancestry follows every parent, not just the first."""
    m = make_manager(tmp_path)
    tree = make_tree(m)
    base = m.create_commit(tree, [], AUTHOR, "base")
    left = m.create_commit(tree, [base], AUTHOR, "left")
    right = m.create_commit(tree, [base], AUTHOR, "right")
    merge = m.create_commit(tree, [left, right], AUTHOR, "merge")
    assert m.is_ancestor(right, merge) is True
    assert m.is_ancestor(base, merge) is True
    assert m.is_ancestor(merge, left) is False


def test_is_ancestor_unrelated_branches(tmp_path):
    """Two root commits are not ancestors of each other."""
    m = make_manager(tmp_path)
    tree = make_tree(m)
    a = m.create_commit(tree, [], AUTHOR, "a")
    b = m.create_commit(tree, [], AUTHOR, "b")
    assert m.is_ancestor(a, b) is False
    assert m.is_ancestor(b, a) is False


# testing switch_branch checkout


def test_switch_branch_checks_out_target_tree(tmp_path):
    """Switching restores the target commit's tree and then moves HEAD."""
    m = make_manager(tmp_path)
    tree = make_tree(m)
    a = m.create_commit(tree, [], AUTHOR, "a")
    m.create_branch("feature", a)
    m.switch_branch("feature")
    assert m.tree.checked_out == [tree]
    assert m.read_head() == "feature"


def test_switch_branch_with_staged_changes_leaves_head_and_refs(tmp_path):
    """Staged changes raise MiniGitError before anything is touched."""
    m = make_manager(tmp_path)
    a = m.create_commit(make_tree(m), [], AUTHOR, "a")
    m.create_branch("feature", a)
    m.write_head("main")
    m.tree.index_tree = m.store.hash_object(b"staged", "tree")
    with pytest.raises(MiniGitError):
        m.switch_branch("feature")
    assert m.read_head() == "main"
    assert m.read_ref("main") == a
    assert m.read_ref("feature") == a
    assert m.tree.checked_out == []


def test_switch_branch_failed_checkout_leaves_head_and_refs(tmp_path):
    """A checkout that raises leaves HEAD and every ref where they were."""
    m = make_manager(tmp_path)
    a = m.create_commit(make_tree(m), [], AUTHOR, "a")
    m.create_branch("feature", a)
    m.write_head("main")
    m.tree.fail = True
    with pytest.raises(MiniGitError):
        m.switch_branch("feature")
    assert m.read_head() == "main"
    assert m.read_ref("main") == a
    assert m.read_ref("feature") == a


# testing merge fast-forward


def test_merge_missing_branch_raises(tmp_path):
    """Merging an unknown branch raises RefNotFoundError."""
    m = make_manager(tmp_path)
    m.create_commit(make_tree(m), [], AUTHOR, "a")
    with pytest.raises(RefNotFoundError):
        m.merge("nope")


def test_merge_fast_forward_moves_current_ref_without_a_commit(tmp_path):
    """A fast-forward moves the current ref to the target and writes no commit."""
    m = make_manager(tmp_path)
    a, b = setup_fast_forward(m)
    objects_before = count_object_files(tmp_path)
    assert m.merge("feature") is None
    assert m.read_ref("main") == b
    assert m.read_ref("feature") == b
    assert count_object_files(tmp_path) == objects_before
    assert m.read_ref("main") != a


def test_merge_fast_forward_checks_out_target_tree(tmp_path):
    """A fast-forward restores the target commit's tree and keeps HEAD in place."""
    m = make_manager(tmp_path)
    _, b = setup_fast_forward(m)
    m.merge("feature")
    assert m.tree.checked_out == [m.read_commit(b).tree]
    assert m.read_head() == "main"


def test_merge_same_tip_changes_nothing(tmp_path):
    """Merging a branch that points at the current tip is a no-op."""
    m = make_manager(tmp_path)
    a = m.create_commit(make_tree(m), [], AUTHOR, "a")
    m.create_branch("feature", a)
    m.write_head("main")
    assert m.merge("feature") is None
    assert m.read_ref("main") == a
    assert m.read_ref("feature") == a
    assert m.read_head() == "main"
    assert m.tree.checked_out == []


def test_merge_already_merged_target_changes_nothing(tmp_path):
    """Merging an ancestor of the current tip is a no-op."""
    m = make_manager(tmp_path)
    tree = make_tree(m)
    a = m.create_commit(tree, [], AUTHOR, "a")
    m.create_branch("feature", a)
    b = m.create_commit(tree, [a], AUTHOR, "b")
    assert m.merge("feature") is None
    assert m.read_ref("main") == b
    assert m.read_ref("feature") == a
    assert m.tree.checked_out == []


def test_merge_into_unborn_branch_creates_the_ref(tmp_path):
    """An unborn current branch fast-forwards to the target and keeps HEAD."""
    m = make_manager(tmp_path)
    tree = make_tree(m)
    a = m.create_commit(tree, [], AUTHOR, "a")
    m.create_branch("feature", a)
    (tmp_path / ".minigit" / "refs" / "heads" / "main").unlink()
    assert m.merge("feature") is None
    assert m.read_ref("main") == a
    assert m._current_branch() == "main"
    assert m.tree.checked_out == [tree]


def test_merge_diverged_history_raises_and_changes_nothing(tmp_path):
    """Diverged histories raise MiniGitError and leave both refs and HEAD alone."""
    m = make_manager(tmp_path)
    tree = make_tree(m)
    a = m.create_commit(tree, [], AUTHOR, "a")
    m.create_branch("feature", a)
    m.write_head("feature")
    b = m.create_commit(tree, [a], AUTHOR, "b")
    m.write_head("main")
    c = m.create_commit(tree, [a], AUTHOR, "c")
    with pytest.raises(MiniGitError, match="three-way merge is not implemented yet"):
        m.merge("feature")
    assert m.read_ref("main") == c
    assert m.read_ref("feature") == b
    assert m.read_head() == "main"
    assert m.tree.checked_out == []


def test_merge_with_staged_changes_leaves_refs_unchanged(tmp_path):
    """Staged changes block a fast-forward and leave the refs where they were."""
    m = make_manager(tmp_path)
    a, b = setup_fast_forward(m)
    m.tree.index_tree = m.store.hash_object(b"staged", "tree")
    with pytest.raises(MiniGitError):
        m.merge("feature")
    assert m.read_ref("main") == a
    assert m.read_ref("feature") == b
    assert m.read_head() == "main"
    assert m.tree.checked_out == []


def test_merge_failed_checkout_leaves_refs_unchanged(tmp_path):
    """A checkout that raises during a fast-forward leaves the current ref alone."""
    m = make_manager(tmp_path)
    a, b = setup_fast_forward(m)
    m.tree.fail = True
    with pytest.raises(MiniGitError):
        m.merge("feature")
    assert m.read_ref("main") == a
    assert m.read_ref("feature") == b
    assert m.read_head() == "main"


# testing against the real working tree (needs M2 checkout, issue #23)


@pytest.mark.xfail(reason="needs #23: WorkingTree.checkout is still a stub", strict=False)
def test_switch_between_real_snapshots(tmp_path):
    """Switching between two committed snapshots restores files, index, and HEAD."""
    m = make_real_manager(tmp_path)
    a = commit_file(m, "file.txt", "A\n", "A")
    m.create_branch("feature", a)
    m.switch_branch("feature")
    b = commit_file(m, "file.txt", "B\n", "B")
    m.switch_branch("main")
    assert (tmp_path / "file.txt").read_text() == "A\n"
    assert [e.path for e in m.tree.read_index()] == ["file.txt"]
    assert m.tree.read_index()[0].hash == m.store.hash_object(b"A\n", "blob")
    assert m.read_head() == "main"
    assert m.read_ref("main") == a
    assert m.read_ref("feature") == b


@pytest.mark.xfail(reason="needs #23: WorkingTree.checkout is still a stub", strict=False)
def test_real_fast_forward_restores_target_files(tmp_path):
    """The team checkpoint: a fast-forward brings the files and both refs to B."""
    m = make_real_manager(tmp_path)
    a = commit_file(m, "file.txt", "A\n", "A")
    m.create_branch("feature", a)
    m.switch_branch("feature")
    b = commit_file(m, "file.txt", "B\n", "B")
    m.switch_branch("main")
    objects_before = count_object_files(tmp_path)
    assert m.merge("feature") is None
    assert (tmp_path / "file.txt").read_text() == "B\n"
    assert m.read_ref("main") == b
    assert m.read_ref("feature") == b
    assert m.read_head() == "main"
    assert count_object_files(tmp_path) == objects_before
    assert a != b
