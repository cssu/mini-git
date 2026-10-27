"""Module 2 - Index and working tree.

Owns the bridge between live files on disk and the immutable object store:
the index file, staging, diffing, checkout, and the text `status` and `diff` print.

Depends on: module 1.
Serves: module 3.

Build the `WorkingTree` class here, per the interface contract.
"""

import json
import os
import re
import stat
from dataclasses import dataclass, field
from typing import NamedTuple

from minigit.errors import MiniGitError, ObjectCorruptError


class IndexEntry(NamedTuple):
    mode: str
    hash: str
    path: str


class LineEdit(NamedTuple):
    """One change, in the coordinates of the OLD (base) list of lines.

    Replace `old_count` lines starting at index `start` with `new_lines`.
    A pure insert has old_count == 0; a pure delete has new_lines == [].
    """

    start: int
    old_count: int
    new_lines: list


@dataclass
class DiffResult:
    added: list
    deleted: list
    modified: list
    # path -> list[LineEdit] for text files, or None for binary / undecodable files.
    # A mode-only change has an empty list. Ignored by == so filename-list checks still work.
    line_diffs: dict = field(default_factory=dict, compare=False)


class _FileChange(NamedTuple):
    path: str
    status: str  # "added" | "deleted" | "modified"
    old_mode: str | None
    new_mode: str | None
    old_lines: list  # [] when binary
    edits: list  # [] when binary
    binary: bool
    content_changed: bool


# ---------------------------------------------------------------------------
# Myers line diff (pure functions - no files, no store)
# ---------------------------------------------------------------------------


def split_lines(text: str) -> list[str]:
    """Split on "\n" only, keeping the newline on each line.

    "a\nb" -> ["a\n", "b"]; a missing final newline stays visible as a last
    line without "\n". (str.splitlines would also split on \r, \x0c, etc.)
    """
    return re.findall(r"[^\n]*\n|[^\n]+", text)


def myers_diff(a: list, b: list) -> list[LineEdit]:
    """Return the shortest list of LineEdit that turns `a` into `b`."""
    return _group_ops(_shortest_path(a, b))


def _shortest_path(a: list, b: list) -> list:
    n, m = len(a), len(b)
    if n == 0 and m == 0:
        return []

    # v[k] = furthest x reached on diagonal k, where k = x - y.
    v = {1: 0}
    trace = []  # trace[d] = copy of v at the start of round d

    for d in range(n + m + 1):
        trace.append(dict(v))
        for k in range(-d, d + 1, 2):
            if k == -d or (k != d and v[k - 1] < v[k + 1]):
                x = v[k + 1]  # step down: insert a line from b
            else:
                x = v[k - 1] + 1  # step right: delete a line from a
            y = x - k
            while x < n and y < m and a[x] == b[y]:  # matching lines are free
                x += 1
                y += 1
            v[k] = x
            if x >= n and y >= m:
                return _backtrack(b, trace, d, n, m)
    raise AssertionError("unreachable")  # pragma: no cover


def _backtrack(b: list, trace: list, last_d: int, n: int, m: int) -> list:
    x, y = n, m
    ops = []  # ("del", old_index) or ("ins", old_index, line)

    for d in range(last_d, 0, -1):
        v = trace[d]
        k = x - y
        if k == -d or (k != d and v[k - 1] < v[k + 1]):
            prev_k = k + 1  # came from above: an insertion
        else:
            prev_k = k - 1  # came from the left: a deletion
        prev_x = v[prev_k]
        prev_y = prev_x - prev_k

        while x > prev_x and y > prev_y:  # walk back over matching lines
            x -= 1
            y -= 1

        if prev_k == k + 1:
            ops.append(("ins", prev_x, b[prev_y]))
        else:
            ops.append(("del", prev_x))
        x, y = prev_x, prev_y

    ops.reverse()
    return ops


