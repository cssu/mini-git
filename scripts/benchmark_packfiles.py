"""Measure loose and packed object storage on a deterministic shared fixture."""

import argparse
import cProfile
import io
import platform
import pstats
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from minigit.objects import ObjectStore


def storage_bytes(root: Path) -> int:
    return sum(
        path.stat().st_size
        for path in (root / ".minigit" / "objects").glob("*/*")
        if path.is_file()
    )


def read_all(store: ObjectStore, hashes: list[str], repeats: int = 1) -> float:
    started = time.perf_counter()
    for _ in range(repeats):
        for obj_hash in hashes:
            store.read_object(obj_hash)
    return (time.perf_counter() - started) * 1000


def read_uncached_index(
    store: ObjectStore, index_path: Path
) -> tuple[dict[str, tuple[int, int]], Path]:
    return store._read_index(index_path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--objects", type=int, default=512)
    parser.add_argument("--payload-size", type=int, default=2048)
    parser.add_argument("--profile", action="store_true")
    parser.add_argument("--no-cache", action="store_true")
    args = parser.parse_args()
    if args.objects <= 0 or args.payload_size <= 0:
        parser.error("--objects and --payload-size must be positive")
    if args.no_cache:
        ObjectStore._read_cached_index = read_uncached_index

    with tempfile.TemporaryDirectory(prefix="minigit-m4-fixture-") as directory:
        root = Path(directory)
        store = ObjectStore(root)
        hashes = []
        started = time.perf_counter()
        for index in range(args.objects):
            prefix = f"shared-fixture-{index:06d}:".encode()
            data = prefix + bytes((index % 251,)) * args.payload_size
            hashes.append(store.write_object(data, "blob"))
        write_ms = (time.perf_counter() - started) * 1000
        loose_bytes = storage_bytes(root)

        loose_read_ms = read_all(ObjectStore(root), hashes)
        started = time.perf_counter()
        pack_path, index_path = store.pack_objects(hashes)
        pack_ms = (time.perf_counter() - started) * 1000
        retained_loose_bytes = sum(
            path.stat().st_size
            for path in (root / ".minigit" / "objects").glob("*/*")
            if path.is_file() and path.parent.name != "pack"
        )
        pack_bytes = pack_path.stat().st_size
        index_bytes = index_path.stat().st_size

        for obj_hash in hashes:
            store._object_path(obj_hash).unlink()
        packed_read_ms = read_all(ObjectStore(root), hashes)

        print(f"python={sys.version.split()[0]}")
        print(f"platform={platform.platform()}")
        print(f"fixture_objects={len(hashes)}")
        print(f"fixture_payload_size={args.payload_size}")
        print(f"loose_write_ms={write_ms:.3f}")
        print(f"loose_bytes={loose_bytes}")
        print(f"loose_fresh_read_ms={loose_read_ms:.3f}")
        print(f"pack_ms={pack_ms:.3f}")
        print(f"packed_retained_loose_bytes={retained_loose_bytes}")
        print(f"pack_bytes={pack_bytes}")
        print(f"index_bytes={index_bytes}")
        print(f"packed_only_fresh_read_ms={packed_read_ms:.3f}")

        if args.profile:
            profiler = cProfile.Profile()
            profiler.enable()
            read_all(ObjectStore(root), hashes, repeats=3)
            profiler.disable()
            output = io.StringIO()
            pstats.Stats(profiler, stream=output).sort_stats("cumtime").print_stats(12)
            print(output.getvalue())

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
