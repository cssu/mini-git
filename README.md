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

## Current workflow

After setup, try this in a separate scratch directory with `minigit` on your PATH:

```bash
minigit init
printf 'A\n' > file.txt
minigit add file.txt
minigit commit -m "A"
minigit log               # full ID, author, timezone-explicit date, message
minigit log --oneline     # compact ID and first message line
minigit branch feature
minigit checkout feature
printf 'B\n' > file.txt
minigit add file.txt
minigit commit -m "B"
minigit checkout main     # file.txt contains A
minigit merge feature     # reports a fast-forward; file.txt contains B
minigit status            # clean
```

Checkout restores files, executable modes, and the index. It rejects staged or
unstaged changes and untracked paths that would be overwritten. Fast-forward
merges keep HEAD on the current branch and create no additional commit.
Diverged branches use Myers line edits for a three-way merge. Disjoint text edits
produce a commit with both parents. Conflicts write markers and leave branch refs
unchanged. Edit each conflicted file, run `minigit add <path>`, then
`minigit commit -m "Resolve merge"`. To resolve by deleting a file, remove it and
add its path. Pending conflicts survive new CLI processes and block another
merge, checkout, or commit until explicitly resolved.

`minigit log` follows both parents of merge commits. It prints the requested
tip first, then visits parents in their stored order, and prints shared
ancestors once. This ordering is deterministic and never uses commit
timestamps to decide ancestry. Detailed output includes full commit and parent
IDs; use `--oneline` for compact summaries. `log` and branch listing are
read-only, including while a merge is pending.

Branch, checkout, and merge commands distinguish created/already-current,
up-to-date, fast-forward, clean-merge, and conflict outcomes. Expected failures
use a concise `minigit: ...` message on stderr and a nonzero exit code; argument
usage errors keep argparse's usage output and exit code 2. After a conflict,
resolve each path, run `minigit add <path>`, then run
`minigit commit -m "Resolve merge"`.

`minigit diff` shows line changes between HEAD and working files, including
untracked files, binary changes, executable modes, and missing final newlines.
`minigit status` retains its staged / not staged / untracked sections. Both
commands are read-only. Object storage also supports local
[packfiles](docs/packfiles.md) without changing object hashes.

To test push, initialize another scratch repository and run
`minigit serve --port 9418 --token demo-token` there. From the first repository,
run `minigit push 127.0.0.1:9418 main --token demo-token`. Push transfers missing
objects and advances the remote branch only after validating its history; it
does not check out files or update the server's HEAD or index. Repeating the push
reports that the branch is up to date. Use a disposable token for this local demo.
From another initialized repository, run
`minigit pull 127.0.0.1:9418 main --token demo-token` to fetch the complete history
and restore the branch. See the network workflow below for multiple branches
and conflict resolution.

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

`main` is protected, so this is enforced rather than suggested.

This repository is public, so never commit anything secret: no tokens, no
credentials, no personal data. Git history is public too, so removing a secret
in a later commit does not unpublish it.

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

## Sharing Repos Over the Network

One repo serves, others push and pull. Every command needs the server's
token, which is never printed back. Use a disposable token for local examples;
stop the server with Ctrl-C when finished.

```bash
# in the repo that acts as the remote
minigit serve --port 9418 --token demo-token

# in any other repo
minigit ls-remote 127.0.0.1:9418 --token demo-token       # list the remote's branches
minigit push 127.0.0.1:9418 main --token demo-token        # or --all for every local branch
minigit fetch 127.0.0.1:9418 main --token demo-token       # download history only, no ref changes
minigit pull 127.0.0.1:9418 main --token demo-token        # fetch, then integrate; or --all
```

- A push is rejected if the remote branch moved since you last synced
  (`push rejected: ... - pull first`). Pull, merge, then push again.
- Pull fetches and validates the whole history before changing anything.
  If the transfer fails, your refs, HEAD, index, and files are untouched,
  and running the same pull again reuses what already arrived.
- Pulling a branch you don't have checked out creates or fast-forwards it
  without touching your files. A diverged one is refused: check it out and
  pull again to merge.
- Pulling into the checked-out branch fast-forwards or merges it. If both
  sides changed the same lines, pull stops with the conflicting paths; fix
  the conflict markers, `minigit add` the files, and `minigit commit`.

## Docs

- [Onboarding guide](https://docs.google.com/document/d/1D77Ncmtunxj7GVADytHo1An6BKzaV-Ep7AxAYfL405k/edit?tab=t.0) -
  start here if you are new: setup, branching, workflow, pull requests, code
  review, and coding standards.
- [Project description](https://docs.google.com/document/d/16OqU5R1x6is5E-2ZWk4KoWDouUU8gCKNzJvW7LTXJtI/edit?tab=t.0#heading=h.m3v17fze97bb) -
  the full explanation of what we are building and why.
- [docs/branch-protection.md](docs/branch-protection.md) - the rules on `main`
  and the merge settings, and how to change them.
