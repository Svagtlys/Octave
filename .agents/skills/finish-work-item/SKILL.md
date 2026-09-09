---
name: finish-work-item
description: >-
  Use in code mode after implementation is complete to verify, finalize commits,
  and prepare the PR for review. Triggers on "finish this work item", "complete #123",
  "prepare this branch for review", or when the user indicates all implementation tasks are done.
modeSlugs:
  - code
---

# Finish Work Item

## Overview

Verify tests pass, lint is clean, documentation matches the change, all plan tasks are accounted for in commits, and the branch is pushed and ready for review.

**This skill MUST run in code mode** (to execute verification and git commands). It is invoked after implementation completes (via subagent-driven-development or executing-plans).

**Announce at start:** "I'm using the finish-work-item skill to prepare this work item for review."

## Prerequisites

- Implementation is complete (plan tasks all done)
- You are on the feature/fix/hotfix branch
- A draft PR exists (created by `start-work-item`)
- You are in **code mode**

## Step 1: Run Tests

```bash
backend/.venv/bin/pytest
```

**If tests fail:** Stop and inform the user. Do not proceed.

```
Tests failing (<N> failures). Must fix before preparing for review:

[Show failures]
```

## Step 2: Run Linting

```bash
backend/.venv/bin/ruff check .
```

**If lint errors:** Fix them or run `backend/.venv/bin/ruff check . --fix` and commit the fix.

## Step 3: Verify Frontend Builds (if frontend changes exist)

```bash
git diff --name-only origin/develop...HEAD | grep -q '^frontend/'
```

If frontend files changed:

```bash
(cd frontend && npm run build)
```

**If build fails:** Stop and inform the user.

## Step 3.5: Verify Documentation Is Up to Date

Plans routinely omit documentation tasks, so do not trust the plan's task list as the
doc checklist — derive the requirement from the diff.

Scan the branch diff for user-visible changes:

```bash
git diff origin/develop...HEAD | grep -nE "^\+.*(env_prefix|OCTAVE_[A-Z_]+|BaseSettings|APIRouter|add_api_route|class .*Adapter|__all__)"
```

**If a trigger matched, confirm the corresponding doc changed in the same branch**
(`git diff --name-only origin/develop...HEAD`):

| Diff contains | Doc that must be updated |
|---|---|
| New/renamed env var or settings field | `docs/DEVELOPMENT.md` → Environment Configuration |
| New component, package, or integration boundary | `docs/ARCHITECTURE.md` |
| Changed dev workflow, commands, or project layout | `docs/DEVELOPMENT.md` |
| Feature status change | `docs/TODO.md` |

**If a doc is stale:** update it and commit before marking the PR ready. A doc gap
blocks review just like a lint error does — the env vars in this repo's inference
layer shipped undocumented once for exactly this reason (the plan only listed
`ARCHITECTURE.md`).

## Step 4: Load the Implementation Plan

Read the plan file to verify all tasks are accounted for:

```bash
# Find the plan file for this branch
ls .agents/specs/
```

Read the plan and extract the task list. Compare against commits to ensure every task has corresponding committed work.

## Step 5: Audit Commits Against the Plan

List current commits on the branch:

```bash
git log --oneline origin/develop..HEAD
```

Cross-reference each plan task with commits:

| Check | Action |
|---|---|
| Task has no commits | Ask user — was this task skipped or forgotten? |
| Uncommitted changes exist | Stage and commit with proper conventional commit message |
| Commits not in plan | Verify they are related (refinements, fixes) — if unrelated, ask user |

### Ensure Proper Commit Messages

Each commit must follow Conventional Commits per [`.agents/rules/coding.md`](.agents/rules/coding.md):

```
<type>(<scope>): <short description>
```

**Type mapping from branch prefix:**

| Branch prefix | Commit type |
|---|---|
| `feature/*` | `feat` |
| `fix/*` | `fix` |
| `docs/*` | `docs` |
| `chore/*` | `chore` |
| `hotfix/*` | `fix` |

