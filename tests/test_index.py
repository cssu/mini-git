"""Tests for minigit/index.py - WorkingTree, IndexEntry, DiffResult."""

from minigit.index import WorkingTree
from minigit.objects import ObjectStore


def test_index_works_new_workingtree(tmp_path):
    (tmp_path / "hello.txt").write_text("hi")
    store = ObjectStore(str(tmp_path))

    wt1 = WorkingTree(repo_path=str(tmp_path), store=store)
    wt1.stage_file("hello.txt")

    # a brand new WorkingTree, simulating a separate CLI invocation
    wt2 = WorkingTree(repo_path=str(tmp_path), store=store)
    entries = wt2.read_index()

    assert len(entries) == 1
    assert entries[0].path == "hello.txt"


def test_staging_twice(tmp_path):
    (tmp_path / "hello.txt").write_text("hi")
    store = ObjectStore(str(tmp_path))
    wt = WorkingTree(repo_path=str(tmp_path), store=store)

    wt.stage_file("hello.txt")
    wt.stage_file("hello.txt")

    entries = wt.read_index()
    assert len(entries) == 1


def test_index_file(tmp_path):
    (tmp_path / "z.txt").write_text("z")
    store = ObjectStore(str(tmp_path))
    wt = WorkingTree(repo_path=str(tmp_path), store=store)

    wt.stage_file("z.txt")

    index_path = tmp_path / ".minigit" / "index"
    lines = index_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1

    (tmp_path / "a.txt").write_text("a")
    wt.stage_file("a.txt")

    lines = index_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert lines[0].endswith("a.txt")
    assert lines[1].endswith("z.txt")


def test_path_with_space(tmp_path):
    (tmp_path / "my notes.txt").write_text("hello")
    store = ObjectStore(str(tmp_path))
    wt = WorkingTree(repo_path=str(tmp_path), store=store)

    wt.stage_file("my notes.txt")

    entries = wt.read_index()
    assert len(entries) == 1
    assert entries[0].path == "my notes.txt"


def test_edit_after_staging(tmp_path):
    file = tmp_path / "hello.txt"
    file.write_text("original")
    store = ObjectStore(str(tmp_path))
    wt = WorkingTree(repo_path=str(tmp_path), store=store)

    wt.stage_file("hello.txt")
    hash_before = wt.read_index()[0].hash

    file.write_text("changed!!!")

    hash_after = wt.read_index()[0].hash
    assert hash_before == hash_after  # snapshot rule still holds

    status = wt._working_status()
    assert "hello.txt" in status.modified


def test_untracked(tmp_path):
    (tmp_path / "hello.txt").write_text("hi")
    (tmp_path / "extra.txt").write_text("not staged at all")
    store = ObjectStore(str(tmp_path))
    wt = WorkingTree(repo_path=str(tmp_path), store=store)

    wt.stage_file("hello.txt")

    status = wt._working_status()
    assert "extra.txt" in status.added
    assert "hello.txt" not in status.added


def test_deleted(tmp_path):
    file = tmp_path / "hello.txt"
    file.write_text("hi")
    store = ObjectStore(str(tmp_path))
    wt = WorkingTree(repo_path=str(tmp_path), store=store)

    wt.stage_file("hello.txt")
    file.unlink()

    status = wt._working_status()
    assert "hello.txt" in status.deleted