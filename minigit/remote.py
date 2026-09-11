"""Module 4 - Remotes and networking.

Owns push and pull over a TCP socket: object and ref exchange between two
repositories, authentication, and push conflict detection.

Depends on: modules 1 and 3.
Serves: nobody - this is the top of the stack.

Build the `RemoteClient` class here, per the interface contract.
"""

# from .objects import ObjectStore  (Module 1 & 3)
# from .commits import CommitManager
import os
import socket

from minigit.errors import NetworkProtocolError


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


class RemoteClient:
    """Push and pull commits between two minigit repos over a TCP connection."""

    def __init__(self, repo_path=".", store=None, commits=None):

        self.repo_path = repo_path
        self.config_path = os.path.join(self.repo_path, ".minigit", "config")
        self.store = store
        self.commits = commits
        # Below is correct but need name of function within module 1 & 3
        # self.store = store if store is not None else ObjectStore(self.repo_path)
        # self.commits = commits if commits is not None else CommitManager(self.repo_path)

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


class RemoteServer:
    """Accepts a RemoteClient's AUTH + REF handshake over TCP, one client at a time."""

    def __init__(self, repo_path=".", token="", host="127.0.0.1", port=0):
        self.repo_path = repo_path
        self.token = token
        self.host = host

        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind((host, port))
        self._sock.listen()
        self.port = self._sock.getsockname()[1]

    def close(self) -> None:
        """Stop accepting connections. Unblocks a `serve_forever()` running on another thread."""

        self._sock.close()

    def serve_forever(self) -> None:
        """Accept connections and handle them one at a time until `close()` is called."""

        while True:
            try:
                conn, _ = self._sock.accept()
            except OSError:
                return  # listening socket was closed - shut down

            try:
                self._handle_client(conn)
            except (NetworkProtocolError, OSError):
                pass  # a bad client must not take down the server
            finally:
                conn.close()

    def _handle_client(self, conn) -> None:
        """Run one client's AUTH + REF handshake."""

        buf = bytearray()

        line = receive_line(conn, buf)
        command, _, value = line.partition(" ")
        if command != "AUTH" or value != self.token:
            send_line(conn, "ERR bad auth")
            return
        send_line(conn, "OK")

        line = receive_line(conn, buf)
        command, _, branch = line.partition(" ")
        if command != "REF":
            send_line(conn, "ERR expected REF")
            return

        ref_path = os.path.join(self.repo_path, ".minigit", "refs", "heads", branch)
        if os.path.exists(ref_path):
            with open(ref_path) as f:
                commit_hash = f.read().strip()
        else:
            commit_hash = "-"
        send_line(conn, f"REF {branch} {commit_hash}")


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
    """Register the `push` and `pull` subcommands with the CLI parser."""

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


def cmd_push(args) -> int:
    """Handle `minigit push` from the CLI."""

    RemoteClient().push(args.address, args.branch, args.token)
    return 0


def cmd_pull(args) -> int:
    """Handle `minigit pull` from the CLI."""

    RemoteClient().pull(args.address, args.branch, args.token)
    return 0
