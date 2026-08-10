# mini-git

A version control system built from scratch in Python: a content-addressable
object store, a staging area, commit history as a DAG, branching, merging, and
push/pull over a TCP socket.

The core logic is written by hand. No `libgit2`, no shelling out to system Git,
no existing VCS libraries for storage, diffing, or merging. Hashing,
compression, sockets, argument parsing, and testing libraries are fine.

## Getting Started

```bash
git clone https://github.com/cssu/mini-git.git
cd mini-git
scripts/init.sh
```

That creates a virtual environment, installs everything, and installs the Git
hooks. You need Python 3.11 or newer.

Then:

```bash
minigit --help          # after `source .venv/bin/activate`
scripts/quality-check.sh
```

## Dev Commands

Everything lives in `scripts/`. They all work from any directory in the repo
and find the virtual environment themselves, so activating it is optional.

| Command | What it does |
| --- | --- |
| `scripts/init.sh` | One-time setup: virtual environment, dependencies, Git hooks. |
| `scripts/install-dependencies.sh` | Reinstalls dependencies. Run it after someone changes `pyproject.toml`. |
| `scripts/test.sh` | Runs the tests. Arguments pass through to pytest: `scripts/test.sh -k objects`. |
| `scripts/quality-check.sh` | Lint, formatting, and tests. This is exactly what CI runs. |
| `scripts/format.sh` | Fixes formatting and auto-fixable lint problems. |
| `scripts/build.sh` | Builds an installable package into `dist/`. |
| `scripts/install-git-hooks.sh` | Points Git at the hooks in `githooks/`. `init.sh` already does this. |

## Layout

```
minigit/
  cli.py        the shared `minigit` command; each module registers subcommands here
  errors.py     the exception types every module shares
  objects.py    Module 1 - object storage
  index.py      Module 2 - index and working tree
  commits.py    Module 3 - commits and branching
  remote.py     Module 4 - remotes and networking
tests/
scripts/        dev commands
githooks/       the pre-commit hook
docs/
```

Each module file has a docstring saying what it owns, what it depends on, and
what depends on it. Module boundaries follow the interface contract; changing a
signature another module calls is a conversation with the team, not a solo
decision.

## How We Work

- Branch off `main`. Never commit to `main` directly.
- Open a pull request and fill in the checklist.
- CI has to pass and one teammate has to approve before it merges.
- Squash merge, then delete the branch.

`main` is not protected yet, so for now this is a convention rather than
something GitHub enforces. See
[docs/branch-protection.md](docs/branch-protection.md) for why and how to turn
it on.

## Checks

Three things run your code, and they run the same checks:

- **The pre-commit hook** runs lint and formatting before each commit. It skips
  tests to keep committing fast. If you need to bypass it for a
  work-in-progress commit, `git commit --no-verify` works, but CI will still
  catch what you skipped.
- **`scripts/quality-check.sh`** runs lint, formatting, and tests. Run it before
  opening a pull request.
- **CI** runs `scripts/quality-check.sh` on every pull request and on every push
  to `main`.

A lint check catches unused imports, undefined names, and similar mistakes. A
formatting check keeps everyone's code looking the same so diffs show real
changes instead of whitespace. Tests are the ones you write in `tests/`.

## Docs

- [docs/branch-protection.md](docs/branch-protection.md) - the intended rules on
  `main`, and what is blocking them.
