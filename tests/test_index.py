"""Tests for minigit/index.py - WorkingTree, trees, diffing, status, checkout, Myers diff,
and pull / merge-result handling."""

import argparse
import hashlib
import os
import random
import shutil
import sys
import types

import pytest

from minigit.commits import CommitManager
from minigit.errors import MiniGitError, ObjectNotFoundError
from minigit.index import (
    LineEdit,
    WorkingTree,
    apply_edits,
    cmd_add,
    cmd_diff,
    cmd_status,
    myers_diff,
    split_lines,
)
from minigit.objects import ObjectStore, TreeEntry


def test_build_tree_empty_index(tmp_path):
    store = ObjectStore(str(tmp_path))
    wt = WorkingTree(repo_path=str(tmp_path), store=store)

    tree_hash = wt.build_tree_from_index()

    obj_type, data = store.read_object(tree_hash)
    assert obj_type == "tree"
    assert data == b""


def test_build_tree_nested_directories(tmp_path):
    (tmp_path / "a.txt").write_text("hello")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "b.txt").write_text("world")

    store = ObjectStore(str(tmp_path))
    wt = WorkingTree(repo_path=str(tmp_path), store=store)
    wt.stage_file("a.txt")
    wt.stage_file("src/b.txt")

    tree_hash = wt.build_tree_from_index()
    entries = wt.read_tree_entries(tree_hash)

    assert len(entries) == 2
    paths = [e.path for e in entries]
    assert "a.txt" in paths
    assert "src/b.txt" in paths


def test_build_tree_deterministic(tmp_path):
    (tmp_path / "a.txt").write_text("hello")
    store = ObjectStore(str(tmp_path))
    wt = WorkingTree(repo_path=str(tmp_path), store=store)
    wt.stage_file("a.txt")

    hash1 = wt.build_tree_from_index()
    hash2 = wt.build_tree_from_index()

    assert hash1 == hash2


def test_build_tree_path_with_space(tmp_path):
    (tmp_path / "my notes.txt").write_text("hi")
    store = ObjectStore(str(tmp_path))
    wt = WorkingTree(repo_path=str(tmp_path), store=store)
    wt.stage_file("my notes.txt")

    tree_hash = wt.build_tree_from_index()
    entries = wt.read_tree_entries(tree_hash)

    assert len(entries) == 1
    assert entries[0].path == "my notes.txt"


def test_build_tree_executable_mode_preserved(tmp_path):
    file = tmp_path / "run.sh"
    file.write_text("#!/bin/sh\necho hi")
    file.chmod(0o755)

    store = ObjectStore(str(tmp_path))
    wt = WorkingTree(repo_path=str(tmp_path), store=store)
    wt.stage_file("run.sh")

    tree_hash = wt.build_tree_from_index()
    entries = wt.read_tree_entries(tree_hash)

    assert len(entries) == 1
    assert entries[0].mode == "100755"


def test_read_tree_entries_sorted(tmp_path):
    (tmp_path / "z.txt").write_text("z")
    (tmp_path / "a.txt").write_text("a")

    store = ObjectStore(str(tmp_path))
    wt = WorkingTree(repo_path=str(tmp_path), store=store)
    wt.stage_file("z.txt")
    wt.stage_file("a.txt")

    tree_hash = wt.build_tree_from_index()
    entries = wt.read_tree_entries(tree_hash)

    assert entries[0].path == "a.txt"
    assert entries[1].path == "z.txt"


def test_diff_detects_modified_after_staging(tmp_path):
    file = tmp_path / "hello.txt"
    file.write_text("original")

    store = ObjectStore(str(tmp_path))
    wt = WorkingTree(repo_path=str(tmp_path), store=store)
    wt.stage_file("hello.txt")

    tree_hash = wt.build_tree_from_index()

    file.write_text("changed!!!")

    diff = wt.diff_working_tree_vs(tree_hash)
    assert "hello.txt" in diff.modified
    assert "hello.txt" not in diff.added
    assert "hello.txt" not in diff.deleted


def test_diff_detects_deleted(tmp_path):
    file = tmp_path / "hello.txt"
    file.write_text("hi")

    store = ObjectStore(str(tmp_path))
    wt = WorkingTree(repo_path=str(tmp_path), store=store)
    wt.stage_file("hello.txt")

    tree_hash = wt.build_tree_from_index()
    file.unlink()

    diff = wt.diff_working_tree_vs(tree_hash)
    assert "hello.txt" in diff.deleted


def test_diff_detects_added(tmp_path):
    (tmp_path / "hello.txt").write_text("hi")

    store = ObjectStore(str(tmp_path))
    wt = WorkingTree(repo_path=str(tmp_path), store=store)
    wt.stage_file("hello.txt")
    tree_hash = wt.build_tree_from_index()

    (tmp_path / "extra.txt").write_text("new file, never staged")

    diff = wt.diff_working_tree_vs(tree_hash)
    assert "extra.txt" in diff.added
    assert "hello.txt" not in diff.added


def test_diff_excludes_minigit_folder(tmp_path):
    (tmp_path / "hello.txt").write_text("hi")

    store = ObjectStore(str(tmp_path))
    wt = WorkingTree(repo_path=str(tmp_path), store=store)
    wt.stage_file("hello.txt")
    tree_hash = wt.build_tree_from_index()

    diff = wt.diff_working_tree_vs(tree_hash)
    assert not any(p.startswith(".minigit") for p in diff.added)


