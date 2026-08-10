# Branch Protection

**Status: not yet enabled.** `main` is currently unprotected. GitHub does not
allow branch protection rules or rulesets on private repositories under the free
plan, and the `cssu` organization is on the free plan. See
[Unblocking this](#unblocking-this) below.

Until it is enabled, the pull request workflow in the README is a team
convention rather than something GitHub enforces. Follow it anyway.

## Intended Rules for `main`

Apply these once the repository can support them, at
`Settings -> Branches -> Branch protection rules -> Add rule`, with the branch
name pattern `main`:

- Require a pull request before merging.
- Require 1 approving review.
- Dismiss stale approvals when new commits are pushed.
- Require conversation resolution before merging.
- Require status checks to pass, and select `quality`.
- Require branches to be up to date before merging.
- Require linear history.
- Include administrators.
- Block force pushes.
- Block branch deletion.

The required status check is the `quality` job in
[`.github/workflows/ci.yml`](../.github/workflows/ci.yml), which runs
`scripts/quality-check.sh`. That job has already run, so `quality` will appear
in the status check selector. If the job is ever renamed, the rule has to be
updated to require the new name, or nothing will be enforced.

## Unblocking This

Two options, either one is enough:

1. **Make the repository public.** Branch protection is free on public
   repositories. This is a team and CSSU decision, not a technical one.
2. **Upgrade the organization to GitHub Team.** Paid per seat, and enables
   protection on private repositories.

GitHub Free does include protection for public repos only, so option 1 costs
nothing but publishes the code.

## Recommended Repository Settings

At `Settings -> General -> Pull Requests`:

- Enable squash merging.
- Disable merge commits.
- Disable rebase merging.
- Enable automatically delete head branches.

Squash-only keeps history on `main` to one commit per pull request, which is
what "require linear history" expects. These settings work on the free plan and
are independent of branch protection.

## Notes

These are repository settings, not files, so they are not version controlled and
a change takes effect immediately for everyone. Talk to the team before
loosening anything.
