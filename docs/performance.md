# Working-tree performance (`status` and `diff`)

Week 8, Module 2. Numbers below are best-of-5 wall-clock seconds for one call of `cmd_status`
and `cmd_diff` on a generated repository.

## How it was measured

- Repository: 3000 small files (40 lines each, in 50 folders) plus one 20000-line file, all
  committed.
- Benchmark: `bench_working_tree.py` (`--profile` shows where the time goes).
- Machine: MacBook Air (Apple silicon), Python 3.13.5.
- Command: `PYTHONPATH=. python3 bench_working_tree.py`

## Before

`main`'s code. Old and new were run alternately, one right after the other, so both saw the same
machine conditions.

| Situation | status | diff |
| --- | --- | --- |
| nothing changed | 0.133 s | 0.122 s |
| 1 small file changed | 0.125 s | 0.121 s |
| 50 small files changed | 0.123 s | 0.125 s |
| + big file, 2 edits | 0.123 s | 0.131 s |
| + big file, 500 scattered edits | 0.125 s | 0.223 s |

## Where the time went

With 3000 files, `status` and `diff` take about the same time however many files changed, so
the cost is per file visited, not per change. The profile of the "nothing changed" case showed:

- `os.getcwd` called 6003 times (twice per file), about 0.05 s of the 0.19 s profiled run.
  Cause: the file walk called `os.path.relpath` once for every file. `WorkingTree` uses the
  relative root `"."`, so each call asked the operating system for the current directory
  twice.
- Reading the files (`open`, 3055 calls, about 0.03 s) and the executable-bit check
  (`os.access`, 3001 calls, about 0.01 s). Both are needed to decide whether a file changed.
- Line diffing (Myers) only matters for the big file with 500 scattered edits, where `diff`
  rises from about 0.13 s to 0.23 s. That cost is real work, not repeated work.

## Change made

One bounded fix: the three file walks (`status`, `diff`, and the added-file check) now share
`walk_working_files()`, which works out the relative folder once per folder (about 50 times)
instead of once per file (3000 times).

Nothing else changed. Every file is still read and hashed, so a same-size edit with the
original timestamp is still found (regression test:
`test_same_size_edit_with_preserved_timestamp_is_still_found`). Nothing is cached, and
`status` and `diff` still write nothing to the repository.

## After

Same command, on the code with `walk_working_files()`.

| Situation | status | diff |
| --- | --- | --- |
| nothing changed | 0.070 s | 0.064 s |
| 1 small file changed | 0.066 s | 0.064 s |
| 50 small files changed | 0.066 s | 0.066 s |
| + big file, 2 edits | 0.070 s | 0.071 s |
| + big file, 500 scattered edits | 0.066 s | 0.166 s |

With many files, `status` and `diff` take about 45% less time (about 0.12 s down to 0.066 s).
Every row improves by roughly the same 0.055 s, which matches the time the profile showed going
to the 6003 `getcwd` calls. The 500-edit `diff` improves by less (0.223 s to 0.166 s) because
most of its time is the Myers diff, which was not changed.

## Re-run

Run these numbers again with the shared benchmark (#47) once it is merged.
