"""Module 4 - Remotes and networking.

Owns push and pull over a TCP socket: object and ref exchange between two
repositories, authentication, and push conflict detection.

Depends on: modules 1 and 3.
Serves: nobody - this is the top of the stack.

Build the `RemoteClient` class here, per the interface contract.
"""

import os
import socket
import sys
from contextlib import contextmanager

from minigit.commits import CommitManager
from minigit.errors import (
    MiniGitError,
    NetworkProtocolError,
    ObjectCorruptError,
    ObjectNotFoundError,
)
from minigit.objects import ObjectStore


def send_line(sock, text: str) -> None:
    """Send one line of the wire protocol,"""

    sock.sendall((text + "\n").encode())


def receive_line(sock, buf: bytearray) -> str:
    """Read one `\\n`-terminated line from `sock`.

    `buf` is the connection's leftover-bytes buffer, owned by the caller and
    reused across calls on the same socket: a `recv()` can return two lines
    at once (the second one waits here for the next call) or half a line
    (we keep reading until the rest arrives).
    """

    while b"\n" not in buf:
        chunk = sock.recv(4096)
        if not chunk:
            raise NetworkProtocolError("connection closed mid-line")
        buf.extend(chunk)

    line, _, rest = buf.partition(b"\n")
    buf[:] = rest
    try:
        return line.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise NetworkProtocolError("protocol line is not UTF-8") from exc


def recv_exact(sock, buf: bytearray, size: int) -> bytes:
    """Read exactly `size` bytes from `sock`, sharing `buf` with `receive_line`.

    Same buffer contract: bytes past the `size`th belong to whatever message
    comes next on this connection and are left in `buf` for that call to
    consume, instead of being read (and discarded) here.
    """

    while len(buf) < size:
        chunk = sock.recv(4096)
        if not chunk:
            raise NetworkProtocolError("connection closed mid-message")
        buf.extend(chunk)

    data = bytes(buf[:size])
    del buf[:size]
    return data


def _valid_hash(value: str) -> bool:
    """Return whether `value` is a full 40-character lowercase hex object hash."""

    return len(value) == 40 and all(c in "0123456789abcdef" for c in value)


def _valid_branch(name: str) -> bool:
    """Return whether `name` is safe to use as a ref path under `refs/heads/`.

    Rejects empty names, `.`/`..`/empty path segments (traversal), and
    characters that would break a ref file or a protocol line.
    """

    return (
        bool(name)
        and all(part not in {"", ".", ".."} for part in name.split("/"))
        and not any(char in name for char in "\0\\\r\n ")
    )


def _rejection_reason(reply: str) -> str:
    """Strip the `ERR ` prefix off a server reply so it reads well in a CLI error."""

    command, _, reason = reply.partition(" ")
    return reason if command == "ERR" and reason else reply


def _has_object(store, obj_hash: str) -> bool:
    """Return whether `obj_hash` already exists in `store`.

    Prefers `store.has_object` (Module 1, #22) once the injected store has
    grown one; falls back to a real read so this works against `main` today.
    """

    has_object = getattr(store, "has_object", None)
    if has_object is not None:
        return has_object(obj_hash)
    try:
        store.read_object(obj_hash)
        return True
    except ObjectNotFoundError:
        return False


def _is_ancestor(commits, ancestor_hash: str, descendant_hash: str) -> bool:
    """Return whether `ancestor_hash` is reachable from `descendant_hash`.

    Prefers `commits.is_ancestor` (Module 3, #24) once the injected commits
    manager has grown one; falls back to `walk_history` so this works
    against `main` today.
    """

    is_ancestor = getattr(commits, "is_ancestor", None)
    if is_ancestor is not None:
        return is_ancestor(ancestor_hash, descendant_hash)
    return ancestor_hash in commits.walk_history(descendant_hash)