**Commit size:** One logical change per commit. If a commit message needs "and" to describe what changed, it should be split.

## Step 6: Stage Any Remaining Uncommitted Work

```bash
git status
```

If uncommitted changes exist, stage and commit them with proper conventional commit messages referencing the relevant plan task.

## Step 7: Push Final Branch State

```bash
git push origin <branch-name>
```

If you rebased locally:

```bash
git push --force-with-lease origin <branch-name>
```

## Step 8: Update Draft PR Description

Ensure the PR description references the plan and spec. Write the body to a temporary file, then use `gh pr edit`:

```bash
cat > /tmp/pr-body.md << 'EOF'
Closes #<ISSUE_NUMBER>

## Description
[Brief description from the issue]

## Implementation
- [Task 1 summary]
- [Task 2 summary]

## References
- Design doc: `.agents/specs/YYYY-MM-DD-<topic>-design.md`
- Implementation plan: `.agents/specs/YYYY-MM-DD-<feature-name>.md`

## Checklist
- [x] Tests passing
- [x] Lint clean
- [x] Frontend builds (if applicable)
EOF

gh pr edit <PR_NUMBER> --body-file /tmp/pr-body.md --repo Svagtlys/Octave
```

Or use `--body` for inline updates:

```bash
gh pr edit <PR_NUMBER> --body '## What

[Description]

## Testing
- Tests passing
- Lint clean' --repo Svagtlys/Octave
```

**Note:** `gh pr edit` may show a deprecation warning for GitHub Projects (classic) — this is harmless and can be ignored. The PR body will still be updated successfully.

## Step 9: Verify PR Metadata (Labels, Milestone, Assignee)

Double-check that the PR has the correct metadata propagated from the linked issue. Fix any gaps:

```bash
# Check current PR metadata
gh pr view <PR_NUMBER> --repo Svagtlys/Octave --json labels,milestone,assignees

# Get issue metadata for comparison
gh issue view <ISSUE_NUMBER> --repo Svagtlys/Octave --json labels,milestone
```

If any metadata is missing, apply it:

```bash
ISSUE_LABELS=$(gh issue view <ISSUE_NUMBER> --repo Svagtlys/Octave --json labels --jq '[.labels[].name] | join(",")')
ISSUE_MILESTONE=$(gh issue view <ISSUE_NUMBER> --repo Svagtlys/Octave --json milestone --jq '.milestone.title // empty')

gh pr edit <PR_NUMBER> --repo Svagtlys/Octave \
  --add-label "$ISSUE_LABELS" \
  --milestone "$ISSUE_MILESTONE" \
  --add-assignee "@me"
```