def _group_ops(ops: list) -> list[LineEdit]:
    """Merge neighbouring del/ins ops into LineEdit(start, old_count, new_lines)."""
    edits = []
    current = None  # [start, old_count, new_lines]
    for op in ops:
        pos = op[1]
        if current is None or pos != current[0] + current[1]:
            if current is not None:
                edits.append(LineEdit(*current))
            current = [pos, 0, []]
        if op[0] == "del":
            current[1] += 1
        else:
            current[2].append(op[2])
    if current is not None:
        edits.append(LineEdit(*current))
    return edits


def apply_edits(base: list, edits: list[LineEdit]) -> list:
    """Apply LineEdits (base coordinates) to `base`; the inverse of myers_diff."""
    result = list(base)
    for edit in sorted(edits, key=lambda e: e.start, reverse=True):
        result[edit.start : edit.start + edit.old_count] = edit.new_lines
    return result


def _bytes_to_lines(data: bytes) -> list[str] | None:
    """Decode file bytes into lines, or None if the file looks binary."""
    if b"\0" in data:
        return None
    try:
        return split_lines(data.decode("utf-8"))
    except UnicodeDecodeError:
        return None


def walk_working_files(root: str):
    """Yield (full_path, repo-relative path with '/') for every file under `root`, skipping
    .minigit. The relative directory is worked out once per directory, not once per file."""
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d != ".minigit"]
        rel_dir = os.path.relpath(dirpath, root)
        prefix = "" if rel_dir == "." else rel_dir.replace(os.sep, "/") + "/"
        for filename in filenames:
            yield os.path.join(dirpath, filename), prefix + filename