class RemoteClient:
    """Push and pull commits between two minigit repos over a TCP connection."""

    def __init__(self, repo_path=".", store=None, commits=None):
        self.repo_path = repo_path
        self.config_path = os.path.join(self.repo_path, ".minigit", "config")
        self.store = store if store is not None else ObjectStore(self.repo_path)
        self.commits = (
            commits if commits is not None else CommitManager(self.repo_path, store=self.store)
        )

    def _parse_address(self, address: str) -> tuple[str, int]:
        """split a string address by host part(string) and the port part(integer)"""

        parts = address.rsplit(":", 1)
        if len(parts) != 2:
            raise NetworkProtocolError(f"address must be host:port, got {address!r}")

        host, port = parts
        if host and port:
            if port.isnumeric():
                int_port = int(port)
                if 1 <= int_port <= 65535:
                    return host, int_port
                else:
                    raise NetworkProtocolError(f"port out of range 1-65535: {port}")
            else:
                raise NetworkProtocolError(f"port is not a number: {port!r}")
        else:
            raise NetworkProtocolError(f"address must be host:port, got {address!r}")

    def _preflight(self, remote_address: str, token: str, operation: str) -> tuple[str, int]:
        """Validate the address and token before touching any local or remote state."""

        host, port = self._parse_address(remote_address)
        if len(token) == 0:
            raise NetworkProtocolError(f"{operation} needs a token: pass --token")
        return host, port

    @contextmanager
    def _session(self, host: str, port: int, token: str, operation: str):
        """Connect, authenticate, and yield `(sock, buf)`; always closes the socket.

        Transport failures (refused, timeout, reset) and bad objects met
        mid-session all surface as `NetworkProtocolError`. Error messages
        name the operation and `host:port` only - never the token.
        """

        try:
            sock = socket.create_connection((host, port), timeout=5)
        except OSError as exc:
            raise NetworkProtocolError(f"could not connect to {host}:{port}: {exc}") from exc

        try:
            buf = bytearray()
            send_line(sock, f"AUTH {token}")
            if receive_line(sock, buf) != "OK":
                raise NetworkProtocolError(f"{operation} to {host}:{port}: authentication failed")
            yield sock, buf
        except NetworkProtocolError:
            raise
        except (OSError, ObjectNotFoundError, ObjectCorruptError) as exc:
            raise NetworkProtocolError(f"{operation} to {host}:{port} failed: {exc}") from exc
        finally:
            sock.close()

    def _request_ref(self, sock, buf: bytearray, branch: str) -> str:
        """Ask the remote for `branch`'s tip; returns a commit hash or `-` if it has none."""

        send_line(sock, f"REF {branch}")
        reply = receive_line(sock, buf)
        fields = reply.split(" ")
        if len(fields) != 3 or fields[:2] != ["REF", branch]:
            raise NetworkProtocolError(f"invalid REF response: {reply!r}")
        remote_hash = fields[2]
        if remote_hash != "-" and not _valid_hash(remote_hash):
            raise NetworkProtocolError(f"invalid remote hash: {remote_hash!r}")
        return remote_hash

    def push(self, remote_address: str, branch: str, token: str) -> None:
        """Send local commits on `branch` to the remote, rejecting if it has diverged.

        Resolves the local tip and authenticates, then reads the remote's
        current tip through `REF`. Equal tips mean nothing to do. A remote
        tip that is not an ancestor of the local tip (unknown locally, ahead
        of local, or diverged) means someone else pushed first, and the push
        is rejected before anything is uploaded. Otherwise every object
        reachable from the local tip that the remote doesn't already have is
        uploaded (`HAVE`/`PUT`), and the ref only moves after `DONE` reports
        the transfer validated on the far side - the server rechecks the
        expected old tip there too, so a push that raced ours still loses.
        """

        host, port = self._preflight(remote_address, token, "push")

        local_hash = self.commits.read_ref(branch)
        if local_hash is None:
            raise NetworkProtocolError(f"local branch {branch!r} has no commits to push")

        with self._session(host, port, token, "push") as (sock, buf):
            remote_hash = self._request_ref(sock, buf, branch)

            if remote_hash == local_hash:
                send_line(sock, "DONE")
                print(f"{branch} is up to date")
                return

            if remote_hash != "-":
                self._check_fast_forward(branch, remote_hash, local_hash)

            send_line(sock, f"PUSH {branch} {remote_hash} {local_hash}")
            reply = receive_line(sock, buf)
            if reply != "OK":
                raise NetworkProtocolError(f"push rejected: {_rejection_reason(reply)}")

            # Pinned to `local_hash`, not re-read from the ref: a local commit
            # landing mid-push must not change the snapshot we proposed.
            for obj_hash in self._collect_from(local_hash):
                send_line(sock, f"HAVE {obj_hash}")
                reply = receive_line(sock, buf)
                if reply == "NO":
                    self._put_object(sock, buf, obj_hash)
                elif reply != "YES":
                    raise NetworkProtocolError(f"expected YES or NO, got {reply!r}")

            send_line(sock, "DONE")
            reply = receive_line(sock, buf)
            if reply != "OK":
                raise NetworkProtocolError(f"push rejected: {_rejection_reason(reply)}")

            print(f"{branch} now at {local_hash}")

    def _check_fast_forward(self, branch: str, remote_hash: str, local_hash: str) -> None:
        """Raise unless moving the remote from `remote_hash` to `local_hash` is a fast-forward.

        Ancestry comes from M3's history walk, which follows every parent of
        a merge commit, so a remote tip reachable only through a merge's
        second parent still counts as an ancestor.
        """

        if _is_ancestor(self.commits, remote_hash, local_hash):
            return
        if not _has_object(self.store, remote_hash):
            raise NetworkProtocolError(
                f"push rejected: remote {branch} is at {remote_hash[:7]}, "
                "which you don't have locally - pull first"
            )
        if _is_ancestor(self.commits, local_hash, remote_hash):
            raise NetworkProtocolError(
                f"push rejected: remote {branch} is ahead of local - pull first"
            )
        raise NetworkProtocolError(
            f"push rejected: local and remote {branch} have diverged - pull and merge first"
        )

    def _put_object(self, sock, buf: bytearray, obj_hash: str) -> None:
        """Send `PUT <hash>` followed by the object's `OBJ` header and bytes."""

        obj_type, content = self.store.read_object(obj_hash)
        send_line(sock, f"PUT {obj_hash}")
        send_line(sock, f"OBJ {obj_type} {len(content)}")
        sock.sendall(content)

        reply = receive_line(sock, buf)
        if reply != "OK":
            raise NetworkProtocolError(f"remote rejected {obj_hash}: {reply}")

    def list_refs(self, remote_address: str, token: str) -> dict[str, str]:
        """Return every branch the remote advertises, as `{name: commit hash}`."""

        host, port = self._preflight(remote_address, token, "ls-remote")

        refs: dict[str, str] = {}
        with self._session(host, port, token, "ls-remote") as (sock, buf):
            send_line(sock, "REFS")
            while (line := receive_line(sock, buf)) != "END":
                fields = line.split(" ")
                if (
                    len(fields) != 3
                    or fields[0] != "REF"
                    or not _valid_branch(fields[1])
                    or not _valid_hash(fields[2])
                    or fields[1] in refs
                ):
                    raise NetworkProtocolError(f"invalid REFS entry: {line!r}")
                refs[fields[1]] = fields[2]
            send_line(sock, "DONE")
        return refs

    def fetch(self, remote_address: str, branch: str, token: str) -> str:
        """Download `branch`'s complete history from the remote; return its tip hash.

        The tip is read once and the whole transfer is pinned to it, so the
        remote branch moving mid-fetch can't switch snapshots. Only objects
        missing locally are requested, but the walk still descends through
        objects that are already present - an interrupted fetch can leave a
        commit stored without its tree - so on return every commit, tree,
        and blob reachable from the tip is present, hash-checked, and of the
        type its parent says it is. No ref, HEAD, index, or file is touched;
        objects received before a failure stay stored for a retry to reuse.
        """

        host, port = self._preflight(remote_address, token, "fetch")
        if not _valid_branch(branch):
            raise NetworkProtocolError(f"invalid branch name: {branch!r}")

        with self._session(host, port, token, "fetch") as (sock, buf):
            tip = self._request_ref(sock, buf, branch)
            if tip == "-":
                raise NetworkProtocolError(f"remote has no branch {branch!r}")
            self._fetch_history(sock, buf, tip)
            send_line(sock, "DONE")
        return tip

    def _fetch_history(self, sock, buf: bytearray, tip: str) -> None:
        """Make every object reachable from commit `tip` present locally, following all parents."""

        seen: set[str] = set()
        pending = [tip]
        while pending:
            commit_hash = pending.pop()
            if commit_hash in seen:
                continue
            seen.add(commit_hash)

            self._ensure_object(sock, buf, commit_hash, "commit")
            commit = self.commits.read_commit(commit_hash)
            self._fetch_tree(sock, buf, commit.tree, seen)
            pending.extend(commit.parents)

    def _fetch_tree(self, sock, buf: bytearray, tree_hash: str, seen: set[str]) -> None:
        """Make `tree_hash` and everything nested under it present locally, once each."""

        if tree_hash in seen:
            return
        seen.add(tree_hash)

        self._ensure_object(sock, buf, tree_hash, "tree")
        for entry in self.store.read_tree(tree_hash):
            if entry.type == "tree":
                self._fetch_tree(sock, buf, entry.hash, seen)
            elif entry.hash not in seen:
                seen.add(entry.hash)
                self._ensure_object(sock, buf, entry.hash, "blob")

    def _ensure_object(self, sock, buf: bytearray, obj_hash: str, expected_type: str) -> None:
        """Request `obj_hash` unless it's already stored, and check it is an `expected_type`."""

        if _has_object(self.store, obj_hash):
            obj_type, _ = self.store.read_object(obj_hash)  # re-hashes: catches local corruption
            if obj_type != expected_type:
                raise NetworkProtocolError(
                    f"object {obj_hash} is a {obj_type}, expected a {expected_type}"
                )
            return

        send_line(sock, f"WANT {obj_hash}")
        self._receive_object(sock, buf, obj_hash, expected_type)

    def pull(self, remote_address: str, branch: str, token: str) -> None:
        """Fetch `branch` from the remote and integrate it into the local branch of that name.

        Integration is Module 3's job (`integrate_fetched`, #40): it runs
        only after the fetched history is complete and validated, and a
        `MergeConflictError` from it propagates unchanged so the user sees
        the conflicting paths.
        """

        fetched = self.fetch(remote_address, branch, token)
        before = self.commits.read_ref(branch)
        merge_hash = self._integrate(branch, fetched)
        after = self.commits.read_ref(branch)

        if merge_hash is not None:
            print(f"{branch}: merged {fetched[:7]} as {merge_hash[:7]}")
        elif after == before:
            print(f"{branch} is up to date")
        else:
            print(f"{branch} now at {after}")

    def _integrate(self, branch: str, fetched: str) -> str | None:
        """Hand a fetched, validated tip to M3; returns a new merge commit hash or None."""

        integrate_fetched = getattr(self.commits, "integrate_fetched", None)
        if integrate_fetched is not None:
            return integrate_fetched(branch, fetched)
        return self._integrate_by_ref(branch, fetched)

    def _integrate_by_ref(self, branch: str, fetched: str) -> None:
        """Interim stand-in for M3's `integrate_fetched` until #40 lands.

        Handles only the cases that are pure ref moves through M3's
        `read_ref`/`write_ref`: no-op when local already contains the fetched
        tip, and creating or fast-forwarding a branch that isn't checked
        out. Anything needing a checkout or a merge is refused rather than
        reimplemented here; the fetched objects stay stored either way.
        """

        local = self.commits.read_ref(branch)
        if local == fetched or (local is not None and _is_ancestor(self.commits, fetched, local)):
            return None

        # M3 treats a repo with no HEAD file as being on `main`.
        if branch == (self.commits.read_head() or "main"):
            raise MiniGitError(
                f"can't update checked-out branch {branch!r} yet: pulling into it needs "
                "Module 3's fetched-tip integration (#40). The fetched history is stored "
                "locally; pull again once that lands"
            )
        if local is not None and not _is_ancestor(self.commits, local, fetched):
            raise MiniGitError(
                f"local {branch} has diverged from the remote: "
                f"check out {branch} and pull again to merge"
            )

        self.commits.write_ref(branch, fetched)
        return None

    def fetch_objects(self, remote_address: str, hashes: list[str], token: str) -> None:
        """Fetch each of `hashes` from the remote and write it into the local store.

        Authenticates once, then sends one `WANT` per unique hash over the
        same connection. Each reply's content is re-hashed and checked
        against the hash that was requested before it is stored, so a
        corrupted or mismatched reply never lands in the object store.
        """

        host, port = self._preflight(remote_address, token, "fetch")
        with self._session(host, port, token, "fetch") as (sock, buf):
            for obj_hash in dict.fromkeys(hashes):
                send_line(sock, f"WANT {obj_hash}")
                self._receive_object(sock, buf, obj_hash)
            send_line(sock, "DONE")

    def _receive_object(
        self, sock, buf: bytearray, expected_hash: str, expected_type: str | None = None
    ) -> str:
        """Read one `OBJ`/`ERR` reply for `expected_hash`, store it if it checks out,
        and return its type. `expected_type`, when given, must match too.
        """

        header = receive_line(sock, buf)
        command, _, rest = header.partition(" ")

        if command == "ERR":
            raise NetworkProtocolError(f"remote could not provide {expected_hash}: {rest}")
        if command != "OBJ":
            raise NetworkProtocolError(f"expected OBJ, got {header!r}")

        obj_type, _, length_text = rest.partition(" ")
        if obj_type not in {"blob", "tree", "commit"} or not (
            length_text.isascii() and length_text.isdigit()
        ):
            raise NetworkProtocolError(f"malformed OBJ header: {header!r}")

        content = recv_exact(sock, buf, int(length_text))
        if self.store.hash_object(content, obj_type) != expected_hash:
            raise NetworkProtocolError(f"object {expected_hash} failed hash verification")
        if expected_type is not None and obj_type != expected_type:
            raise NetworkProtocolError(
                f"object {expected_hash} is a {obj_type}, expected a {expected_type}"
            )

        self.store.write_object(content, obj_type)
        return obj_type

    def collect_reachable(self, branch: str) -> set[str]:
        """Return every commit, tree, and blob hash reachable from `branch`'s tip."""

        tip = self.commits.read_ref(branch)
        if tip is None:
            return set()
        return self._collect_from(tip)

    def _collect_from(self, tip: str) -> set[str]:
        """Return every commit, tree, and blob hash reachable from commit `tip`."""

        reachable: set[str] = set()
        for commit_hash in self.commits.walk_history(tip):
            reachable.add(commit_hash)
            commit = self.commits.read_commit(commit_hash)
            self._collect_tree(commit.tree, reachable)

        return reachable

    def _collect_tree(self, tree_hash: str, reachable: set[str]) -> None:
        """Add `tree_hash` and everything nested under it to `reachable`, once each."""

        if tree_hash in reachable:
            return
        reachable.add(tree_hash)

        for entry in self.store.read_tree(tree_hash):
            if entry.type == "tree":
                self._collect_tree(entry.hash, reachable)
            else:
                reachable.add(entry.hash)


