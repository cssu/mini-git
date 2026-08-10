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
- Include administrators, so nobody can bypass the above.
- Block force pushes.
- Block branch deletion.

The required status check is the `quality` job in
[`.github/workflows/ci.yml`](../.github/workflows/ci.yml), which runs
`scripts/quality-check.sh`. If that job is ever renamed, the protection rule has
to be updated to require the new name, or nothing will be enforced.

## Merge Settings

At `Settings -> General -> Pull Requests`:

- Squash merging enabled.
- Merge commits disabled.
- Rebase merging disabled.
- Automatically delete head branches enabled.

Squash-only keeps history on `main` to one commit per pull request, which is
what "require linear history" expects.

## Why the Repository Is Public

GitHub Free does not allow branch protection or rulesets on private
repositories, and CSSU is on the free plan. Making the repository public was the
no-cost way to get these protections. The alternative is upgrading the
organization to GitHub Team.

Because the repository is public, nothing secret belongs in it. No tokens, no
credentials, no personal data, not even in a commit that gets reverted later.
Git history is public too.

## Changing These

`Settings -> Branches -> Branch protection rules`, then edit the `main` rule.
These are repository settings, not files, so they are not version controlled and
a change takes effect immediately for everyone. Talk to the team before
loosening anything.

If you add a new required status check, the workflow has to have run at least
once before GitHub will offer its name in the selector.