def test_diff_no_changes_reports_nothing(tmp_path):
    (tmp_path / "hello.txt").write_text("hi")

    store = ObjectStore(str(tmp_path))
    wt = WorkingTree(repo_path=str(tmp_path), store=store)
    wt.stage_file("hello.txt")
    tree_hash = wt.build_tree_from_index()

    diff = wt.diff_working_tree_vs(tree_hash)
    assert diff.added == []
    assert diff.deleted == []
    assert diff.modified == []


# --- cmd_status against real commit objects ---


class _Args:
    """Minimal stand-in for argparse's Namespace, since cmd_status(args)
    doesn't actually read any attributes off args."""


def test_status_clean_after_commit(tmp_path, monkeypatch, capsys):
    (tmp_path / "hello.txt").write_text("hi")

    store = ObjectStore(str(tmp_path))
    wt = WorkingTree(repo_path=str(tmp_path), store=store)
    wt.stage_file("hello.txt")
    tree_hash = wt.build_tree_from_index()

    monkeypatch.chdir(tmp_path)
    CommitManager(tmp_path).create_commit(tree_hash, [], "Test", "initial")

    cmd_status(_Args())

    output = capsys.readouterr().out
    assert output.strip() == "clean"


def test_status_staged_and_unstaged_on_one_file(tmp_path, monkeypatch, capsys):
    file = tmp_path / "hello.txt"
    file.write_text("v1")

    store = ObjectStore(str(tmp_path))
    wt = WorkingTree(repo_path=str(tmp_path), store=store)
    wt.stage_file("hello.txt")
    committed_tree_hash = wt.build_tree_from_index()  # simulates "last commit"

    # stage a change (now differs from the committed tree -> staged)
    file.write_text("v2")
    wt.stage_file("hello.txt")

    # edit again without re-staging (now differs from index -> not staged)
    file.write_text("v3")

    monkeypatch.chdir(tmp_path)
    CommitManager(tmp_path).create_commit(committed_tree_hash, [], "Test", "initial")

    cmd_status(_Args())

    output = capsys.readouterr().out
    assert "staged:" in output
    assert "not staged:" in output
    assert "hello.txt" in output


def test_status_unborn_head_shows_all_staged(tmp_path, monkeypatch, capsys):
    (tmp_path / "hello.txt").write_text("hi")

    store = ObjectStore(str(tmp_path))
    wt = WorkingTree(repo_path=str(tmp_path), store=store)
    wt.stage_file("hello.txt")

    monkeypatch.chdir(tmp_path)

    cmd_status(_Args())

    output = capsys.readouterr().out
    assert "staged:" in output
    assert "hello.txt" in output


def test_status_and_diff_do_not_store_unstaged_content(tmp_path, monkeypatch, capsys):
    file = tmp_path / "file"
    file.write_text("committed")
    wt = WorkingTree(tmp_path)
    wt.stage_file("file")
    tree = wt.build_tree_from_index()
    CommitManager(tmp_path).create_commit(tree, [], "Test", "initial")
    before = set(wt.store.objects_dir.rglob("*"))
    file.write_text("unstaged")
    monkeypatch.chdir(tmp_path)
    cmd_status(_Args())
    assert capsys.readouterr().out == "not staged:\n  file\n"
    assert wt.diff_working_tree_vs(tree).modified == ["file"]
    assert set(wt.store.objects_dir.rglob("*")) == before


def test_status_reports_file_removed_from_index_as_untracked(tmp_path, monkeypatch, capsys):
    (tmp_path / "file").write_text("content")
    wt = WorkingTree(tmp_path)
    wt.stage_file("file")
    CommitManager(tmp_path).create_commit(wt.build_tree_from_index(), [], "Test", "initial")
    wt.write_index([])
    monkeypatch.chdir(tmp_path)
    cmd_status(_Args())
    assert capsys.readouterr().out == "staged:\n  file\nuntracked:\n  file\n"


def test_checkout_restores_changed_file(tmp_path):
    store = ObjectStore(str(tmp_path))
    wt = WorkingTree(repo_path=str(tmp_path), store=store)
    (tmp_path / "hello.txt").write_text("v1")
    wt.stage_file("hello.txt")
    tree_hash = wt.build_tree_from_index()

    (tmp_path / "hello.txt").write_text("v1")  # revert to clean state first
    wt.stage_file("hello.txt")

    wt.checkout(tree_hash)
    assert (tmp_path / "hello.txt").read_text() == "v1"


def test_checkout_nested_paths_and_spaces(tmp_path):
    store = ObjectStore(str(tmp_path))
    wt = WorkingTree(repo_path=str(tmp_path), store=store)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "my notes.txt").write_text("hi")
    wt.stage_file("src/my notes.txt")
    tree_hash = wt.build_tree_from_index()

    (tmp_path / "src" / "my notes.txt").unlink()
    (tmp_path / "src" / "my notes.txt").write_text("hi")  # re-stage clean copy
    wt.stage_file("src/my notes.txt")

    wt.checkout(tree_hash)
    assert (tmp_path / "src" / "my notes.txt").read_text() == "hi"


def test_checkout_restores_binary_bytes(tmp_path):
    store = ObjectStore(str(tmp_path))
    wt = WorkingTree(repo_path=str(tmp_path), store=store)
    (tmp_path / "data.bin").write_bytes(b"\x00\x01\xff\xfe")
    wt.stage_file("data.bin")
    tree_hash = wt.build_tree_from_index()

    wt.checkout(tree_hash)
    assert (tmp_path / "data.bin").read_bytes() == b"\x00\x01\xff\xfe"


