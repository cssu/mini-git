"""Tests for minigit/index.py - WorkingTree, trees, diffing, and status."""

from minigit.commits import CommitManager
from minigit.index import WorkingTree, cmd_status
from minigit.objects import ObjectStore


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
