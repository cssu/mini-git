"""Tests for minigit/index.py - WorkingTree, IndexEntry, DiffResult."""

from minigit.index import WorkingTree


class FakeObjectStore:
    """Stand-in for the real ObjectStore - keeps everything in memory."""

    def __init__(self):
        self.objects = {}

    def write_object(self, data, obj_type):
        fake_hash = f"hash{len(data)}"
        self.objects[fake_hash] = data
        return fake_hash


def test_staging_adds_one_entry(tmp_path):
    file = tmp_path / "hello.txt"
    file.write_text("hi")

    wt = WorkingTree(repo_path=str(tmp_path), store=FakeObjectStore())
    wt.stage_file("hello.txt")

    entries = wt.read_index()
    assert len(entries) == 1
    assert entries[0].path == "hello.txt"


def test_staging_twice_replaces_not_duplicates(tmp_path):
    file = tmp_path / "hello.txt"
    file.write_text("hi")

    wt = WorkingTree(repo_path=str(tmp_path), store=FakeObjectStore())
    wt.stage_file("hello.txt")
    wt.stage_file("hello.txt")

    entries = wt.read_index()
    assert len(entries) == 1


def test_read_index_sorted(tmp_path):
    (tmp_path / "z.txt").write_text("z")
    (tmp_path / "a.txt").write_text("a")

    wt = WorkingTree(repo_path=str(tmp_path), store=FakeObjectStore())
    wt.stage_file("z.txt")
    wt.stage_file("a.txt")

    entries = wt.read_index()
    assert entries[0].path == "a.txt"
    assert entries[1].path == "z.txt"


def test_editing_after_staging_does_not_change_entry(tmp_path):
    file = tmp_path / "hello.txt"
    file.write_text("original content")

    wt = WorkingTree(repo_path=str(tmp_path), store=FakeObjectStore())
    wt.stage_file("hello.txt")

    hash_before = wt.read_index()[0].hash

    file.write_text("changed content!!!")

    hash_after = wt.read_index()[0].hash

    assert hash_before == hash_after
