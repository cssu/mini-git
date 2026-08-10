# 2026 Tech Project Template

Template repository for student tech projects.

The CI workflow and local hook scripts are examples for a Node.js project. Update them to match the language, package manager, linter, and test runner used by the project created from this template.

This template includes pull requests, branch protection, CI checks, and optional Git hooks to give student developers practice with workflows used on real engineering teams. These checks also help keep the repository cleaner by catching formatting, linting, and test issues before code is merged.

## Repository Setup

After creating a repository from this template:

1. Configure branch protection for `main` using [docs/branch-protection.md](docs/branch-protection.md).

2. Optional: install local Git hooks:

   ```bash
   scripts/install-git-hooks.sh
   ```

3. Replace or adjust the example quality checks as needed. The included Node.js checks run these commands when they are defined in `package.json`:

   - `npm run lint`
   - `npm run test`

## Pull Request Flow

- Work on a feature branch.
- Open a pull request into `main`.
- Complete the pull request checklist.
- Wait for the required quality check to pass.
- Get review approval before merging.

## Local Quality Checks

Run the same checks locally:

```bash
scripts/quality-check.sh
```

The pre-commit hook runs this script before each commit.

## Scripts

These files are small command-line programs. They are written as `.sh` shell scripts so they can be run locally from the terminal and also reused by GitHub Actions.

- `scripts/install-dependencies.sh`

  Installs the packages needed by a Node.js project. In most Node projects, dependencies are listed in `package.json` and installed with a package manager like `npm`, `pnpm`, or `yarn`. This script checks which lockfile the project has and uses the matching package manager.

  This is mainly used by CI before running tests, because a fresh GitHub Actions runner does not already have the project's dependencies installed.

- `scripts/quality-check.sh`

  Runs the project's automated checks. In this template, it looks for `lint` and `test` scripts in `package.json`.

  A lint command checks code style and catches common mistakes. A test command runs the project's automated tests. If either command fails, the script fails too, which helps stop broken code from being merged.

- `scripts/install-git-hooks.sh`

  Tells Git to use the hook files in the `githooks/` folder. You usually run this once after cloning or creating the repository.

  Git hooks are scripts that Git can run automatically at certain moments, such as right before making a commit.

- `githooks/pre-commit`

  Runs before Git creates a commit, but only after the hooks have been installed with `scripts/install-git-hooks.sh`.

  This hook runs `scripts/quality-check.sh`, so developers get quick feedback before committing code that does not pass the project's checks.