class RemoteServer:
    """Serves one repo's refs and objects to `RemoteClient`s over TCP, one client at a time.

    Sessions are handled strictly one after another, which is what makes a
    push's final "recheck ref -> validate history -> write ref" sequence
    atomic with respect to other pushes: no second session can run between
    the check and the write. If `serve_forever` is ever made concurrent, that
    sequence in `_handle_push` must be wrapped in a lock. (Writes made to the
    served repo outside this server, e.g. a local commit, aren't covered.)

    Each accepted connection gets `conn_timeout` seconds per read/write, so a
    stalled client can't hold the server - and every client queued behind
    it - forever.
    """

    def __init__(
        self,
        repo_path=".",
        token="",
        host="127.0.0.1",
        port=0,
        store=None,
        commits=None,
        conn_timeout=10.0,
    ):
        self.repo_path = repo_path
        self.token = token
        self.host = host
        self.conn_timeout = conn_timeout
        self.store = store if store is not None else ObjectStore(repo_path)
        self.commits = (
            commits if commits is not None else CommitManager(repo_path, store=self.store)
        )

        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind((host, port))
        self._sock.listen()
        # accept() polls this instead of blocking forever: closing the socket
        # from another thread isn't guaranteed to unblock a pending accept()
        # on every platform (it does on macOS, not reliably on Linux).
        self._sock.settimeout(0.5)
        self.port = self._sock.getsockname()[1]
        self._closed = False

    def close(self) -> None:
        """Stop accepting connections. `serve_forever()` notices within one poll interval."""

        self._closed = True
        self._sock.close()

    def serve_forever(self) -> None:
        """Accept connections and handle them one at a time until `close()` is called."""

        while not self._closed:
            try:
                conn, _ = self._sock.accept()
            except TimeoutError:
                continue  # no connection yet - check self._closed and try again
            except OSError:
                return  # listening socket was closed - shut down

            try:
                conn.settimeout(self.conn_timeout)
                self._handle_client(conn)
            except (ObjectNotFoundError, ObjectCorruptError) as exc:
                try:
                    send_line(conn, f"ERR object validation failed: {exc}")
                except OSError:
                    pass
            except (NetworkProtocolError, OSError):
                pass  # a bad client must not take down the server
            finally:
                conn.close()

    def _handle_client(self, conn) -> None:
        """Authenticate the connection, then answer REF / REFS / WANT requests until DONE.

        Nothing is answered before a successful AUTH: any other first line
        gets `ERR bad auth` and the connection is closed.
        """

        buf = bytearray()

        line = receive_line(conn, buf)
        command, _, value = line.partition(" ")
        if command != "AUTH" or value != self.token:
            send_line(conn, "ERR bad auth")
            return
        send_line(conn, "OK")

        while True:
            line = receive_line(conn, buf)
            command, _, value = line.partition(" ")

            if command == "DONE":
                return
            elif command == "REF":
                self._send_ref(conn, value)
            elif command == "REFS":
                self._send_refs(conn)
            elif command == "WANT":
                self._send_object(conn, value)
            elif command == "PUSH":
                self._handle_push(conn, buf, value)
                return
            else:
                send_line(conn, "ERR expected REF, REFS, WANT, PUSH, or DONE")
                return

    def _send_refs(self, conn) -> None:
        """Reply with one `REF <name> <hash>` line per branch, then `END`.

        Branches whose name or contents wouldn't survive the wire format
        (unsafe names, empty or malformed ref files) are left out.
        """

        for name in self.commits.list_branches():
            if not _valid_branch(name):
                continue
            try:
                commit_hash = self.commits.read_ref(name)
            except OSError:
                continue  # e.g. a directory under refs/heads/, not a ref file
            if commit_hash and _valid_hash(commit_hash):
                send_line(conn, f"REF {name} {commit_hash}")
        send_line(conn, "END")

    def _send_ref(self, conn, branch: str) -> None:
        """Reply with the commit hash `branch` currently points at, or `-` if it has none."""

        if not _valid_branch(branch):
            send_line(conn, "ERR invalid branch")
            return
        commit_hash = self.commits.read_ref(branch) or "-"
        send_line(conn, f"REF {branch} {commit_hash}")

    def _handle_push(self, conn, buf: bytearray, args: str) -> None:
        """Handle a PUSH sub-session: HAVE/PUT exchange, then validate and move the ref.

        `args` is `"<branch> <old-hash|-> <new-hash>"`. A failure anywhere in
        here - a stale old ref, a bad object, a dropped connection - leaves
        the ref untouched; any objects already stored are simply left in
        place for a retry to reuse.
        """

        try:
            branch, old_hash, new_hash = args.split(" ")
        except ValueError:
            send_line(conn, "ERR malformed PUSH")
            return

        if not _valid_branch(branch):
            send_line(conn, "ERR invalid branch")
            return
        if old_hash != "-" and not _valid_hash(old_hash):
            send_line(conn, "ERR invalid old hash")
            return
        if not _valid_hash(new_hash):
            send_line(conn, "ERR invalid new hash")
            return

        expected_old = None if old_hash == "-" else old_hash
        if self.commits.read_ref(branch) != expected_old:
            send_line(conn, f"ERR remote {branch} has moved since you last synced - pull first")
            return

        send_line(conn, "OK")

        while True:
            line = receive_line(conn, buf)
            command, _, value = line.partition(" ")

            if command == "HAVE":
                if not _valid_hash(value):
                    send_line(conn, "ERR invalid object hash")
                    return
                send_line(conn, "YES" if _has_object(self.store, value) else "NO")
            elif command == "PUT":
                if not self._receive_pushed_object(conn, buf, value):
                    return
            elif command == "DONE":
                break
            else:
                send_line(conn, "ERR expected HAVE, PUT, or DONE")
                return

        # Recheck the ref hasn't moved since we accepted the PUSH. From here
        # to write_ref below nothing else can move it: sessions are serial
        # (see the class docstring), so check + write act as one step.
        if self.commits.read_ref(branch) != expected_old:
            send_line(conn, f"ERR remote {branch} changed during push - pull first")
            return

        try:
            self._validate_history(new_hash)
        except (ObjectNotFoundError, ObjectCorruptError) as exc:
            send_line(conn, f"ERR incomplete history: {exc}")
            return

        if expected_old is not None and not _is_ancestor(self.commits, expected_old, new_hash):
            send_line(conn, "ERR not a fast-forward")
            return

        self.commits.write_ref(branch, new_hash)
        send_line(conn, "OK")

    def _receive_pushed_object(self, conn, buf: bytearray, expected_hash: str) -> bool:
        """Read one `PUT`'s `OBJ` header and bytes, hash-check, and store it.

        Replies `OK`/`ERR` itself (mirroring `_send_object`'s style) and
        returns whether it succeeded, so the caller knows to stop the
        session on failure.
        """

        if not _valid_hash(expected_hash):
            send_line(conn, "ERR invalid object hash")
            return False

        header = receive_line(conn, buf)
        command, _, rest = header.partition(" ")
        if command != "OBJ":
            send_line(conn, "ERR expected OBJ")
            return False

        obj_type, _, length_text = rest.partition(" ")
        if obj_type not in {"blob", "tree", "commit"} or not (
            length_text.isascii() and length_text.isdigit()
        ):
            send_line(conn, "ERR malformed OBJ header")
            return False

        content = recv_exact(conn, buf, int(length_text))
        if self.store.hash_object(content, obj_type) != expected_hash:
            send_line(conn, f"ERR hash mismatch for {expected_hash}")
            return False

        self.store.write_object(content, obj_type)
        send_line(conn, "OK")
        return True

    def _validate_history(self, commit_hash: str) -> None:
        """Verify every commit, tree, and blob reachable from `commit_hash` exists
        with the expected type, before the ref is allowed to move.
        """

        visited_trees: set[str] = set()
        for c_hash in self.commits.walk_history(commit_hash):
            commit = self.commits.read_commit(c_hash)
            self._validate_tree(commit.tree, visited_trees)

    def _validate_tree(self, tree_hash: str, visited: set[str]) -> None:
        """Add `tree_hash` to `visited` and check everything nested under it, once each."""

        if tree_hash in visited:
            return
        visited.add(tree_hash)

        for entry in self.store.read_tree(tree_hash):
            if entry.type == "tree":
                self._validate_tree(entry.hash, visited)
            else:
                obj_type, _ = self.store.read_object(entry.hash)
                if obj_type != "blob":
                    raise ObjectCorruptError(entry.hash)

    def _send_object(self, conn, obj_hash: str) -> None:
        """Reply with the requested object's bytes, or `ERR` if it isn't in the store."""

        if not _valid_hash(obj_hash):
            send_line(conn, "ERR invalid object hash")
            return
        try:
            obj_type, content = self.store.read_object(obj_hash)
        except ObjectNotFoundError:
            send_line(conn, f"ERR unknown object {obj_hash}")
            return

        except ObjectCorruptError:
            send_line(conn, f"ERR corrupt object {obj_hash}")
            return

        send_line(conn, f"OBJ {obj_type} {len(content)}")
        conn.sendall(content)