def test_checkout_restores_executable_mode(tmp_path):
    store = ObjectStore(str(tmp_path))
    wt = WorkingTree(repo_path=str(tmp_path), store=store)
    file = tmp_path / "run.sh"
    file.write_text("echo hi")
    file.chmod(0o755)
    wt.stage_file("run.sh")
    tree_hash = wt.build_tree_from_index()

    file.chmod(0o644)  # revert manually
    wt.stage_file("run.sh")  # re-stage as-is (still 644 on disk now)

    wt.checkout(tree_hash)
    assert os.access(tmp_path / "run.sh", os.X_OK)


def test_checkout_removes_tracked_file_not_in_target(tmp_path):
    store = ObjectStore(str(tmp_path))
    wt = WorkingTree(repo_path=str(tmp_path), store=store)
    (tmp_path / "keep.txt").write_text("keep")
    (tmp_path / "gone.txt").write_text("gone")
    wt.stage_file("keep.txt")
    empty_target = wt.build_tree_from_index()  # tree with just keep.txt

    wt.stage_file("gone.txt")  # now index has both, target only has keep.txt

    wt.checkout(empty_target)
    assert (tmp_path / "keep.txt").exists()
    assert not (tmp_path / "gone.txt").exists()


def test_checkout_preserves_untracked_file(tmp_path):
    store = ObjectStore(str(tmp_path))
    wt = WorkingTree(repo_path=str(tmp_path), store=store)
    (tmp_path / "tracked.txt").write_text("hi")
    wt.stage_file("tracked.txt")
    tree_hash = wt.build_tree_from_index()

    (tmp_path / "untracked.txt").write_text("leave me alone")

    wt.checkout(tree_hash)
    assert (tmp_path / "untracked.txt").read_text() == "leave me alone"


def test_checkout_empty_tree_clears_index(tmp_path):
    store = ObjectStore(str(tmp_path))
    wt = WorkingTree(repo_path=str(tmp_path), store=store)
    empty_hash = wt.build_tree_from_index()  # nothing staged -> empty tree

    wt.checkout(empty_hash)
    assert wt.read_index() == []


def test_checkout_fails_on_local_edit_before_changing_anything(tmp_path):
    store = ObjectStore(str(tmp_path))
    wt = WorkingTree(repo_path=str(tmp_path), store=store)
    file = tmp_path / "hello.txt"
    file.write_text("original")
    wt.stage_file("hello.txt")
    tree_hash = wt.build_tree_from_index()

    file.write_text("uncommitted edit")  # local edit, not re-staged

    with pytest.raises(MiniGitError):
        wt.checkout(tree_hash)
    assert file.read_text() == "uncommitted edit"  # untouched


def test_checkout_fails_on_untracked_path_blocking_target(tmp_path):
    store = ObjectStore(str(tmp_path))
    wt = WorkingTree(repo_path=str(tmp_path), store=store)
    (tmp_path / "hello.txt").write_text("hi")
    wt.stage_file("hello.txt")

    wt2 = WorkingTree(repo_path=str(tmp_path), store=store)
    (tmp_path / "new.txt").write_text("staged elsewhere")
    wt2.stage_file("new.txt")
    tree_with_new = wt2.build_tree_from_index()

    # simulate an untracked file blocking the checkout target
    wt3 = WorkingTree(repo_path=str(tmp_path), store=store)
    wt3.write_index([e for e in wt3.read_index() if e.path != "new.txt"])

    with pytest.raises(MiniGitError):
        wt3.checkout(tree_with_new)


def test_checkout_is_idempotent(tmp_path):
    store = ObjectStore(str(tmp_path))
    wt = WorkingTree(repo_path=str(tmp_path), store=store)
    (tmp_path / "hello.txt").write_text("hi")
    wt.stage_file("hello.txt")
    tree_hash = wt.build_tree_from_index()

    wt.checkout(tree_hash)
    first_index = wt.read_index()
    wt.checkout(tree_hash)
    second_index = wt.read_index()

    assert first_index == second_index
    assert (tmp_path / "hello.txt").read_text() == "hi"


# ---------------------------------------------------------------------------
# Myers: pure function tests
# ---------------------------------------------------------------------------


def lcs_length(a, b):
    table = [[0] * (len(b) + 1) for _ in range(len(a) + 1)]
    for i in range(len(a) - 1, -1, -1):
        for j in range(len(b) - 1, -1, -1):
            if a[i] == b[j]:
                table[i][j] = table[i + 1][j + 1] + 1
            else:
                table[i][j] = max(table[i + 1][j], table[i][j + 1])
    return table[0][0]


def edit_cost(edits):
    return sum(e.old_count + len(e.new_lines) for e in edits)


def test_identical_lists_have_no_edits():
    assert myers_diff(["a", "b", "c"], ["a", "b", "c"]) == []


def test_both_empty_have_no_edits():
    assert myers_diff([], []) == []


def test_pure_insert_uses_base_position():
    assert myers_diff(["a", "c"], ["a", "b", "c"]) == [LineEdit(1, 0, ["b"])]


def test_insert_at_start_and_end():
    assert myers_diff(["b"], ["a", "b"]) == [LineEdit(0, 0, ["a"])]
    assert myers_diff(["a"], ["a", "b"]) == [LineEdit(1, 0, ["b"])]


def test_pure_delete():
    assert myers_diff(["a", "b", "c"], ["a", "c"]) == [LineEdit(1, 1, [])]


def test_replace():
    assert myers_diff(["a", "b", "c"], ["a", "x", "c"]) == [LineEdit(1, 1, ["x"])]