class WorkingTree:
    def __init__(self, repo_path=".", store=None):
        self.root = repo_path
        self.index_path = f"{repo_path}/.minigit/index"

        if store is None:
            from minigit.objects import ObjectStore

            store = ObjectStore(repo_path)

        self.store = store

    @staticmethod
    def _file_mode(path: str) -> str:
        if os.stat(path).st_mode & stat.S_IXUSR:
            return "100755"
        if os.name == "nt" and os.path.splitext(path)[1].lower() in {".bat", ".cmd", ".exe", ".sh"}:
            return "100755"
        return "100644"

    def read_index(self) -> list[IndexEntry]:
        if not os.path.exists(self.index_path):
            return []

        entries = []
        with open(self.index_path, encoding="utf-8") as f:
            for number, line in enumerate(f, start=1):
                line = line.rstrip("\n")
                if not line:
                    continue
                try:
                    mode, hash_, path = line.split(" ", 2)
                except ValueError:
                    raise MiniGitError(
                        f"corrupt index: line {number} is not '<mode> <hash> <path>'; "
                        "delete .minigit/index and run 'minigit add' again"
                    ) from None
                entries.append(IndexEntry(mode, hash_, path))

        return sorted(entries, key=lambda e: e.path)

    def write_index(self, entries) -> None:
        entries = sorted(entries, key=lambda e: e.path)
        lines = [f"{e.mode} {e.hash} {e.path}" for e in entries]
        content = "\n".join(lines)
        if content:
            content += "\n"

        os.makedirs(os.path.dirname(self.index_path), exist_ok=True)
        with open(self.index_path, "w", encoding="utf-8") as f:
            f.write(content)

    def stage_file(self, path) -> None:
        full_path = os.path.join(self.root, path)

        rel_path = os.path.relpath(full_path, self.root).replace(os.sep, "/")
        self._validate_checkout_path(rel_path)
        if not os.path.isfile(full_path):
            entries = self.read_index()
            remaining = [entry for entry in entries if entry.path != rel_path]
            if os.path.lexists(full_path) or len(remaining) == len(entries):
                raise MiniGitError(
                    f"No such file: {path}; check the path, or run 'minigit status' to list files"
                )
            self.write_index(remaining)
            return

        with open(full_path, "rb") as f:
            data = f.read()

        blob_hash = self.store.write_object(data, "blob")

        mode = self._file_mode(full_path)

        entries = [e for e in self.read_index() if e.path != rel_path]
        entries.append(IndexEntry(mode, blob_hash, rel_path))
        self.write_index(entries)

    def build_tree_from_index(self) -> str:
        from minigit.objects import TreeEntry

        entries = self.read_index()

        root: dict = {}
        for entry in entries:
            parts = entry.path.split("/")
            current = root
            for part in parts[:-1]:
                current = current.setdefault(part, {})
            current[parts[-1]] = entry

        def write_dir(node: dict) -> str:
            tree_entries = []
            for name in sorted(node.keys()):
                value = node[name]
                if isinstance(value, dict):
                    subtree_hash = write_dir(value)
                    tree_entries.append(
                        TreeEntry(mode="40000", type="tree", hash=subtree_hash, name=name)
                    )
                else:
                    tree_entries.append(
                        TreeEntry(mode=value.mode, type="blob", hash=value.hash, name=name)
                    )
            return self.store.write_tree(tree_entries)

        return write_dir(root)

    def read_tree_entries(self, tree_hash: str) -> list[IndexEntry]:
        entries = []

        def walk(hash_, prefix):
            for te in self.store.read_tree(hash_):
                path = f"{prefix}/{te.name}" if prefix else te.name
                if te.type == "tree":
                    walk(te.hash, path)
                else:
                    entries.append(IndexEntry(te.mode, te.hash, path))

        walk(tree_hash, "")
        return sorted(entries, key=lambda e: e.path)

    def _make_change(self, path, status, old_mode, new_mode, old_data, new_data) -> _FileChange:
        old_lines = _bytes_to_lines(old_data)
        new_lines = _bytes_to_lines(new_data)
        binary = old_lines is None or new_lines is None
        edits = [] if binary else myers_diff(old_lines, new_lines)
        return _FileChange(
            path=path,
            status=status,
            old_mode=old_mode,
            new_mode=new_mode,
            old_lines=[] if binary else old_lines,
            edits=edits,
            binary=binary,
            content_changed=old_data != new_data,
        )

    def _compare_with_tree(self, tree_hash) -> list[_FileChange]:
        """Compare a tree (None = empty tree) with the files on disk. Read-only."""
        tree_entries = self.read_tree_entries(tree_hash) if tree_hash else []
        known_paths = {e.path for e in tree_entries}
        changes = []

        for entry in tree_entries:
            full_path = os.path.join(self.root, entry.path)
            if not os.path.isfile(full_path):
                _, old_data = self.store.read_object(entry.hash)
                changes.append(
                    self._make_change(entry.path, "deleted", entry.mode, None, old_data, b"")
                )
                continue
            with open(full_path, "rb") as f:
                new_data = f.read()
            new_mode = self._file_mode(full_path)
            if self.store.hash_object(new_data, "blob") == entry.hash and new_mode == entry.mode:
                continue
            _, old_data = self.store.read_object(entry.hash)
            changes.append(
                self._make_change(entry.path, "modified", entry.mode, new_mode, old_data, new_data)
            )

        for full_path, rel_path in walk_working_files(self.root):
            if rel_path in known_paths:
                continue
            with open(full_path, "rb") as f:
                new_data = f.read()
            new_mode = self._file_mode(full_path)
            changes.append(self._make_change(rel_path, "added", None, new_mode, b"", new_data))

        return sorted(changes, key=lambda c: c.path)

    def diff_working_tree_vs(self, tree_hash) -> DiffResult:
        """Compare the working files with a tree (None = empty tree).

        added / deleted / modified stay plain sorted path lists. line_diffs adds
        path -> list[LineEdit] (base = the tree's version), or None for binary files.
        """
        result = DiffResult([], [], [])
        for change in self._compare_with_tree(tree_hash):
            getattr(result, change.status).append(change.path)
            result.line_diffs[change.path] = None if change.binary else change.edits
        return result

    def _working_status(self) -> DiffResult:
        result = DiffResult([], [], [])
        entries = self.read_index()
        known_paths = {e.path for e in entries}

        for entry in entries:
            full_path = f"{self.root}/{entry.path}"
            if not os.path.isfile(full_path):
                result.deleted.append(entry.path)
            else:
                with open(full_path, "rb") as f:
                    data = f.read()
                current_hash = self.store.hash_object(data, "blob")
                current_mode = self._file_mode(full_path)
                if current_hash != entry.hash or current_mode != entry.mode:
                    result.modified.append(entry.path)

        for _, rel_path in walk_working_files(self.root):
            if rel_path not in known_paths:
                result.added.append(rel_path)

        return result

    def checkout(self, tree_hash, conflicted_paths=()) -> None:
        """Make the working files and the index match `tree_hash`.

        `conflicted_paths` (used by a merge) are written to disk like every other
        path - their blob in the tree holds the conflict markers - but their index
        entry stays at its pre-merge version (or is left out if there was none), so
        status reports them as "not staged" until `add` resolves them.
        """
        target_entries = self.read_tree_entries(tree_hash)
        target_by_path = {e.path: e for e in target_entries}

        conflicted = set(conflicted_paths)
        for path in sorted(conflicted - set(target_by_path)):
            raise MiniGitError(f"conflicted path is not in the target tree: {path}")

        # Reject unsafe paths
        blob_cache = {}
        for entry in target_entries:
            self._validate_checkout_path(entry.path)
            obj_type, data = self.store.read_object(entry.hash)
            if obj_type != "blob":
                raise ObjectCorruptError(entry.hash)
            blob_cache[entry.path] = data

        current_entries = self.read_index()
        current_by_path = {e.path: e for e in current_entries}

        # Refuse if any tracked file has local edits or is missing.
        for entry in current_entries:
            self._validate_checkout_path(entry.path)
            full_path = os.path.join(self.root, entry.path)
            if not os.path.isfile(full_path):
                raise MiniGitError(
                    f"local changes would be lost: {entry.path} is missing; "
                    "restore the file or commit its deletion first"
                )
            with open(full_path, "rb") as f:
                data = f.read()
            disk_hash = self.store.hash_object(data, "blob")
            disk_mode = self._file_mode(full_path)
            if disk_hash != entry.hash or disk_mode != entry.mode:
                raise MiniGitError(
                    f"local changes would be lost: {entry.path}; commit or undo your edits first"
                )

        # Preflight every parent and destination before deleting any tracked file.
        # Directories may become files only when all their contents are tracked.
        removable = set(current_by_path) - set(target_by_path)
        tracked_dirs = set()
        for path in removable:
            parent = os.path.dirname(path)
            while parent:
                tracked_dirs.add(parent)
                parent = os.path.dirname(parent)

        for entry in target_entries:
            parts = entry.path.split("/")
            for i in range(1, len(parts)):
                parent = "/".join(parts[:i])
                full_parent = os.path.join(self.root, parent)
                if os.path.lexists(full_parent) and not os.path.isdir(full_parent):
                    if parent not in removable:
                        raise MiniGitError(
                            f"untracked path would be overwritten: {parent}; "
                            "move or delete it first"
                        )

            full_path = os.path.join(self.root, entry.path)
            if os.path.isdir(full_path):
                if entry.path not in tracked_dirs:
                    raise MiniGitError(
                        f"untracked path would be overwritten: {entry.path}; "
                        "move or delete it first"
                    )
                for directory, dirnames, filenames in os.walk(full_path):
                    for name in dirnames + filenames:
                        child = os.path.join(directory, name)
                        relative = os.path.relpath(child, self.root).replace(os.sep, "/")
                        if os.path.islink(child) or relative not in removable | tracked_dirs:
                            raise MiniGitError(
                                f"untracked path would be overwritten: {relative}; "
                                "move or delete it first"
                            )
            elif os.path.lexists(full_path) and entry.path not in current_by_path:
                raise MiniGitError(
                    f"untracked path would be overwritten: {entry.path}; move or delete it first"
                )

        # Remove tracked files the target doesn't have, then prune
        for entry in current_entries:
            if entry.path not in target_by_path:
                full_path = os.path.join(self.root, entry.path)
                if os.path.isfile(full_path):
                    os.remove(full_path)
                self._prune_empty_dirs(os.path.dirname(full_path))

        # Create directories and write every target file's bytes/mode.
        for entry in target_entries:
            full_path = os.path.join(self.root, entry.path)
            os.makedirs(os.path.dirname(full_path), exist_ok=True)
            with open(full_path, "wb") as f:
                f.write(blob_cache[entry.path])
            os.chmod(full_path, 0o755 if entry.mode == "100755" else 0o644)

        # Now matched - except conflicted paths, which keep their pre-merge entry.
        index_entries = []
        for entry in target_entries:
            if entry.path not in conflicted:
                index_entries.append(entry)
            elif entry.path in current_by_path:
                index_entries.append(current_by_path[entry.path])
        self.write_index(index_entries)

    def _validate_checkout_path(self, path: str) -> None:
        if (
            os.path.isabs(path)
            or any(part in {"", ".", ".."} for part in path.split("/"))
            or any(char in path for char in "\\\0\r\n")
        ):
            raise MiniGitError(f"unsafe path: {display_path(path)}")
        if any(part == ".minigit" for part in path.split("/")):
            raise MiniGitError(f"path under .minigit: {path}")

        parts = path.split("/")
        current = self.root
        for part in parts:
            current = os.path.join(current, part)
            if os.path.islink(current):
                raise MiniGitError(f"path passes through symlink: {path}")

    def _prune_empty_dirs(self, dir_path: str) -> None:
        root = os.path.realpath(self.root)
        current = os.path.realpath(dir_path)
        while current != root and os.path.commonpath([root, current]) == root:
            if not os.path.isdir(current) or os.listdir(current):
                break
            os.rmdir(current)
            current = os.path.dirname(current)


