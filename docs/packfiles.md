# Packfile proposal

Week 4 still writes loose objects. This proposal defines the starting point for
Week 5; it does not change the current object API or introduce packed reads yet.

## Records and index

Store immutable `pack-<id>.pack` and `pack-<id>.idx` pairs under
`.minigit/objects/pack/`. Use a versioned pack header and an object count. Each
record contains its object type, uncompressed byte length, compressed byte
length, and independently zlib-compressed payload. Lengths and offsets use a
fixed-width unsigned integer encoding with a documented byte order.

The versioned index maps each full lowercase SHA-1 hash to its record's byte
offset and total length. Sort index entries by hash and reject duplicates,
invalid hashes, out-of-bounds offsets, overlapping records, and unsupported
versions. Independent record compression permits direct reads without
inflating the whole pack. Delta compression is outside the initial scope.

## Lookup and validation

Keep `read_object(hash) -> (type, bytes)` unchanged. Validate the hash before
lookup, try the loose-object path first, then search published pack indexes.
A missing loose file falls back to packs; a corrupt loose object raises
`ObjectCorruptError` rather than silently hiding the corruption. No match in
either storage form raises `ObjectNotFoundError`.

For packed reads, validate record boundaries, type, lengths, zlib end-of-stream,
and the existing SHA-1 calculation over `type + space + byte_length + NUL + data`.
Hashes and exact bytes must remain identical to loose storage. Keep
`has_object` based on `read_object`, propagating corruption errors. Duplicate
writes should also validate an existing packed object before returning.

## Publication and compatibility

Deduplicate requested hashes and read all source objects through `read_object`.
Write the pack and index to temporary files beside their final destinations.
Close and validate both, rename the pack first, then publish the index last.
Readers discover only finalized indexes, so an interrupted publication cannot
expose a partial pack. Use unique immutable pair names; an orphaned pack is
ignored until cleanup. Clean temporary files on failure.

Keep loose originals and allow new writes to remain loose. Pruning is a later
feature. Tests should cover packed-only and mixed storage, fresh store instances,
empty/binary/Unicode payloads, duplicate hashes, bad offsets, truncated records,
broken compression, wrong hashes, and interrupted publication.