def test_from_empty_and_to_empty():
    assert myers_diff([], ["a", "b"]) == [LineEdit(0, 0, ["a", "b"])]
    assert myers_diff(["a", "b"], []) == [LineEdit(0, 2, [])]


def test_repeated_lines():
    edits = myers_diff(["x", "x", "x"], ["x", "x"])
    assert edit_cost(edits) == 1
    assert apply_edits(["x", "x", "x"], edits) == ["x", "x"]


def test_two_separate_changes_stay_separate():
    old = ["a", "b", "c", "d", "e"]
    new = ["a", "B", "c", "d", "E"]
    assert myers_diff(old, new) == [LineEdit(1, 1, ["B"]), LineEdit(4, 1, ["E"])]


def test_missing_final_newline_is_a_change():
    old = split_lines("a\nb")
    new = split_lines("a\nb\n")
    assert myers_diff(old, new) == [LineEdit(1, 1, ["b\n"])]


def test_edits_are_sorted_and_do_not_overlap():
    random.seed(7)
    for _ in range(200):
        a = [random.choice("abc") for _ in range(random.randint(0, 8))]
        b = [random.choice("abc") for _ in range(random.randint(0, 8))]
        edits = myers_diff(a, b)
        for first, second in zip(edits, edits[1:], strict=False):
            assert first.start + first.old_count < second.start


def test_random_round_trip_and_minimal():
    random.seed(1)
    for _ in range(500):
        a = [random.choice("abc") for _ in range(random.randint(0, 9))]
        b = [random.choice("abc") for _ in range(random.randint(0, 9))]
        edits = myers_diff(a, b)
        assert apply_edits(a, edits) == b
        assert edit_cost(edits) == len(a) + len(b) - 2 * lcs_length(a, b)


def test_split_lines_keeps_newlines_and_only_splits_on_newline():
    assert split_lines("") == []
    assert split_lines("a\n") == ["a\n"]
    assert split_lines("a\nb") == ["a\n", "b"]
    assert split_lines("a\r\nb\n") == ["a\r\n", "b\n"]
    assert split_lines("a\x0cb\u2028c\n") == ["a\x0cb\u2028c\n"]
    assert split_lines("\n\n") == ["\n", "\n"]


# ---------------------------------------------------------------------------
# diff_working_tree_vs with line details
# ---------------------------------------------------------------------------


@pytest.fixture
def repo(tmp_path):
    store = ObjectStore(str(tmp_path))
    return WorkingTree(str(tmp_path), store=store)