def cmd_add(args) -> int:
    """Stage a path. During a merge, that path is marked resolved."""
    wt = WorkingTree()
    from minigit.commits import CommitManager

    manager = CommitManager(wt.root, store=wt.store, tree=wt)
    full_path = os.path.join(wt.root, args.path)
    rel_path = os.path.relpath(full_path, wt.root).replace(os.sep, "/")
    wt._validate_checkout_path(rel_path)
    pending = manager._read_pending_merge()
    # A delete/modify conflict can have no pre-merge index entry. Explicitly
    # adding its absent path still resolves the conflict as a deletion.
    missing_conflict = (
        pending is not None and rel_path in pending[3] and not os.path.lexists(full_path)
    )
    if missing_conflict:
        wt.write_index([entry for entry in wt.read_index() if entry.path != rel_path])
    else:
        wt.stage_file(args.path)
    manager.mark_path_resolved(rel_path)
    return 0


# ---------------------------------------------------------------------------
# Output formatting for `status` and `diff` (plain text; no Myers code in here)
# ---------------------------------------------------------------------------

CONTEXT_LINES = 3  # unchanged lines shown around each change in a diff hunk


def display_path(path: str, prefix: str = "") -> str:
    """Return `prefix + path` the way status and diff print it.

    Ordinary paths (spaces and Unicode included) are printed as they are. A path with
    a control character or a backslash is printed as a quoted, escaped string, so one
    path always stays on one line and the output reads the same everywhere.
    """
    text = prefix + path
    if any(not char.isprintable() or char == "\\" for char in text):
        return json.dumps(text)
    return text


