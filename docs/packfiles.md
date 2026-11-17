# Packfile storage

`ObjectStore.pack_objects(hashes)` deduplicates selected objects, validates them
through `read_object`, and returns `(pack_path, index_path)`. Loose originals
remain in place; new objects are written loose. Pruning and delta compression
are not implemented.

Packfiles are currently a Python API, not a CLI command. The supported entry
point is `ObjectStore.pack_objects`; `hash-object` and `cat-file` continue to
operate on individual objects.

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

After validation, an `ObjectStore` instance caches the parsed index together
with the index and pack modification times and sizes. Every lookup still
enumerates the current `pack-*.idx` files, so an existing store discovers a
newly published pack. A changed published file invalidates its cached metadata
and repeats full validation; the selected record is then decoded and hash
checked on every read. The cache stores metadata, not object payloads, and is
discarded when the store instance is discarded.

## Publication and compatibility

Both temporary files are closed and validated before publication. The pack is
renamed first, then the index is published last. Readers discover only finalized
indexes, so interruption cannot expose a partially written pack. Unique immutable
names prevent overwriting existing pairs. Temporary files are removed on failure;
an orphaned pack without an index is ignored. Loose originals remain readable.

History, checkout, diff, merge, and networking use the same object API for loose,
packed, and mixed storage. Packfiles are a local storage format: push continues
to transfer validated individual objects through the existing protocol.

## Readiness limitations

The current format retains loose originals after packing, has no pruning or
garbage collection, and does not use delta compression. The index and pack
formats are version 1 and are intentionally unchanged by the storage demo.
