"""Module 4 - Remotes and networking.

Owns push and pull over a TCP socket: object and ref exchange between two
repositories, authentication, and push conflict detection.

Depends on: modules 1 and 3.
Serves: nobody - this is the top of the stack.

Build the `RemoteClient` class here, per the interface contract.
"""

import os
import socket

from minigit.commits import CommitManager
from minigit.errors import NetworkProtocolError, ObjectNotFoundError
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
    return line.decode()


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


class RemoteClient:
    """Push and pull commits between two minigit repos over a TCP connection."""

    def __init__(self, repo_path=".", store=None, commits=None):
        self.repo_path = repo_path
        self.config_path = os.path.join(self.repo_path, ".minigit", "config")
        self.store = store if store is not None else ObjectStore(self.repo_path)
        self.commits = commits if commits is not None else CommitManager(self.repo_path)

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

    def push(self, remote_address: str, branch: str, token: str) -> None:
        """Send local commits on `branch` to the remote, rejecting if it has diverged."""

        host, port = self._parse_address(remote_address)
        if len(token) == 0:
            raise NetworkProtocolError("push needs a token: pass --token")

        try:
            sock = socket.create_connection((host, port), timeout=5)
        except OSError as exc:
            raise NetworkProtocolError(f"could not connect to {host}:{port}: {exc}") from exc

        try:
            buf = bytearray()
            send_line(sock, f"AUTH {token}")
            reply = receive_line(sock, buf)
            if reply != "OK":
                raise NetworkProtocolError(f"auth failed: {reply}")

            send_line(sock, f"REF {branch}")
            reply = receive_line(sock, buf)
            remote_hash = reply.rsplit(" ", 1)[1]
            print(f"remote {branch} is at {remote_hash}")
            print("# Week 6 - send missing objects, move the ref last")
        finally:
            sock.close()

        # remote hash not an ancestor of local -> someone else pushed first -> NetworkProtocolError
        # walk local commit graph from remote's hash up to local -> collect reachable objects
        # send only the missing objects
        # move the remote ref LAST, only after every object arrived

    def pull(self, remote_address: str, branch: str, token: str) -> None:
        """Fetch `branch` from the remote and update the matching local ref."""

        host, port = self._parse_address(remote_address)
        if len(token) == 0:
            raise NetworkProtocolError("pull needs a token: pass --token")

        try:
            sock = socket.create_connection((host, port), timeout=5)
        except OSError as exc:
            raise NetworkProtocolError(f"could not connect to {host}:{port}: {exc}") from exc

        try:
            buf = bytearray()
            send_line(sock, f"AUTH {token}")
            reply = receive_line(sock, buf)
            if reply != "OK":
                raise NetworkProtocolError(f"auth failed: {reply}")

            send_line(sock, f"REF {branch}")
            reply = receive_line(sock, buf)
            remote_hash = reply.rsplit(" ", 1)[1]
            print(f"remote {branch} is at {remote_hash}")
            print("# Week 6 - same exchange in reverse")
        finally:
            sock.close()

    def fetch_objects(self, remote_address: str, hashes: list[str], token: str) -> None:
        """Fetch each of `hashes` from the remote and write it into the local store.

        Authenticates once, then sends one `WANT` per unique hash over the
        same connection. Each reply's content is re-hashed and checked
        against the hash that was requested before it is stored, so a
        corrupted or mismatched reply never lands in the object store.
        """

        host, port = self._parse_address(remote_address)
        if len(token) == 0:
            raise NetworkProtocolError("fetch needs a token: pass --token")

        try:
            sock = socket.create_connection((host, port), timeout=5)
        except OSError as exc:
            raise NetworkProtocolError(f"could not connect to {host}:{port}: {exc}") from exc

        try:
            buf = bytearray()
            send_line(sock, f"AUTH {token}")
            reply = receive_line(sock, buf)
            if reply != "OK":
                raise NetworkProtocolError(f"auth failed: {reply}")

            for obj_hash in dict.fromkeys(hashes):
                send_line(sock, f"WANT {obj_hash}")
                self._receive_object(sock, buf, obj_hash)

            send_line(sock, "DONE")
        except OSError as exc:
            raise NetworkProtocolError(f"connection to {host}:{port} failed: {exc}") from exc
        finally:
            sock.close()

    def _receive_object(self, sock, buf: bytearray, expected_hash: str) -> None:
        """Read one `OBJ`/`ERR` reply for `expected_hash` and store it if it checks out."""

        header = receive_line(sock, buf)
        command, _, rest = header.partition(" ")

        if command == "ERR":
            raise NetworkProtocolError(f"remote could not provide {expected_hash}: {rest}")
        if command != "OBJ":
            raise NetworkProtocolError(f"expected OBJ, got {header!r}")

        obj_type, _, length_text = rest.partition(" ")
        if not obj_type or not length_text.isdigit():
            raise NetworkProtocolError(f"malformed OBJ header: {header!r}")

        content = recv_exact(sock, buf, int(length_text))
        if self.store.hash_object(content, obj_type) != expected_hash:
            raise NetworkProtocolError(f"object {expected_hash} failed hash verification")

        self.store.write_object(content, obj_type)

    def collect_reachable(self, branch: str) -> set[str]:
        """Return every commit, tree, and blob hash reachable from `branch`'s tip.

        Local-only this week: this is what push will later diff against the
        remote's advertised hash to find what's actually missing there.
        Written against M1's `read_tree` (#16) and M3's `read_commit` /
        `walk_history` (#18), which haven't merged yet - real integration
        follows once they do.
        """

        tip = self.commits.read_ref(branch)
        if tip is None:
            return set()

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
    """Accepts a RemoteClient's AUTH + REF handshake over TCP, one client at a time."""

    def __init__(self, repo_path=".", token="", host="127.0.0.1", port=0, store=None):
        self.repo_path = repo_path
        self.token = token
        self.host = host
        self.store = store if store is not None else ObjectStore(repo_path)

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
                self._handle_client(conn)
            except (NetworkProtocolError, OSError):
                pass  # a bad client must not take down the server
            finally:
                conn.close()

    def _handle_client(self, conn) -> None:
        """Authenticate the connection, then answer REF / WANT requests until DONE."""

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
            elif command == "WANT":
                self._send_object(conn, value)
            else:
                send_line(conn, "ERR expected REF, WANT, or DONE")
                return

    def _send_ref(self, conn, branch: str) -> None:
        """Reply with the commit hash `branch` currently points at, or `-` if it has none."""

        ref_path = os.path.join(self.repo_path, ".minigit", "refs", "heads", branch)
        if os.path.exists(ref_path):
            with open(ref_path) as f:
                commit_hash = f.read().strip()
        else:
            commit_hash = "-"
        send_line(conn, f"REF {branch} {commit_hash}")

    def _send_object(self, conn, obj_hash: str) -> None:
        """Reply with the requested object's bytes, or `ERR` if it isn't in the store."""

        try:
            obj_type, content = self.store.read_object(obj_hash)
        except ObjectNotFoundError:
            send_line(conn, f"ERR unknown object {obj_hash}")
            return

        send_line(conn, f"OBJ {obj_type} {len(content)}")
        conn.sendall(content)