def format_status(header, unborn, unresolved, staged, not_staged, untracked) -> list[str]:
    """Return the lines `minigit status` prints. Empty sections are left out."""
    lines = [header]
    if unborn:
        lines.append("no commits yet")
    sections = (
        ("unresolved (merge conflict):", unresolved),
        ("staged:", staged),
        ("not staged:", not_staged),
        ("untracked:", untracked),
    )
    shown = False
    for title, paths in sections:
        if paths:
            lines.append(title)
            lines.extend(f"  {display_path(path)}" for path in sorted(paths))
            shown = True
    if not shown:
        lines.append("clean")
    return lines


def _head_header(head) -> str:
    """First status line. `head` is what CommitManager.read_head() returned."""
    if head is None:
        return "On branch main"  # no HEAD file yet: same default as CommitManager
    if len(head) == 40 and all(char in "0123456789abcdef" for char in head):
        return f"HEAD detached at {head[:7]}"
    return f"On branch {head}"


def _unresolved_merge_paths(manager) -> list[str]:
    """Paths of a pending merge that `add` has not resolved yet (M3 owns the merge state)."""
    pending = manager._read_pending_merge()
    return sorted(pending[3]) if pending else []


def cmd_status(args) -> int:
    """minigit status: branch, then staged (HEAD tree vs index), not staged (index vs
    working files) and untracked files. Read-only."""
    wt = WorkingTree()
    index_entries = wt.read_index()
    index_by_path = {e.path: e for e in index_entries}

    from minigit.commits import CommitManager

    manager = CommitManager(wt.root, store=wt.store, tree=wt)
    head_tree_hash = manager.get_head_tree()
    head_entries = wt.read_tree_entries(head_tree_hash) if head_tree_hash else []
    head_by_path = {e.path: e for e in head_entries}

    # staged: HEAD tree vs index
    staged_paths = set()
    for path, entry in index_by_path.items():
        head_entry = head_by_path.get(path)
        if head_entry is None or head_entry.hash != entry.hash or head_entry.mode != entry.mode:
            staged_paths.add(path)
    for path in head_by_path:
        if path not in index_by_path:
            staged_paths.add(path)

    # not staged: index vs disk
    not_staged_paths = set()
    for entry in index_entries:
        full_path = os.path.join(wt.root, entry.path)
        if not os.path.isfile(full_path):
            not_staged_paths.add(entry.path)
        else:
            with open(full_path, "rb") as f:
                data = f.read()
            current_hash = wt.store.hash_object(data, "blob")
            current_mode = wt._file_mode(full_path)
            if current_hash != entry.hash or current_mode != entry.mode:
                not_staged_paths.add(entry.path)

    # A path with an unresolved merge conflict is listed once, under its own heading.
    unresolved = set(_unresolved_merge_paths(manager))
    not_staged_paths -= unresolved

    # untracked: on disk, absent from the index
    known_paths = set(index_by_path) | unresolved
    untracked_paths = set()
    for _, rel_path in walk_working_files(wt.root):
        if rel_path not in known_paths:
            untracked_paths.add(rel_path)

    lines = format_status(
        _head_header(manager.read_head()),
        head_tree_hash is None,
        sorted(unresolved),
        staged_paths,
        not_staged_paths,
        untracked_paths,
    )
    for line in lines:
        print(line)
    return 0


