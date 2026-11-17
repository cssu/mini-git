# Module 1: object storage

## What was built

Module 1 owns every read and write under `.minigit/objects/`. `ObjectStore`
provides the stable boundary used by trees, commits, the working tree, and
networking:

- `hash_object(data, type)` computes an object ID without writing.
- `write_object(data, type)` validates duplicate objects and atomically writes
  compressed loose storage.
- `read_object(hash)` returns `(type, bytes)` with loose-first lookup.
- `write_tree` and `read_tree` serialize and validate nested tree entries.
- `pack_objects(hashes)` creates and publishes a local pack/index pair.

The public object API and SHA-1 IDs are unchanged by the packfile work.

## Object framing and hashing

An object ID is the SHA-1 digest of:

```text
<type> <byte length>\0<exact bytes>
```

The length is measured in bytes, not characters, so binary and UTF-8 payloads
are addressed consistently. A loose object stores the same header and payload
inside one zlib stream at:

```text
.minigit/objects/<first two hash characters>/<remaining hash characters>
```

Writes use a temporary file in the destination directory, flush the compressed
bytes, and publish with `os.replace`. A failed write removes its temporary
file. Rewriting an existing object is not accepted blindly: duplicate writes
read and validate the existing object first.

## Trees and commits

Trees are object payloads containing one newline-terminated entry per line:

```text
<mode> <type> <object hash>\t<name>\n
```

Entries are sorted by name and validated for supported modes, object types,
lowercase SHA-1 hashes, unique names, and safe filenames. A nested tree points
to another tree object, while a blob entry points to file bytes.

A commit stores the root tree hash, zero or more parent hashes, author
metadata, and the message. Consequently, a commit points to the root tree,
the root tree points through subtrees to blobs, and the complete snapshot is
reachable through object IDs. Two-parent commits preserve both parent links.

## Pack layout and lookup

`pack_objects` reads selected objects through `read_object`, deduplicates the
requested hashes, and writes version-1 `.pack` and `.idx` files under
`.minigit/objects/pack/`. Each pack record preserves the object type and exact
uncompressed bytes, while zlib compresses the payload. The index maps each raw
20-byte SHA-1 to a record offset and length.

Publication is deliberately ordered: the temporary pack and index are flushed,
fsynced, and validated; the pack is renamed first; the index is renamed last.
Readers discover only published indexes. An interrupted publication can leave
an orphan pack, but cannot make an unpublished index visible. Loose originals
remain readable and are not pruned.

Reads check loose storage first. If no loose file exists, each published index
is validated before a matching packed record is decoded. The per-store index
metadata cache avoids repeating complete pack validation after the first
validated read. Current pack/index metadata is still discovered on every lookup,
and changed files invalidate the cache. Selected records remain decompressed,
length-checked, and hash-checked on every read.

## Error behavior and tradeoffs

- An invalid or missing hash raises `ObjectNotFoundError`.
- A missing object raises `ObjectNotFoundError`.
- Malformed compressed bytes, headers, lengths, types, hashes, indexes, or packs
  raise `ObjectCorruptError`.
- A corrupt loose object never falls back to a valid packed copy.
- A corrupt published index or pack is not silently skipped.

The design favors correctness and simple recovery over aggressive storage
reclamation. It keeps loose originals, has no pruning or garbage collection,
does not use delta compression, and retains a simple version-1 format. The
metadata cache reduces repeated parsing and decompression work but does not
cache object payloads, so loose-file latency and filesystem discovery remain
costs.

## Measured results

The shared Week 8 fixture used 512 deterministic blobs with 2,048-byte
payloads on Python 3.11.9 and Windows 10 build 26200. The recorded cached run
reported:

| Measurement | Result |
| --- | ---: |
| Loose object write time | 1,972.382 ms |
| Loose stored bytes | 27,534 |
| Loose fresh-store read time | 7,278.564 ms |
| Pack time | 128.798 ms |
| Retained loose bytes after packing | 27,534 |
| Pack bytes | 33,179 |
| Index bytes | 18,444 |
| Packed-only retained loose bytes | 0 |
| Packed-only fresh-store read time | 190.982 ms |

The controlled uncached comparison took 5,017.308 ms for the packed-only
fresh-store read. Three repeated packed reads took 28.802 seconds uncached
versus 0.688 seconds with cached metadata. These are measurements from the
shared fixture, not guarantees for every filesystem or repository size.

## What I would change next

First, measure cache memory and lookup behavior with many published packs before
choosing a cache bound or eviction policy. Next, add explicit lifecycle tooling
for pruning loose originals and obsolete packs only after reachability and
crash-recovery rules are specified. Delta compression and a format redesign
would be separate compatibility decisions, not incremental demo work.
