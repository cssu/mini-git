# Week 8 storage performance

## Reproduction

The M1 storage fixture, separate from the shared project benchmark in #47, uses
[`scripts/benchmark_packfiles.py`](../scripts/benchmark_packfiles.py): 512
deterministic blob objects, each with a 2,048-byte repeated payload plus a
unique 22-byte prefix (2,070 bytes total). The benchmark uses a fresh temporary
repository, reads through a newly constructed `ObjectStore`, and removes loose objects before the
packed-only read. It reports retained loose bytes separately from pack and
index bytes.

On Windows from the repository root:

```powershell
.\.venv\Scripts\python.exe scripts\benchmark_packfiles.py --profile
.\.venv\Scripts\python.exe scripts\benchmark_packfiles.py --no-cache --profile
```

`--no-cache` is a controlled comparison on the current code: it reparses and
validates the published index and complete pack for every lookup. It is not
an exact historical revision. The benchmark does not
change the object format, enable pruning, or use delta compression.

## Earlier reported storage measurements

These Windows measurements were reported with #65. The exact measured source
revision was not recorded, so they are historical observations rather than a
reproducible revision-to-revision baseline. Current revision-pinned measurements
and commands are in [the shared performance report](performance.md#reproduction-of-71).

Environment for the recorded run:

- Python 3.11.9
- Windows 10, build 26200
- 512 objects, 2,048-byte payload parameter
- One fresh-store read of every object in each storage mode

Cached run (`--profile`):

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

The loose and packed byte totals are not directly interchangeable: packing
does not prune the loose originals, and the current format has no delta
compression. The packed-only footprint in this run is 51,623 bytes
(33,179-byte pack plus 18,444-byte index), while the loose-only footprint is
27,534 bytes.

## Measured fix and profile

The controlled comparison (`--no-cache`) reported 5,017.308 ms for the
packed-only fresh-store read. Its repeated-read profile (three passes) spent
28.802 seconds total, including 25.959 seconds in complete pack validation and
21.207 seconds in repeated record decoding.

The fix caches metadata only after a published index and its matching pack
have passed the existing validation. Each lookup still discovers the current
`pack-*.idx` directory entries, and the current cache signature includes index and
pack size, nanosecond modification time, and inode. The cache retains at most
64 parsed indexes and evicts the least recently used entry. These bounds and
inode checks were added in #71, after the original caching implementation.
A newly published pack is therefore found, and changed signature fields
invalidate cached validation. Selected
records continue to be decoded and hash-checked on every read.

The cached repeated-read profile took 0.688 seconds for the same three passes.
The profile's remaining hot path is filesystem discovery and metadata checks
(`Path.glob`/`Path.stat`), not full-pack validation.

The normal object test suite also covers a small correctness fixture with empty,
binary, and Unicode blobs; nested trees; two-parent commits; overlapping
objects across multiple packs; packed-only and mixed reads; duplicate requests;
fresh store instances; and newly published packs. Existing corruption and
publication tests cover invalid indexes and records, truncation and zlib
failures, wrong hashes, and failed publication. These tests assert behavior,
not wall-clock thresholds.

## Final checkpoint reconciliation

The final integrated Windows run at revision
`2e875865a312479abecb475a77fddee73fcd5667` measured 270.926 ms for a cached
packed-only fresh read and 4,793.757 ms with `--no-cache`; the corresponding
three-pass profile took 0.874 s and 23.205 s. The 512-object fixture and
Python/Windows environment are recorded in
[the shared performance report](performance.md#final-week-10-windows-checkpoint).
These values replace neither historical Mac nor Windows observations; they
are the exact-revision evidence for the storage segment.

## Remaining findings and scope

- Loose reads remain sensitive to Windows filesystem latency; this change does
  not add a loose-object cache.
- Packed storage is larger when loose originals are retained. Pruning and
  delta compression remain outside this issue.
- Pack/index format redesign remains outside this issue.
- Full-project/network measurements remain tracked in #47 and #58; this
  blob-only fixture does not complete those checkpoints.
- See [packfile validation](packfiles.md#lookup-and-validation) for the metadata
  cache assumptions and selected-record integrity checks.
