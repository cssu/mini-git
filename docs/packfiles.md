# Packfile storage

`ObjectStore.pack_objects(hashes)` deduplicates selected objects, validates them
through `read_object`, and returns `(pack_path, index_path)`. Loose originals
remain in place; new objects are written loose. Pruning and delta compression
are not implemented.

```python
from minigit.objects import ObjectStore

store = ObjectStore(".")
blob = store.write_object(b"hello\n", "blob")
pack_path, index_path = store.pack_objects([blob, blob])
assert store.read_object(blob) == ("blob", b"hello\n")
```

## Binary format (version 1)

Immutable `pack-<id>.pack` and `pack-<id>.idx` pairs live under
`.minigit/objects/pack/`. All integers are unsigned and big-endian; there is no
alignment padding. Offsets are measured in bytes from the start of the pack.

| Structure | Fields, in order |
| --- | --- |
| Pack header (12 bytes) | `MGPK` (4 bytes), version (uint32), object count (uint32) |
| Record header (17 bytes) | type length (uint8), raw length (uint64), compressed length (uint64) |
| Record body | ASCII type (`blob`, `tree`, or `commit`), then one zlib-compressed payload |
| Index header (12 bytes) | `MGIX` (4 bytes), version (uint32), entry count (uint32) |
| Index entry (36 bytes) | raw SHA-1 (20 bytes), record offset (uint64), total record length (uint64) |

Index entries are sorted by hash and cover every pack record exactly once.
Records preserve each object's type and exact original bytes; hashes remain
SHA-1 of `type + space + byte_length + NUL + data`.

## Lookup and validation

`read_object(hash) -> (type, bytes)` checks loose storage first, then published
indexes in filename order. A missing loose file falls back to packs. Corrupt
loose objects raise `ObjectCorruptError` without falling back. A hash absent
from both storage forms raises `ObjectNotFoundError`.

Packed lookup validates versions, counts, ordering, record boundaries, offsets,
lengths, and complete index coverage. Reading a selected record also validates
its type, zlib end-of-stream, uncompressed length, and hash. Duplicate writes and
`has_object` use the same validation and work for packed-only objects. An invalid
published index raises `ObjectCorruptError`; it is not silently ignored.

Published index metadata is cached per `ObjectStore` instance after the complete
index and pack pass validation. The cache is bounded to 64 entries and evicts the
least recently used entry, so repositories with many packs do not grow the
reader's memory without limit. Each lookup still enumerates published indexes and
checks index and pack metadata; new packs are therefore discovered, and changed
or replaced backing files force validation again. Selected records are decoded
and hash-checked on every read.

## Publication and compatibility

Both temporary files are closed and validated before publication. The pack is
renamed first, then the index is published last. Readers discover only finalized
indexes, so interruption cannot expose a partially written pack. Unique immutable
names prevent overwriting existing pairs. Temporary files are removed on failure;
an orphaned pack without an index is ignored. Loose originals remain readable.

History, checkout, diff, merge, and networking use the same object API for loose,
packed, and mixed storage. Packfiles are a local storage format: push continues
to transfer validated individual objects through the existing protocol.