def write(repo, name, content, mode=None):
    path = os.path.join(repo.root, name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    data = content if isinstance(content, bytes) else content.encode()
    with open(path, "wb") as f:
        f.write(data)
    if mode is not None:
        os.chmod(path, mode)
    return path


def snapshot_tree(repo, files):
    """Stage `files`, build the tree (this is our stand-in for HEAD), return its hash."""
    for name in files:
        repo.stage_file(name)
    return repo.build_tree_from_index()


def minigit_files(repo):
    found = []
    for dirpath, _, filenames in os.walk(os.path.join(repo.root, ".minigit")):
        for filename in filenames:
            full = os.path.join(dirpath, filename)
            found.append((os.path.relpath(full, repo.root), os.path.getmtime(full)))
    return sorted(found)


def test_modified_file_has_line_edits_and_name_lists(repo):
    write(repo, "a.txt", "one\ntwo\nthree\n")
    tree = snapshot_tree(repo, ["a.txt"])
    write(repo, "a.txt", "one\nTWO\nthree\n")

    result = repo.diff_working_tree_vs(tree)

    assert result.modified == ["a.txt"]
    assert result.added == []
    assert result.deleted == []
    assert result.line_diffs["a.txt"] == [LineEdit(1, 1, ["TWO\n"])]


def test_added_and_deleted_files_have_line_edits(repo):
    write(repo, "gone.txt", "x\ny\n")
    tree = snapshot_tree(repo, ["gone.txt"])
    os.remove(os.path.join(repo.root, "gone.txt"))
    write(repo, "new.txt", "p\nq\n")

    result = repo.diff_working_tree_vs(tree)

    assert result.deleted == ["gone.txt"]
    assert result.added == ["new.txt"]
    assert result.line_diffs["gone.txt"] == [LineEdit(0, 2, [])]
    assert result.line_diffs["new.txt"] == [LineEdit(0, 0, ["p\n", "q\n"])]


def test_unchanged_file_is_not_reported(repo):
    write(repo, "same.txt", "hello\n")
    tree = snapshot_tree(repo, ["same.txt"])

    result = repo.diff_working_tree_vs(tree)

    assert result.added == result.deleted == result.modified == []
    assert result.line_diffs == {}


def test_unborn_head_none_is_an_empty_tree(repo):
    write(repo, "a.txt", "hi\n")

    result = repo.diff_working_tree_vs(None)

    assert result.added == ["a.txt"]
    assert result.line_diffs["a.txt"] == [LineEdit(0, 0, ["hi\n"])]


def test_binary_and_undecodable_files_have_no_line_diff(repo):
    write(repo, "img.bin", b"\x00\x01\x02")
    write(repo, "latin.txt", b"caf\xe9\n")
    tree = snapshot_tree(repo, ["img.bin", "latin.txt"])
    write(repo, "img.bin", b"\x00\x01\x03")
    write(repo, "latin.txt", b"caf\xe8\n")

    result = repo.diff_working_tree_vs(tree)

    assert result.modified == ["img.bin", "latin.txt"]
    assert result.line_diffs["img.bin"] is None
    assert result.line_diffs["latin.txt"] is None


def test_unicode_text_is_diffed_as_text(repo):
    write(repo, "u.txt", "café\nnaïve\n")
    tree = snapshot_tree(repo, ["u.txt"])
    write(repo, "u.txt", "café\nnaïve!\n")

    result = repo.diff_working_tree_vs(tree)

    assert result.line_diffs["u.txt"] == [LineEdit(1, 1, ["naïve!\n"])]


def test_mode_only_change_is_modified_with_no_line_edits(repo):
    write(repo, "run.sh", "echo hi\n", mode=0o644)
    tree = snapshot_tree(repo, ["run.sh"])
    os.chmod(os.path.join(repo.root, "run.sh"), 0o755)

    result = repo.diff_working_tree_vs(tree)

    assert result.modified == ["run.sh"]
    assert result.line_diffs["run.sh"] == []


def test_diff_ignores_minigit_directory(repo):
    write(repo, "a.txt", "x\n")
    tree = snapshot_tree(repo, ["a.txt"])

    result = repo.diff_working_tree_vs(tree)

    assert not any(p.startswith(".minigit") for p in result.added)


def test_diff_writes_nothing_to_minigit(repo):
    write(repo, "a.txt", "one\n")
    tree = snapshot_tree(repo, ["a.txt"])
    write(repo, "a.txt", "two\n")
    write(repo, "b.txt", "new\n")
    before = minigit_files(repo)

    repo.diff_working_tree_vs(tree)

    assert minigit_files(repo) == before


# ---------------------------------------------------------------------------
# `minigit diff` and status (fake minigit.commits so these tests stand alone)
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_repo(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    repo = WorkingTree(".", store=ObjectStore("."))
    state = {"head_tree": None}

    class FakeCommitManager:
        def __init__(self, repo_path=".", store=None, tree=None):
            pass

        def get_head_tree(self):
            return state["head_tree"]

    fake = types.ModuleType("minigit.commits")
    fake.CommitManager = FakeCommitManager
    monkeypatch.setitem(sys.modules, "minigit.commits", fake)

    def commit(*files):
        state["head_tree"] = snapshot_tree(repo, files)

    return repo, commit


def run_diff(capsys):
    assert cmd_diff(None) == 0
    return capsys.readouterr().out


def test_cli_clean_tree_prints_nothing(cli_repo, capsys):
    repo, commit = cli_repo
    write(repo, "a.txt", "hi\n")
    commit("a.txt")

    assert run_diff(capsys) == ""


def test_cli_shows_headers_and_added_removed_lines(cli_repo, capsys):
    repo, commit = cli_repo
    write(repo, "a.txt", "one\ntwo\nthree\n")
    commit("a.txt")
    write(repo, "a.txt", "one\nTWO\nthree\n")

    out = run_diff(capsys).splitlines()

    assert "diff --minigit a/a.txt b/a.txt" in out
    assert "--- a/a.txt" in out
    assert "+++ b/a.txt" in out
    assert "@@ -2,1 +2,1 @@" in out
    assert "-two" in out
    assert "+TWO" in out
    assert " one" not in out


def test_cli_removed_lines_come_from_the_old_text(cli_repo, capsys):
    repo, commit = cli_repo
    write(repo, "a.txt", "keep\nold1\nold2\nkeep2\n")
    commit("a.txt")
    write(repo, "a.txt", "keep\nnew\nkeep2\n")

    out = run_diff(capsys).splitlines()

    assert out.index("-old1") < out.index("-old2") < out.index("+new")


def test_cli_second_hunk_line_numbers_account_for_earlier_edits(cli_repo, capsys):
    repo, commit = cli_repo
    write(repo, "a.txt", "a\nb\nc\nd\ne\nf\n")
    commit("a.txt")
    write(repo, "a.txt", "a\nX\nY\nb\nc\nd\ne\nF\n")

    out = run_diff(capsys).splitlines()

    assert "@@ -1,0 +2,2 @@" in out
    assert "@@ -6,1 +8,1 @@" in out


def test_cli_new_and_deleted_files(cli_repo, capsys):
    repo, commit = cli_repo
    write(repo, "gone.txt", "bye\n")
    commit("gone.txt")
    os.remove("gone.txt")
    write(repo, "new.txt", "hello\n")

    out = run_diff(capsys).splitlines()

    assert "deleted file mode 100644" in out
    assert "-bye" in out
    assert "+++ /dev/null" in out
    assert "new file mode 100644" in out
    assert "--- /dev/null" in out
    assert "+hello" in out


def test_cli_unborn_head_shows_everything_as_new(cli_repo, capsys):
    repo, _ = cli_repo
    write(repo, "a.txt", "hi\n")

    out = run_diff(capsys).splitlines()

    assert "new file mode 100644" in out
    assert "+hi" in out


def test_cli_binary_file_reports_binary_changed(cli_repo, capsys):
    repo, commit = cli_repo
    write(repo, "img.bin", b"\x00\x01")
    commit("img.bin")
    write(repo, "img.bin", b"\x00\x02")

    out = run_diff(capsys)

    assert "binary file changed" in out
    assert "@@" not in out


def test_cli_mode_only_change(cli_repo, capsys):
    repo, commit = cli_repo
    write(repo, "run.sh", "echo hi\n", mode=0o644)
    commit("run.sh")
    os.chmod("run.sh", 0o755)

    out = run_diff(capsys).splitlines()

    assert "old mode 100644" in out
    assert "new mode 100755" in out
    assert not any(line.startswith("@@") for line in out)


def test_cli_missing_final_newline_is_marked(cli_repo, capsys):
    repo, commit = cli_repo
    write(repo, "a.txt", "a\nb")
    commit("a.txt")
    write(repo, "a.txt", "a\nb\n")

    out = run_diff(capsys).splitlines()

    assert "-b" in out
    assert "\\ No newline at end of file" in out
    assert "+b" in out


def test_cli_diff_and_status_are_read_only(cli_repo, capsys):
    repo, commit = cli_repo
    write(repo, "a.txt", "one\n")
    commit("a.txt")
    write(repo, "a.txt", "two\n")
    write(repo, "b.txt", "new\n")
    before_files = minigit_files(repo)
    before_index = repo.read_index()

    cmd_diff(None)
    cmd_status(None)
    capsys.readouterr()

    assert minigit_files(repo) == before_files
    assert repo.read_index() == before_index
    with open("a.txt") as f:
        assert f.read() == "two\n"


def test_status_sections_still_staged_not_staged_untracked(cli_repo, capsys):
    repo, commit = cli_repo
    write(repo, "a.txt", "one\n")
    commit("a.txt")
    write(repo, "a.txt", "two\n")
    write(repo, "b.txt", "new\n")
    repo.stage_file("b.txt")
    write(repo, "c.txt", "loose\n")

    cmd_status(None)
    out = capsys.readouterr().out.splitlines()

    assert out == ["staged:", "  b.txt", "not staged:", "  a.txt", "untracked:", "  c.txt"]


# ---------------------------------------------------------------------------
# Merge integration: M3 hands us a merged tree (conflict markers are just blob text)
# ---------------------------------------------------------------------------


def test_checkout_applies_a_merge_tree_with_conflict_markers(repo):
    write(repo, "a.txt", "base\n")
    repo.stage_file("a.txt")
    base_tree = repo.build_tree_from_index()
    repo.checkout(base_tree)

    conflicted = b"<<<<<<< ours\nmine\n=======\ntheirs\n>>>>>>> theirs\n"
    blob = repo.store.write_object(conflicted, "blob")
    clean = repo.store.write_object(b"added by merge\n", "blob")
    merge_tree = repo.store.write_tree(
        [
            TreeEntry("100644", "blob", blob, "a.txt"),
            TreeEntry("100644", "blob", clean, "b.txt"),
        ]
    )

    repo.checkout(merge_tree)

    with open(os.path.join(repo.root, "a.txt"), "rb") as f:
        assert f.read() == conflicted
    with open(os.path.join(repo.root, "b.txt"), "rb") as f:
        assert f.read() == b"added by merge\n"
    assert [e.path for e in repo.read_index()] == ["a.txt", "b.txt"]
    assert repo.diff_working_tree_vs(merge_tree).line_diffs == {}


# ---------------------------------------------------------------------------
# Week 6: pull (fast-forward), safe checkout, merge results
# ---------------------------------------------------------------------------


class Repo:
    """One repository: real object store, working tree and commit manager."""

    def __init__(self, root):
        self.root = str(root)
        os.makedirs(self.root, exist_ok=True)
        self.store = ObjectStore(self.root)
        self.wt = WorkingTree(self.root, store=self.store)
        self.cm = CommitManager(self.root, store=self.store, tree=self.wt)

    def path(self, name):
        return os.path.join(self.root, name)

    def write(self, name, content, mode=None):
        full = self.path(name)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "wb") as f:
            f.write(content if isinstance(content, bytes) else content.encode())
        if mode is not None:
            os.chmod(full, mode)

    def read(self, name):
        with open(self.path(name), "rb") as f:
            return f.read()

    def exists(self, name):
        return os.path.lexists(self.path(name))

    def remove(self, name):
        os.remove(self.path(name))

    def stage(self, *names):
        for name in names:
            self.wt.stage_file(name)

    def commit(self, message, extra_parents=()):
        """Commit whatever is in the index on the current branch."""
        branch = self.cm._current_branch()
        tip = self.cm.read_ref(branch)
        parents = ([tip] if tip else []) + list(extra_parents)
        tree = self.wt.build_tree_from_index()
        return self.cm.create_commit(tree, parents, "Test <t@example.com>", message)

    def snapshot(self):
        """Everything that defines repo state: files on disk, index, refs, HEAD."""
        files = {}
        for dirpath, dirnames, filenames in os.walk(self.root):
            dirnames[:] = [d for d in dirnames if d not in {".minigit"}]
            for name in filenames:
                full = os.path.join(dirpath, name)
                rel = os.path.relpath(full, self.root)
                with open(full, "rb") as f:
                    files[rel] = (f.read(), os.access(full, os.X_OK))
        meta = {}
        for sub in ("index", "HEAD"):
            full = os.path.join(self.root, ".minigit", sub)
            if os.path.exists(full):
                with open(full, "rb") as f:
                    meta[sub] = f.read()
        refs = os.path.join(self.root, ".minigit", "refs", "heads")
        if os.path.isdir(refs):
            for name in sorted(os.listdir(refs)):
                with open(os.path.join(refs, name), "rb") as f:
                    meta[f"ref:{name}"] = f.read()
        return files, meta

    def object_files(self):
        found = {}
        for dirpath, _, filenames in os.walk(os.path.join(self.root, ".minigit", "objects")):
            for name in filenames:
                full = os.path.join(dirpath, name)
                found[full] = os.path.getmtime(full)
        return found


def download(source: Repo, target: Repo, branch="main", as_ref="theirs"):
    """Stand-in for fetch: copy every object over and point `as_ref` at the source tip."""
    src_objects = os.path.join(source.root, ".minigit", "objects")
    dst_objects = os.path.join(target.root, ".minigit", "objects")
    shutil.copytree(src_objects, dst_objects, dirs_exist_ok=True)
    target.cm.write_ref(as_ref, source.cm.read_ref(branch))


def status_text(repo, monkeypatch, capsys):
    monkeypatch.chdir(repo.root)
    assert cmd_status(None) == 0
    return capsys.readouterr().out


def diff_text(repo, monkeypatch, capsys):
    monkeypatch.chdir(repo.root)
    assert cmd_diff(None) == 0
    return capsys.readouterr().out


@pytest.fixture
def source(tmp_path):
    """A 'remote' repo with one commit: nested dirs, an executable, binary, unicode."""
    repo = Repo(tmp_path / "source")
    repo.write("README.md", "hello\n")
    repo.write("src/app.py", "print('v1')\n")
    repo.write("src/deep/util.py", "x = 1\n")
    repo.write("run.sh", "#!/bin/sh\necho hi\n", mode=0o755)
    repo.write("data.bin", b"\x00\x01\xff\xfe")
    repo.write("café.txt", "crème\n")
    repo.write("noeol.txt", "no newline at end")
    repo.stage(
        "README.md", "src/app.py", "src/deep/util.py", "run.sh", "data.bin", "café.txt",
        "noeol.txt",
    )  # fmt: skip
    repo.commit("first")
    return repo


@pytest.fixture
def local(tmp_path):
    return Repo(tmp_path / "local")


# ---------------------------------------------------------------------------
# Fast-forward: files, deletions, nested directories, modes, index, status
# ---------------------------------------------------------------------------


def test_fast_forward_into_fresh_repo_restores_everything(source, local, monkeypatch, capsys):
    download(source, local)

    local.cm.merge("theirs")

    assert local.read("README.md") == b"hello\n"
    assert local.read("src/deep/util.py") == b"x = 1\n"
    assert local.read("data.bin") == b"\x00\x01\xff\xfe"
    assert local.read("café.txt") == "crème\n".encode()
    assert local.read("noeol.txt") == b"no newline at end"
    assert os.access(local.path("run.sh"), os.X_OK)
    assert not os.access(local.path("README.md"), os.X_OK)
    assert [e.path for e in local.wt.read_index()] == sorted(e.path for e in source.wt.read_index())
    assert local.cm._current_branch() == "main"
    assert local.cm.read_ref("main") == source.cm.read_ref("main")
    assert status_text(local, monkeypatch, capsys) == "clean\n"
    assert diff_text(local, monkeypatch, capsys) == ""


def test_second_fast_forward_applies_edit_delete_new_dir_and_mode(
    source, local, monkeypatch, capsys
):
    download(source, local)
    local.cm.merge("theirs")

    source.write("README.md", "hello again\n")
    source.remove("src/deep/util.py")  # last file in src/deep -> directory must go too
    source.write("docs/guide/intro.md", "intro\n")
    os.chmod(source.path("run.sh"), 0o644)
    source.stage("README.md", "src/deep/util.py", "docs/guide/intro.md", "run.sh")
    source.commit("second")
    download(source, local)

    local.cm.merge("theirs")

    assert local.read("README.md") == b"hello again\n"
    assert not local.exists("src/deep/util.py")
    assert not local.exists("src/deep")
    assert local.read("docs/guide/intro.md") == b"intro\n"
    assert not os.access(local.path("run.sh"), os.X_OK)
    assert local.cm._current_branch() == "main"
    assert status_text(local, monkeypatch, capsys) == "clean\n"
    assert diff_text(local, monkeypatch, capsys) == ""


# ---------------------------------------------------------------------------
# Local work is preserved; validation failures change nothing
# ---------------------------------------------------------------------------


@pytest.fixture
def synced(source, local):
    """`local` already has the first commit; `source` then moves ahead."""
    download(source, local)
    local.cm.merge("theirs")
    source.write("README.md", "hello from upstream\n")
    source.write("added.txt", "new upstream file\n")
    source.stage("README.md", "added.txt")
    source.commit("second")
    download(source, local)
    return source, local


def test_dirty_tracked_file_blocks_pull_and_is_kept(synced):
    _, local = synced
    local.write("README.md", "my local edit\n")
    before = local.snapshot()

    with pytest.raises(MiniGitError):
        local.cm.merge("theirs")

    assert local.snapshot() == before
    assert local.read("README.md") == b"my local edit\n"
    assert not local.exists("added.txt")


def test_untracked_collision_blocks_pull_and_is_kept(synced):
    _, local = synced
    local.write("added.txt", "my untracked file\n")
    before = local.snapshot()

    with pytest.raises(MiniGitError):
        local.cm.merge("theirs")

    assert local.snapshot() == before
    assert local.read("added.txt") == b"my untracked file\n"


def test_missing_blob_leaves_files_and_index_untouched(synced):
    source, local = synced
    new_blob = next(e.hash for e in source.wt.read_index() if e.path == "added.txt")
    os.remove(os.path.join(local.root, ".minigit", "objects", new_blob[:2], new_blob[2:]))
    before = local.snapshot()

    with pytest.raises(ObjectNotFoundError):
        local.cm.merge("theirs")

    assert local.snapshot() == before


def test_unsafe_path_in_incoming_tree_changes_nothing(local):
    local.write("keep.txt", "mine\n")
    local.stage("keep.txt")
    local.commit("base")
    blob = local.store.write_object(b"evil", "blob")
    sub = local.store.write_tree([TreeEntry("100644", "blob", blob, "x")])
    # a tree whose directory is named ".minigit" would write inside the repo's own data
    tree = local.store.write_tree(
        [
            TreeEntry("40000", "tree", sub, ".minigit"),
            TreeEntry("100644", "blob", blob, "keep.txt"),
        ]
    )
    before = local.snapshot()

    with pytest.raises(MiniGitError):
        local.wt.checkout(tree)

    assert local.snapshot() == before


# ---------------------------------------------------------------------------
# Status and diff are read-only
# ---------------------------------------------------------------------------


def test_status_and_diff_do_not_change_the_repo(synced, monkeypatch, capsys):
    _, local = synced
    local.cm.merge("theirs")
    local.write("README.md", "edited after pull\n")
    local.write("untracked.txt", "u\n")
    local.write("staged.txt", "s\n")
    local.stage("staged.txt")
    before_state = local.snapshot()
    before_objects = local.object_files()

    status_text(local, monkeypatch, capsys)
    diff_text(local, monkeypatch, capsys)

    assert local.snapshot() == before_state
    assert local.object_files() == before_objects


# ---------------------------------------------------------------------------
# Merge results: markers on disk, pre-merge index entries, resolution via `add`
# ---------------------------------------------------------------------------

MARKERS = b"<<<<<<< ours\nmine\n=======\ntheirs\n>>>>>>> theirs\n"


@pytest.fixture
def merging(local):
    """A repo with a commit, plus the tree M3 would build for a conflicting merge."""
    local.write("a.txt", "base\n")
    local.write("b.txt", "unchanged\n")
    local.stage("a.txt", "b.txt")
    local.commit("ours")
    marker_blob = local.store.write_object(MARKERS, "blob")
    clean_blob = local.store.write_object(b"added by theirs\n", "blob")
    b_blob = next(e.hash for e in local.wt.read_index() if e.path == "b.txt")
    merged_tree = local.store.write_tree(
        [
            TreeEntry("100644", "blob", marker_blob, "a.txt"),
            TreeEntry("100644", "blob", b_blob, "b.txt"),
            TreeEntry("100644", "blob", clean_blob, "c.txt"),
        ]
    )
    return local, merged_tree


def test_conflicted_path_gets_markers_but_keeps_pre_merge_index_entry(merging, monkeypatch, capsys):
    local, merged_tree = merging
    old_a = next(e for e in local.wt.read_index() if e.path == "a.txt")

    local.wt.checkout(merged_tree, conflicted_paths={"a.txt"})

    assert local.read("a.txt") == MARKERS
    assert local.read("c.txt") == b"added by theirs\n"
    index = {e.path: e for e in local.wt.read_index()}
    assert index["a.txt"] == old_a
    assert index["c.txt"].hash == hashlib.sha1(b"blob 16\0added by theirs\n").hexdigest()
    assert status_text(local, monkeypatch, capsys) == "staged:\n  c.txt\nnot staged:\n  a.txt\n"


def test_conflicted_checkout_of_unknown_path_is_refused_before_any_write(merging):
    local, merged_tree = merging
    before = local.snapshot()

    with pytest.raises(MiniGitError):
        local.wt.checkout(merged_tree, conflicted_paths={"nope.txt"})

    assert local.snapshot() == before


def test_resolve_with_add_then_commit_is_clean_across_fresh_objects(merging, monkeypatch, capsys):
    local, merged_tree = merging
    local.wt.checkout(merged_tree, conflicted_paths={"a.txt"})
    theirs_tip = local.cm.read_ref("main")  # stand-in for the second parent

    # each step below uses brand-new objects, like separate CLI processes would
    fresh = Repo(local.root)
    fresh.write("a.txt", "resolved by hand\n")
    monkeypatch.chdir(fresh.root)
    assert cmd_add(argparse.Namespace(path="a.txt")) == 0

    fresh = Repo(local.root)
    assert status_text(fresh, monkeypatch, capsys) == "staged:\n  a.txt\n  c.txt\n"

    fresh = Repo(local.root)
    fresh.commit("merge", extra_parents=[theirs_tip])

    fresh = Repo(local.root)
    assert status_text(fresh, monkeypatch, capsys) == "clean\n"
    assert fresh.read("a.txt") == b"resolved by hand\n"
    assert len(fresh.cm.read_commit(fresh.cm.read_ref("main")).parents) == 2


def test_resolved_deletion_with_add_then_commit(merging, monkeypatch, capsys):
    local, merged_tree = merging
    local.wt.checkout(merged_tree, conflicted_paths={"a.txt"})

    local.remove("a.txt")
    monkeypatch.chdir(local.root)
    assert cmd_add(argparse.Namespace(path="a.txt")) == 0

    fresh = Repo(local.root)
    assert [e.path for e in fresh.wt.read_index()] == ["b.txt", "c.txt"]
    assert status_text(fresh, monkeypatch, capsys) == "staged:\n  a.txt\n  c.txt\n"
    fresh.commit("merge: delete a.txt")
    fresh = Repo(local.root)
    assert status_text(fresh, monkeypatch, capsys) == "clean\n"
    assert not fresh.exists("a.txt")


def test_add_of_a_missing_untracked_path_still_fails(local):
    with pytest.raises(MiniGitError, match="No such file"):
        local.wt.stage_file("never-existed.txt")
