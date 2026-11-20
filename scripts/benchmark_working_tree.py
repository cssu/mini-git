"""Time `minigit status` and `minigit diff` on a generated repository. Not part of CI.

Run from the repository root with the project's virtual environment active, or with
PYTHONPATH=. set:

    python3 scripts/benchmark_working_tree.py              # timings
    python3 scripts/benchmark_working_tree.py --profile    # timings + where the time goes

It builds a throwaway repository in a temporary folder, so it never touches your own work.
"""

import argparse
import contextlib
import cProfile
import io
import os
import pstats
import random
import tempfile
import time

from minigit.commits import CommitManager
from minigit.index import IndexEntry, WorkingTree, cmd_diff, cmd_status

SMALL_FILES = 3000
BIG_FILE_LINES = 20000


def build_repo(root):
    """3000 small files and one big file, all committed."""
    os.chdir(root)
    os.makedirs(".minigit")
    for i in range(SMALL_FILES):
        folder = f"d{i % 50}"
        os.makedirs(folder, exist_ok=True)
        with open(f"{folder}/f{i}.txt", "w") as f:
            f.write("".join(f"line {j} of {i}\n" for j in range(40)))
    random.seed(1)
    with open("big.txt", "w") as f:
        f.write("".join(f"big line {j} {random.randint(0, 50)}\n" for j in range(BIG_FILE_LINES)))

    tree = WorkingTree()
    entries = []
    for folder, dirs, files in os.walk("."):
        dirs[:] = [d for d in dirs if d != ".minigit"]
        for name in files:
            path = os.path.relpath(os.path.join(folder, name), ".").replace(os.sep, "/")
            with open(path, "rb") as f:
                blob = tree.store.write_object(f.read(), "blob")
            entries.append(IndexEntry("100644", blob, path))
    tree.write_index(entries)  # faster than 3000 `add` calls, which is not what we measure
    CommitManager().create_commit(tree.build_tree_from_index(), [], "bench", "first")


def edit_small_files(count):
    for i in range(count):
        with open(f"d{i % 50}/f{i}.txt", "a") as f:
            f.write("edit\n")


def edit_big_file(every):
    with open("big.txt") as f:
        lines = f.readlines()
    for k in range(0, len(lines), every):
        lines[k] = f"changed {k}\n"
    with open("big.txt", "w") as f:
        f.writelines(lines)


def run(command, profile=False, repeat=5):
    """Best of `repeat` runs, in seconds. Output is thrown away."""
    best = None
    stats = None
    for _ in range(repeat):
        with contextlib.redirect_stdout(io.StringIO()):
            profiler = cProfile.Profile() if profile else None
            start = time.perf_counter()
            if profiler:
                profiler.enable()
            command(None)
            if profiler:
                profiler.disable()
            elapsed = time.perf_counter() - start
        if best is None or elapsed < best:
            best, stats = elapsed, profiler
    return best, stats


def show_profile(label, profiler):
    out = io.StringIO()
    pstats.Stats(profiler, stream=out).strip_dirs().sort_stats("tottime").print_stats(6)
    print(f"\n--- where the time goes: {label} (sorted by time spent inside each function) ---")
    for line in out.getvalue().splitlines():
        if line.strip() and "Ordered by" not in line:
            print(line[:120])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", action="store_true", help="also show the slowest functions")
    args = parser.parse_args()

    with tempfile.TemporaryDirectory() as root:
        build_repo(root)
        print(f"repo: {SMALL_FILES} small files + one {BIG_FILE_LINES}-line file\n")
        print(f"{'situation':44s} {'status':>9s} {'diff':>9s}")

        def row(label):
            status_time, status_prof = run(cmd_status, args.profile)
            diff_time, diff_prof = run(cmd_diff, args.profile)
            print(f"{label:44s} {status_time:8.3f}s {diff_time:8.3f}s")
            if args.profile:
                show_profile(f"status, {label}", status_prof)
                show_profile(f"diff, {label}", diff_prof)

        row("nothing changed")
        edit_small_files(1)
        row("1 small file changed")
        edit_small_files(50)
        row("50 small files changed")
        edit_big_file(every=10000)
        row("+ big file, 2 edits")
        edit_big_file(every=40)
        row("+ big file, 500 scattered edits")


if __name__ == "__main__":
    main()