# Wire protocol: one message per line, UTF-8 encoded, terminated with "\n".
#
#   AUTH <token>                 - client authenticates the connection with its token
#   REF <branch>                 - ask for / report the commit hash a branch currently points to
#   REFS                         - list every branch: zero or more `REF <name> <hash>`, then END
#   END                          - terminates a REFS listing
#   WANT <hash>                  - request the object with this hash (fetch/pull)
#   PUSH <branch> <old|-> <new>  - propose moving <branch> from <old> ("-" = unborn) to <new>
#   HAVE <hash>                  - ask whether the remote already has an object
#   PUT <hash>                   - announce an upload of <hash>, followed by an OBJ header + bytes
#   OBJ <type> <len>             - announces an object is coming next: its type and byte length
#   DONE                         - no more messages from this side (also closes a PUSH session)
#   ERR <reason>                 - something went wrong
#
# A push session: AUTH -> REF (read the remote's current tip) -> PUSH
# (propose the move) -> repeated HAVE/PUT (upload only what the remote is
# missing) -> DONE (remote validates the new history and moves the ref) ->
# connection closes.
#
# A fetch session: AUTH -> REF (pin the tip) -> WANT/OBJ for each commit,
# tree, and blob reachable from it that isn't already local -> DONE. Pull
# is a fetch followed by Module 3 integrating the tip into the local branch.
# Multi-branch transfers repeat the single-branch session per name from
# REFS (or per local branch for push); shared objects travel once because
# later sessions find them already present.
#
# Conflict detection happens twice. The client refuses before PUSH if the
# remote tip from REF isn't an ancestor of its local tip. The server
# refuses PUSH if <old> isn't its current ref, and again at DONE if the ref
# moved, history is incomplete, or <new> doesn't descend from <old>. Any
# refusal is `ERR <reason>` and leaves the ref where it was.