# Wire protocol (draft only - Week 2 makes this real):
# One message per line, UTF-8 encoded, terminated with "\n".
#
#   AUTH <token>       - client authenticates the connection with its token
#   REF <branch>       - ask for / report the commit hash a branch currently points to
#   WANT <hash>        - request the object with this hash
#   OBJ <type> <len>   - announces an object is coming next: its type and byte length
#   DONE               - no more messages from this side
#   ERR                - something went wrong


def register_subcommands(subparsers) -> None:
    """Register the `push`, `pull`, and `serve` subcommands with the CLI parser."""

    push_parser = subparsers.add_parser("push", help="push a branch to a remote")
    push_parser.add_argument("address")
    push_parser.add_argument("branch")
    push_parser.add_argument("--token", default="")
    push_parser.set_defaults(handler=cmd_push)

    pull_parser = subparsers.add_parser("pull", help="pull a branch to a local")
    pull_parser.add_argument("address")
    pull_parser.add_argument("branch")
    pull_parser.add_argument("--token", default="")
    pull_parser.set_defaults(handler=cmd_pull)

    serve_parser = subparsers.add_parser("serve", help="serve this repo to push/pull clients")
    serve_parser.add_argument("--port", type=int, required=True)
    serve_parser.add_argument("--token", default="")
    serve_parser.set_defaults(handler=cmd_serve)


def cmd_push(args) -> int:
    """Handle `minigit push` from the CLI."""

    RemoteClient().push(args.address, args.branch, args.token)
    return 0


def cmd_pull(args) -> int:
    """Handle `minigit pull` from the CLI."""

    RemoteClient().pull(args.address, args.branch, args.token)
    return 0


def cmd_serve(args) -> int:
    """Handle `minigit serve` from the CLI."""

    server = RemoteServer(port=args.port, token=args.token)
    print(f"listening on {server.host}:{server.port}")
    server.serve_forever()
    return 0
