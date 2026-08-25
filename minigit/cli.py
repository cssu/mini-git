"""Command-line entry point.

This is the single `minigit` command the whole team shares. Each module wires
its own subcommands in through `_register_commands` below, so nobody has to
edit the same lines of argument parsing at the same time.

Every handler takes the parsed args and returns an exit code (0 = success).
"""

import argparse
import sys
from collections.abc import Sequence

from minigit import __version__, objects
from minigit.errors import MiniGitError


def _register_commands(subparsers) -> None:
    """Attach each module's subcommands to the parser.

    Module owners: add one line here calling into your own module, and keep
    the argument definitions themselves in that module. For example::

        def register_subcommands(subparsers):
            parser = subparsers.add_parser("add", help="stage a file")
            parser.add_argument("path")
            parser.set_defaults(handler=cmd_add)
    """

    objects.register_subcommands(subparsers)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="minigit", description="A version control system.")
    parser.add_argument("--version", action="version", version=f"minigit {__version__}")

    subparsers = parser.add_subparsers(dest="command", metavar="<command>")
    _register_commands(subparsers)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    handler = getattr(args, "handler", None)
    if handler is None:
        parser.print_help()
        return 1

    try:
        return handler(args)
    except MiniGitError as exc:
        print(f"minigit: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
