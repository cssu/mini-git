# Module 2: index and working tree

## What was built

Module 2 owns the index and everything that compares the three places a file can live: the last commit (HEAD), the index (`.minigit/index`), and the files on disk. In `minigit/index.py` it provides:

- `add`, which snapshots a file's bytes and mode into the index.
- `status`, which reports staged, not staged, untracked, and unresolved (merge conflict) paths.
- `diff`, which shows line changes between HEAD and the working files.
- Safe `checkout`, which switches branches only when no local work can be lost.

Tests are in `tests/test_index.py` and `tests/test_checkout_safety.py`, plus the integration tests that run the CLI.

## Index snapshots

The index is a text file with one line per tracked path: `<mode> <hash> <path>`. `add` writes the file's bytes as a blob through Module 1's `ObjectStore` and records the hash, so the index holds the content as it was when `add` ran. Editing the file afterwards does not change what is staged. `commit` builds its tree from the index, not from the disk.

## Building nested trees

Index paths are flat (`src/util/a.py`), but commits need nested trees. The index entries are grouped by folder and written bottom-up: blobs, then each subtree, then the root tree. Entries are sorted by name so the same index always produces the same tree hash.

## Three-state status model

`status` makes two comparisons and one scan:

- staged: HEAD tree against the index.
- not staged: the index against the files on disk.
- untracked: files on disk that are not in the index.
- unresolved (merge conflict): paths from the pending merge state.

Comparisons use content hash and mode only. Timestamps and sizes are never trusted, so a same-size edit with the original timestamp is still found (`test_same_size_edit_with_preserved_timestamp_is_still_found`). A path can be in both staged and not staged at once, which is what the demo's first part shows.

## Myers diff and its reuse by merge

Line diffs use a handwritten Myers O(ND) algorithm. It returns `LineEdit(start, old_count, new_lines)` values in coordinates of the original text, and `apply_edits` replays them. Text is split on `\n` only. A file is binary if it contains a NUL byte or is not valid UTF-8, and binary files get a summary instead of hunks.

Module 3's merge reuses this: it diffs each side against the base and combines the edit lists, reporting a conflict when two edits overlap. Because both modules share one diff, a conflict is reported on exactly the lines `diff` would show as changed.

## Diff output

`diff` compares HEAD with the working files and ignores the index. Changes are grouped into hunks with three lines of context, and each hunk header is `@@ -old,count +new,count @@`. The output also handles a missing final newline (`\ No newline at end of file`), mode changes, binary files, and paths with unusual characters (`display_path` quotes them).

## Checkout safety

`checkout` validates before it writes:

1. Every path in the target tree is checked: absolute paths, `.` or `..` parts, backslashes, NUL, CR/LF, `.minigit`, and symlinks are rejected.
2. Tracked files with local edits or missing files block the switch.
3. Untracked files that the target would overwrite, including a file or folder blocking a path, block the switch.
4. Only after all checks pass are files written, and the index is written last.

Each error ends with the next step after a semicolon, for example `local changes would be lost: a.txt; commit or undo your edits first`. During a merge, conflicted paths keep their pre-merge index entry so they are not mistaken for local edits.

## Boundary with Module 3

Module 3 owns merge state. `add` asks it whether a path is part of a pending merge and tells it when the path has been resolved; a deleted path counts as resolved when it is listed as conflicted. `status` and `commit` read the unresolved list from the same state. Module 2 currently reads this through the private `_read_pending_merge()`.

## Measured results

The full tables, machine, and method are in `docs/performance.md`. On a generated repository of 3000 small files plus one 20000-line file (best of 5, Python 3.13.5, MacBook Air with Apple silicon), `status` and `diff` fell from about 0.12 s to about 0.066 s. A profile showed about 6000 `os.getcwd` calls from computing a relative path for every file. `walk_working_files()` now computes it once per folder. Every file is still read and hashed, nothing is cached, and the commands still write nothing. A 500-edit diff improved less (0.223 s to 0.166 s) because most of its time is the Myers diff.

These numbers come from a Module 2 fixture, not the shared project benchmark in #47/#58. The benchmark script itself is not yet checked in; that is tracked for the final handoff.

## Tradeoffs

- Reading and hashing every file keeps `status` correct but means its cost grows with repository size. There is no stat cache.
- The index is rewritten on each `add`, which is simple and atomic but not cheap for very large indexes.
- Diffs are line-based only, with no rename detection and no word-level output.
- Checkout refuses rather than merges local edits, which is safe but less convenient.

## Lessons learned

This module introduced a lot of concepts I had not worked with before, and the Myers diff was the biggest of them. I knew what a diff looks like, but not how a program finds the smallest set of changes. Working out how it walks along diagonals, and how it records each step so the path can be traced back was really interesting. 

The second lesson was to measure before optimizing. The profile showed something I would not have guessed: the same function, `os.getcwd`, was called about 6000 times because a relative path was recomputed for every file. Computing it once per folder removed that cost with a small change, and kept `status` correct with no cache to go stale.

## What I would change next

First, replace `_read_pending_merge()` with Module 3's public `get_merge_status()` so Module 2 no longer depends on a private method. Second, make `add` refuse, or warn about, a file that still contains conflict markers.
