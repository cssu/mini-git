"""Exception types shared across every module.

These are part of the interface contract. When one module fails in a way
another module needs to handle, it raises one of these rather than a
module-specific exception, so callers only have to know about this file.
"""


class MiniGitError(Exception):
    """Base class for every error raised by minigit."""


class ObjectNotFoundError(MiniGitError):
    """No object with the requested hash exists in the object store."""


class ObjectCorruptError(MiniGitError):
    """An object was found, but its bytes do not match its hash."""


class RefNotFoundError(MiniGitError):
    """A ref (branch, HEAD target, tag) does not exist."""


class MergeConflictError(MiniGitError):
    """A merge could not complete automatically.

    Carries the paths that conflicted so the caller can report them or write
    conflict markers into the working directory.
    """

    def __init__(self, paths: list[str], message: str | None = None) -> None:
        self.paths = list(paths)
        super().__init__(message or f"merge conflict in: {', '.join(self.paths)}")


class NetworkProtocolError(MiniGitError):
    """A push or pull failed: bad handshake, bad auth, or a rejected update."""
