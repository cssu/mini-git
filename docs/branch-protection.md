# Branch Protection

`main` is protected. You cannot push to it directly, and you cannot merge
without a passing CI run and an approving review. This document records what is
configured and how to change it.

## Rules on `main`

- Require a pull request before merging.
- Require 1 approving review.
- Dismiss stale approvals when new commits are pushed.
- Require conversation resolution before merging.
- Require the `quality` status check to pass.
- Require branches to be up to date before merging.
- Require linear history.
- Include administrators.
- Block force pushes.
- Block branch deletion.

The required status check is the `quality` job in
[`.github/workflows/ci.yml`](../.github/workflows/ci.yml), which runs
`scripts/quality-check.sh`. If that job is ever renamed, the protection rule
has to be updated to require the new name, or nothing will be enforced.

## Repository Settings

`Settings -> General -> Pull Requests`:

- Squash merging enabled.
- Merge commits disabled.
- Rebase merging disabled.
- Automatically delete head branches enabled.

Squash-only keeps history on `main` to one commit per pull request, which is
what "require linear history" expects.

## Changing These

`Settings -> Branches -> Branch protection rules`, then edit the `main` rule.
These are repository settings, not files, so they are not version controlled and
a change takes effect immediately for everyone. Talk to the team before
loosening anything.

If you add a new required status check, the workflow has to have run at least
once before GitHub will offer its name in the selector.