def register_subcommands(subparsers) -> None:
    """Register the networking subcommands with the CLI parser."""

    push_parser = subparsers.add_parser("push", help="push a branch to a remote")
    push_parser.add_argument("address")
    push_parser.add_argument("branch", nargs="?")
    push_parser.add_argument("--all", action="store_true", help="push every local branch")
    push_parser.add_argument("--token", default="")
    push_parser.set_defaults(handler=cmd_push)

    pull_parser = subparsers.add_parser("pull", help="fetch a branch and integrate it locally")
    pull_parser.add_argument("address")
    pull_parser.add_argument("branch", nargs="?")
    pull_parser.add_argument("--all", action="store_true", help="pull every remote branch")
    pull_parser.add_argument("--token", default="")
    pull_parser.set_defaults(handler=cmd_pull)

    fetch_parser = subparsers.add_parser("fetch", help="download a branch's history only")
    fetch_parser.add_argument("address")
    fetch_parser.add_argument("branch")
    fetch_parser.add_argument("--token", default="")
    fetch_parser.set_defaults(handler=cmd_fetch)

    ls_remote_parser = subparsers.add_parser("ls-remote", help="list a remote's branches")
    ls_remote_parser.add_argument("address")
    ls_remote_parser.add_argument("--token", default="")
    ls_remote_parser.set_defaults(handler=cmd_ls_remote)

    serve_parser = subparsers.add_parser("serve", help="serve this repo to push/pull clients")
    serve_parser.add_argument("--port", type=int, required=True)
    serve_parser.add_argument("--token", default="")
    serve_parser.set_defaults(handler=cmd_serve)


