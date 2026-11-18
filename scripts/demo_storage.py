"""Rehearse storage invariants in a disposable repository."""

import sys
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from minigit.commits import CommitManager
from minigit.objects import ObjectStore, TreeEntry


def verify_snapshot(root, expected, root_tree, nested_tree, blob, merge, parents):
    """Use a fresh store to check every object plus the tree and parent links."""
    reader = ObjectStore(root)
    for object_id, value in expected.items():
        assert reader.read_object(object_id) == value
    assert reader.read_tree(root_tree) == [TreeEntry("40000", "tree", nested_tree, "nested")]
    assert reader.read_tree(nested_tree) == [TreeEntry("100644", "blob", blob, "hello.txt")]
    assert CommitManager(root, store=reader).read_commit(merge).parents == parents


def main() -> int:
    with TemporaryDirectory(prefix="minigit-storage-demo-") as directory:
        root = Path(directory)
        store = ObjectStore(root)
        commits = CommitManager(root, store=store)
        original = b"hello\n"
        original_id = store.hash_object(original, "blob")
        assert original_id == store.hash_object(original, "blob")
        assert original_id != store.hash_object(b"hello, storage\n", "blob")
        print("identical bytes -> same object ID: true")
        print("changed bytes -> different object ID: true")

        blob = store.write_object(original, "blob")
        nested_tree = store.write_tree([TreeEntry("100644", "blob", blob, "hello.txt")])
        root_tree = store.write_tree([TreeEntry("40000", "tree", nested_tree, "nested")])
        author = "demo <demo@example.com>"
        base = commits.create_commit(root_tree, [], author, "base")
        left = commits.create_commit(root_tree, [base], author, "left")
        right = commits.create_commit(root_tree, [base], author, "right")
        parents = [left, right]
        merge = commits.create_commit(root_tree, parents, author, "merge")
        objects = [blob, nested_tree, root_tree, base, left, right, merge]
        expected = {object_id: store.read_object(object_id) for object_id in objects}
        assert expected[blob] == ("blob", original)

        verify_snapshot(root, expected, root_tree, nested_tree, blob, merge, parents)
        print("nested tree -> links to subtree and blob: true")
        print("merge commit -> preserves both parents: true")
        print("loose snapshot -> all seven objects verified: true")

        # All loose-file removal stays inside this TemporaryDirectory fixture.
        mixed_objects = [blob, nested_tree, base]
        store.pack_objects(mixed_objects)
        for object_id in mixed_objects:
            store._object_path(object_id).unlink()
        verify_snapshot(root, expected, root_tree, nested_tree, blob, merge, parents)
        print("mixed snapshot -> all seven objects verified: true")

        store.pack_objects(objects)
        for object_id in objects:
            store._object_path(object_id).unlink(missing_ok=True)
        verify_snapshot(root, expected, root_tree, nested_tree, blob, merge, parents)
        print("packed-only snapshot -> all seven objects verified: true")

    print("temporary repository cleaned up: true")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