def _hunk_range(start: int, count: int) -> str:
    """`start` is a 0-based line index. A non-empty range is written 1-based
    ("3,2" = lines 3 and 4); an empty range is written as the line before it ("2,0")."""
    return f"{start if count == 0 else start + 1},{count}"


def _diff_lines(prefix: str, line: str) -> list[str]:
    out = [prefix + line.removesuffix("\n")]
    if not line.endswith("\n"):
        out.append("\\ No newline at end of file")
    return out


def _group_edits(edits: list, context: int) -> list[list]:
    """Group edits that are close enough for their context lines to touch or overlap."""
    groups: list[list] = []
    for edit in edits:
        if groups:
            last = groups[-1][-1]
            if edit.start - (last.start + last.old_count) <= 2 * context:
                groups[-1].append(edit)
                continue
        groups.append([edit])
    return groups


def _render_hunk(old_lines: list, group: list, shift: int, context: int):
    """Return (hunk lines, change in line count). `shift` = lines added minus removed so far."""
    first, last = group[0], group[-1]
    old_from = max(0, first.start - context)
    old_to = min(len(old_lines), last.start + last.old_count + context)
    body: list[str] = []
    removed = added = 0
    position = old_from
    for edit in group:
        for line in old_lines[position : edit.start]:
            body.extend(_diff_lines(" ", line))
        for line in old_lines[edit.start : edit.start + edit.old_count]:
            body.extend(_diff_lines("-", line))
        for line in edit.new_lines:
            body.extend(_diff_lines("+", line))
        removed += edit.old_count
        added += len(edit.new_lines)
        position = edit.start + edit.old_count
    for line in old_lines[position:old_to]:
        body.extend(_diff_lines(" ", line))
    old_count = old_to - old_from
    new_count = old_count - removed + added
    header = (
        f"@@ -{_hunk_range(old_from, old_count)} +{_hunk_range(old_from + shift, new_count)} @@"
    )
    return [header, *body], added - removed