def _branches_from_args(args, all_branches) -> list[str]:
    """Resolve `<branch>` vs `--all` into the list of branches to operate on."""

    if args.all == (args.branch is not None):
        raise MiniGitError("pass either a branch name or --all")
    return sorted(all_branches()) if args.all else [args.branch]


def _run_each(operation, branches: list[str]) -> int:
    """Run `operation(branch)` for each branch, reporting failures without stopping.

    A single branch's error propagates as usual. With several, each
    failure is printed against its branch and the exit code is 1 if any
    branch failed.
    """

    if len(branches) == 1:
        operation(branches[0])
        return 0

    failed = False
    for branch in branches:
        try:
            operation(branch)
        except MiniGitError as exc:
            print(f"minigit: {branch}: {exc}", file=sys.stderr)
            failed = True
    return 1 if failed else 0


def cmd_push(args) -> int:
    """Handle `minigit push` from the CLI."""

    client = RemoteClient()
    branches = _branches_from_args(args, client.commits.list_branches)
    return _run_each(lambda branch: client.push(args.address, branch, args.token), branches)


def cmd_pull(args) -> int:
    """Handle `minigit pull` from the CLI."""

    client = RemoteClient()
    branches = _branches_from_args(args, lambda: client.list_refs(args.address, args.token))
    return _run_each(lambda branch: client.pull(args.address, branch, args.token), branches)


def cmd_fetch(args) -> int:
    """Handle `minigit fetch` from the CLI."""

    tip = RemoteClient().fetch(args.address, args.branch, args.token)
    print(f"fetched {args.branch} at {tip}")
    return 0


def cmd_ls_remote(args) -> int:
    """Handle `minigit ls-remote` from the CLI."""

    for name, commit_hash in sorted(RemoteClient().list_refs(args.address, args.token).items()):
        print(f"{commit_hash}\t{name}")
    return 0


def cmd_serve(args) -> int:
    """Handle `minigit serve` from the CLI."""

    server = RemoteServer(port=args.port, token=args.token)
    print(f"listening on {server.host}:{server.port}")
    server.serve_forever()
    return 0
