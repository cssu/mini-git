import pytest

from minigit.errors import MiniGitError, ObjectCorruptError
from minigit.index import IndexEntry, WorkingTree
from minigit.objects import TreeEntry


def target_tree(wt, path, data=b"target"):
    obj = wt.store.write_object(data, "blob")
    parts = path.split("/")
    tree = wt.store.write_tree([TreeEntry("100644", "blob", obj, parts[-1])])
    for part in reversed(parts[:-1]):
        tree = wt.store.write_tree([TreeEntry("40000", "tree", tree, part)])
    return tree


def test_checkout_file_directory_transitions(tmp_path):
    wt = WorkingTree(tmp_path)
    flat = target_tree(wt, "path", b"file")
    nested = target_tree(wt, "path/nested", b"nested")
    wt.checkout(flat)
    wt.checkout(nested)
    assert (tmp_path / "path/nested").read_bytes() == b"nested"
    wt.checkout(flat)
    assert (tmp_path / "path").read_bytes() == b"file"
    assert [e.path for e in wt.read_index()] == ["path"]


def test_checkout_blocking_parent_fails_before_deleting_files(tmp_path):
    wt = WorkingTree(tmp_path)
    wt.checkout(target_tree(wt, "tracked"))
    before = wt.read_index()
    (tmp_path / "blocker").write_bytes(b"untracked")
    with pytest.raises(MiniGitError):
        wt.checkout(target_tree(wt, "blocker/nested"))
    assert (tmp_path / "tracked").read_bytes() == b"target"
    assert (tmp_path / "blocker").read_bytes() == b"untracked"
    assert wt.read_index() == before


@pytest.mark.parametrize("dangling", [False, True])
def test_checkout_rejects_leaf_symlink(tmp_path, dangling):
    root = tmp_path / "repo"
    root.mkdir()
    outside = tmp_path / "outside"
    if not dangling:
        outside.write_bytes(b"original")
    wt = WorkingTree(root)
    old = wt.store.write_object(b"original", "blob")
    if not dangling:
        wt.write_index([IndexEntry("100644", old, "link")])
    (root / "link").symlink_to(outside)
    with pytest.raises(MiniGitError):
        wt.checkout(target_tree(wt, "link"))
    assert (root / "link").is_symlink()
    if dangling:
        assert not outside.exists()
    else:
        assert outside.read_bytes() == b"original"


def test_checkout_rejects_unsafe_current_index(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.write_bytes(b"original")
    wt = WorkingTree(root)
    old = wt.store.write_object(b"original", "blob")
    wt.write_index([IndexEntry("100644", old, "../outside")])
    with pytest.raises(MiniGitError):
        wt.checkout(wt.store.write_tree([]))
    assert outside.read_bytes() == b"original"


def test_checkout_rejects_non_blob_before_changing_files(tmp_path):
    wt = WorkingTree(tmp_path)
    wt.checkout(target_tree(wt, "tracked"))
    before = wt.read_index()
    wrong_type = wt.store.write_tree([])
    bad_tree = wt.store.write_tree([TreeEntry("100644", "blob", wrong_type, "new")])
    with pytest.raises(ObjectCorruptError):
        wt.checkout(bad_tree)
    assert (tmp_path / "tracked").read_bytes() == b"target"
    assert wt.read_index() == before


def test_checkout_directory_transition_preserves_untracked_children(tmp_path):
    wt = WorkingTree(tmp_path)
    wt.checkout(target_tree(wt, "path/tracked"))
    (tmp_path / "path/untracked").write_bytes(b"keep")
    before = wt.read_index()
    with pytest.raises(MiniGitError):
        wt.checkout(target_tree(wt, "path"))
    assert (tmp_path / "path/tracked").read_bytes() == b"target"
    assert (tmp_path / "path/untracked").read_bytes() == b"keep"
    assert wt.read_index() == before