def _format_change(change: _FileChange, context: int = CONTEXT_LINES) -> list[str]:
    """Render one file's changes as printable lines.

    Hunk header: `@@ -<old start>,<old count> +<new start>,<new count> @@`, with the
    old numbers from the HEAD version and the new ones from the working file.
    Each hunk shows `context` unchanged lines around the changes; lines start with
    " " (unchanged), "-" (removed) or "+" (added).
    """
    path = change.path
    out = [f"diff --minigit {display_path(path, 'a/')} {display_path(path, 'b/')}"]

    if change.status == "added":
        out.append(f"new file mode {change.new_mode}")
    elif change.status == "deleted":
        out.append(f"deleted file mode {change.old_mode}")
    elif change.old_mode != change.new_mode:
        out.append(f"old mode {change.old_mode}")
        out.append(f"new mode {change.new_mode}")

    if change.binary:
        if change.status == "added":
            out.append(f"binary file added: {display_path(path)}")
        elif change.status == "deleted":
            out.append(f"binary file deleted: {display_path(path)}")
        elif change.content_changed:
            out.append(f"binary file changed: {display_path(path)}")
        return out
    if not change.edits:
        return out  # mode-only change, or an empty file added / deleted

    out.append("--- /dev/null" if change.status == "added" else f"--- {display_path(path, 'a/')}")
    out.append("+++ /dev/null" if change.status == "deleted" else f"+++ {display_path(path, 'b/')}")

    shift = 0
    for group in _group_edits(change.edits, context):
        hunk, delta = _render_hunk(change.old_lines, group, shift, context)
        out.extend(hunk)
        shift += delta
    return out


def cmd_diff(args) -> int:
    """minigit diff: HEAD tree vs the working files (unborn HEAD = empty tree). Read-only."""
    wt = WorkingTree()

    from minigit.commits import CommitManager

    head_tree_hash = CommitManager(wt.root, store=wt.store, tree=wt).get_head_tree()
    for change in wt._compare_with_tree(head_tree_hash):
        for line in _format_change(change):
            print(line)
    return 0


def register_index_commands(subparsers) -> None:
    add_parser = subparsers.add_parser("add", help="stage a file")
    add_parser.add_argument("path")
    add_parser.set_defaults(handler=cmd_add)

    status_parser = subparsers.add_parser(
        "status", help="show the branch and staged, not staged and untracked files"
    )
    status_parser.set_defaults(handler=cmd_status)

    diff_parser = subparsers.add_parser(
        "diff", help="show line changes: the HEAD tree compared with the working files"
    )
    diff_parser.set_defaults(handler=cmd_diff)