**Note:** `--add-label` and `--add-assignee` are additive (won't remove existing values). `--milestone` replaces the current milestone.

## Step 10: Mark PR Ready for Review

Use `gh pr ready` to mark the draft PR as ready for review:

```bash
gh pr ready <PR_NUMBER> --repo Svagtlys/Octave
```

GitHub automation will move the linked issue from **In Progress** to **In Review**.

## Step 10.5: Remind the User About Merge Settings

Before marking the PR ready, verify the PR base branch is correct and remind the user of the merge strategy:

### Verify PR Base Branch

| Branch prefix | Correct PR target |
|---|---|
| `feature/*`, `docs/*`, `chore/*` | `develop` |
| `fix/*` | `release/x.y` (or `develop` if no release branch exists yet) |
| `hotfix/*` | `main` |

If the base branch is wrong, fix it:
```bash
gh pr edit <PR_NUMBER> --base <correct-target> --repo Svagtlys/Octave
```

### Remind the User of the Merge Strategy

When the PR is approved and ready to merge, the merge strategy depends on the PR type:

| PR type | Merge strategy | Reason |
|---|---|---|
| Feature / chore / docs → `develop` | **Squash and merge** | Keeps `develop` history clean and linear |
| Fix → `release/x.y` or `develop` | **Squash and merge** | Single atomic fix in history |
| Hotfix → `main` | **Create a merge commit** | Preserves traceability for production changes |
| Release (`develop` → `main`) | **Create a merge commit** | Preserves the merge boundary for traceability (equivalent to `--no-ff`) |

Tell the user explicitly which strategy to use when merging.

## Step 11: Inform the User

```
Work item #<ISSUE_NUMBER> is ready for review.

Branch: <branch-name>
PR: https://github.com/Svagtlys/Octave/pull/<PR_NUMBER>
Status: Ready for review (was draft)

Verification:
- Tests: <N>/<N> passing
- Lint: Clean
- Build: <Passing / Not applicable>

Commits:
- <commit 1: message>
- <commit 2: message>

Plan tasks accounted for: <X>/<X>

Merge settings when approved:
- Base branch: <develop/main/release/x.y>
- Merge strategy: <Squash and merge | Create a merge commit>
```

## Flow Diagram

```
finish-work-item (code mode)  ← YOU ARE HERE
    ├── Step 1: Run pytest (STOP if failing)
    ├── Step 2: Run ruff lint (fix if errors)
    ├── Step 3: Verify frontend build (if applicable)
    ├── Step 3.5: Verify documentation matches the diff
    ├── Step 4: Load implementation plan
    ├── Step 5: Audit commits against plan
    │   └── Ensure conventional commit messages
    ├── Step 6: Stage remaining uncommitted work
    ├── Step 7: Push to origin
    ├── Step 8: Update PR description
    ├── Step 9: Verify PR metadata (labels, milestone, assignee)
    ├── Step 10: Mark PR ready for review
    ├── Step 10.5: Verify PR base branch + remind merge strategy
    └── Step 11: Inform user (including merge settings)
```

## Full Workflow Context

```
start-work-item (code mode)
    └── creates branch + draft PR
    └── directs user to architect mode

plan-work-item (architect mode)
    ├── brainstorming → design doc
    ├── writing-plans → implementation plan
    └── directs user to code mode

[subagent-driven-development / executing-plans] (code mode)
    └── implements task-by-task

finish-work-item (code mode)  ← YOU ARE HERE
    ├── verify tests + lint + build
    ├── audit commits against plan
    ├── clean WIP, ensure conventional commits
    ├── push and mark PR ready
    └── squash happens at merge time (not here)
```

## Common Mistakes

- **Merging without running tests** — Always run `backend/.venv/bin/pytest` first.
- **Squashing commits in this skill** — Squashing is a merge-time decision. This skill ensures commits are clean but does not squash.
- **Wrong PR base branch** — Feature/chore/docs PRs must target `develop`, not `main`. Only hotfix and release PRs target `main`.
- **Wrong merge strategy** — Use squash for features merging into `develop`. Use merge commit for release PRs (`develop` → `main`) and hotfixes into `main`.
- **Wrong commit type** — Map branch prefix to conventional commit type correctly.
- **Skipping the venv prefix** — Always use `backend/.venv/bin/` for Python commands.
- **Creating a new PR** — The draft PR already exists from `start-work-item`. Edit it, don't create a new one.
- **Forgetting frontend build check** — If frontend files changed, verify `npm run build`.
- **Trusting the plan's doc list** — Plans frequently omit documentation tasks. Derive the doc checklist from the diff (Step 3.5), not from the plan.
- **Ignoring plan-task coverage** — Every plan task must have corresponding commits.

## Red Flags

**Never:**
- Merge without running tests
- Squash commits in this skill (squash happens at merge time)
- Force-push without `--force-with-lease`
- Skip conventional commit format
- Target `main` with a feature PR (must go to `develop`)
- Squash-merge a release PR or hotfix into `main` (use merge commit for traceability)

**Always:**
- Verify tests before touching merge
- Use `backend/.venv/bin/` prefix for Python commands
- Audit commits against the implementation plan
- Verify documentation reflects the branch diff before marking ready
- Verify the PR base branch matches the branch prefix rules
- Remind the user of the correct merge strategy (squash vs. merge commit)
- Mark PR ready (don't merge directly without review unless explicitly told)
