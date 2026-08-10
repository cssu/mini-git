# Branch Protection

GitHub template repositories copy files, but they do not copy repository settings. Every repository created from this template must configure branch protection manually.

Use this checklist after creating a new repository from the template.

## Configure `main`

Go to:

`Settings -> Branches -> Branch protection rules -> Add branch protection rule`

Set the branch name pattern to:

```text
main
```

Enable these settings:

- Require a pull request before merging.
- Require at least 1 approving review.
- Dismiss stale pull request approvals when new commits are pushed.
- Require conversation resolution before merging.
- Require status checks to pass before merging.
- Require branches to be up to date before merging.
- Require linear history.
- Include administrators.
- Block force pushes.
- Block branch deletion.

## Required Status Check

Before the status check appears in the branch protection selector, the GitHub Actions workflow must run at least once.

The included `CI` workflow is an example. If a project changes the workflow or job names, require that project's equivalent lint/test status check instead.

To make it appear:

1. Push the repository to GitHub.
2. Open a pull request, or push a commit to `main`.
3. Wait for the `CI` workflow to run.
4. Return to the branch protection rule.
5. Select the required status check.

The check may appear as either:

```text
quality
```

or:

```text
CI / quality
```

Choose whichever one GitHub shows for this repository.

## Recommended Repository Settings

Go to:

`Settings -> General -> Pull Requests`

Recommended settings:

- Enable squash merging.
- Disable merge commits.
- Disable rebase merging unless the project specifically wants it.
- Enable automatically delete head branches.

## Notes

These settings cannot be enforced by files in this template alone. The files in `.github/` provide pull request templates and CI checks, but GitHub branch protection must be configured in each created repository.
