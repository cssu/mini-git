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

from minigit.errors import NetworkProtocolError


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
        print(f"push: would push {branch} to {host}:{port}")

        # connect over TCP
        # ask remote for its current hash for <branch>
        # remote hash not an ancestor of local -> someone else pushed first -> NetworkProtocolError
        # walk local commit graph from remote's hash up to local -> collect reachable objects
        # send only the missing objects
        # move the remote ref LAST, only after every object arrived

    def pull(self, remote_address: str, branch: str, token: str) -> None:
        """Fetch `branch` from the remote and update the matching local ref."""

        host, port = self._parse_address(remote_address)
        if len(token) == 0:
            raise NetworkProtocolError("pull needs a token: pass --token")
        print(f"pull: would pull {branch} from {host}:{port}")
        # Week 6 - same exchange in reverse


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
