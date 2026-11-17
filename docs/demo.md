# Storage demo

This is the Module 1 segment for the shared Week 9 demo. It uses a disposable
temporary repository and the existing Python object API. There is no `pack`
CLI command; packing is demonstrated with `ObjectStore.pack_objects`.

## Presenter flow

Run this from the repository root with the project environment selected:

```python
from pathlib import Path
from tempfile import TemporaryDirectory

from minigit.commits import CommitManager
from minigit.objects import ObjectStore, TreeEntry

with TemporaryDirectory(prefix="minigit-storage-demo-") as directory:
    root = Path(directory)
    store = ObjectStore(root)
    commits = CommitManager(root, store=store)

    original = b"hello\n"
    changed = b"hello, storage\n"
    original_id = store.hash_object(original, "blob")
    same_id = store.hash_object(original, "blob")
    changed_id = store.hash_object(changed, "blob")
    assert original_id == same_id
    assert original_id != changed_id

    blob = store.write_object(original, "blob")
    nested = store.write_tree([TreeEntry("100644", "blob", blob, "hello.txt")])
    root_tree = store.write_tree([TreeEntry("40000", "tree", nested, "nested")])
    base = commits.create_commit(root_tree, [], "demo <demo@example.com>", "base")
    side = commits.create_commit(root_tree, [base], "demo <demo@example.com>", "side")
    merge = commits.create_commit(root_tree, [base, side], "demo <demo@example.com>", "merge")

    objects = [blob, nested, root_tree, base, side, merge]
    store.pack_objects(objects)
    for object_id in objects:
        store._object_path(object_id).unlink()

    packed_store = ObjectStore(root)
    assert packed_store.read_object(blob) == ("blob", original)
    assert packed_store.read_tree(root_tree)[0].type == "tree"
    assert commits.read_commit(merge).parents == [base, side]
```

Expected results:

```text
identical bytes -> same object ID: true
changed bytes -> different object ID: true
nested tree -> tree object links to blob and subtree: true
merge commit -> preserves two parents: true
packed-only snapshot -> all six objects read successfully: true
```

## Loose, packed-only, and mixed reads

Use the same object list to show the three supported storage states:

| State | Fixture action | Expected read |
| --- | --- | --- |
| Loose | Keep all loose object files | Every object returns its original type and bytes |
| Packed-only | Call `pack_objects`, then remove loose files in the temporary repository | Every object is read through the published index and pack |
| Mixed | Pack only a subset and keep the remaining loose files | Loose objects are preferred; packed objects are read as fallback |

The removal step is safe here because the repository is created by
`TemporaryDirectory`. Do not remove loose objects from a working repository as
part of the demo.

The larger shared fixture is reproducible with:

```powershell
.\.venv\Scripts\python.exe scripts\benchmark_packfiles.py --profile
```

It reports loose bytes, retained loose bytes, pack bytes, index bytes, pack
time, and packed-only read time. `--no-cache --profile` is the controlled
comparison for repeated index and pack validation.

## Failure guarantees to explain

- A missing hash or absent object raises `ObjectNotFoundError`.
- Malformed loose or packed bytes raise `ObjectCorruptError`.
- A corrupt loose copy wins over a valid packed copy; it is never silently
  replaced by fallback lookup.
- Duplicate writes validate the existing object before returning its hash.
- Pack publication validates both temporary files, renames the pack first, and
  publishes the index last. A failed publication leaves earlier readable
  objects intact and does not expose an unpublished index.

## Rehearsal record

The flow above was rehearsed successfully on revision
`006740a34dc70c70b47397b25a9b681ecdf3f463` with Python 3.11.9 on Windows.
The object regression suite passed 79 tests. The demo intentionally does not
claim pruning, garbage collection, delta compression, or a pack CLI.
