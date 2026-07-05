---
name: start-work-item
description: Start work on a Github issue — fetch the issue, create the new branch off the correct branch, link to the issue, and prepare to work. Use this skill whenever the user pastes a GitHub issue URL (e.g. `https://github.com/Svagtlys/Octave/issues/123`), references an issue number to start on ("let's work on #123", "start work item 123", "pick up issue 123"), or says they want to start a new branch for a work item. Even a bare issue URL with no other text should trigger this skill.
---

# Start Work Item

## Overview

Fetch a GitHub issue, create the correct branch following Octave's branching conventions, open a draft PR linked to the issue, and prepare for implementation.

**This skill MUST start in code mode** (to execute git CLI commands), then switch to architect mode for planning.

## Pre-conditions

Before starting work on any issue:

1. **Check blockers** — If the issue has blockers that are not yet merged, stop and inform the user. Do not begin work.
2. **Verify `develop` is up-to-date** — Fetch origin before creating branches.

## Step 1: Parse the Issue Reference

Extract the issue number from user input. Accept any of:

- Full URL: `https://github.com/Svagtlys/Octave/issues/123`
- Hash reference: `#123`
- Bare number: `123`

Use regex to extract: `issues\/(\d+)` or `#?(\d+)`

## Step 2: Fetch Issue Details

Use the GitHub CLI to fetch the issue:

```bash
gh issue view <ISSUE_NUMBER> --repo Svagtlys/Octave --json title,body,labels,state
```

Parse the output to determine:

| Field | Used for |
|---|---|
| `title` | Branch name, draft PR title |
| `labels` | Branch prefix (see Step 3) |
| `body` | Context for planning |

## Step 3: Determine Branch Prefix and Source Branch

Map issue labels to branch patterns:

| Label present | Branch prefix | Source branch |
|---|---|---|
| `bug` or `fix` | `fix/<name>` | `develop` (default) or `release/x.y` if the release was already cut — **confirm with user if unclear** |
| `hotfix` | `hotfix/<name>` | `main` |
| `documentation` or `docs` | `docs/<name>` | `develop` |
| `chore` | `chore/<name>` | `develop` |
| `enhancement` or `feature` (or no label) | `feature/<name>` | `develop` |

If the label is ambiguous or missing, default to `feature/<name>` from `develop`.

**Branch name derivation:** Use a kebab-case slug from the issue title (first few meaningful words, max ~50 chars total including prefix).

## Step 4: Create the Branch

```bash
git fetch origin
git checkout -b <branch-name> origin/<source-branch>
```

## Step 4.5: Create an Initial Commit

A PR requires at least one commit on the branch. Create a "starting work" commit:

```bash
git commit --allow-empty -m "chore: start work on #<ISSUE_NUMBER>"
git push -u origin <branch-name>
```

## Step 5: Open a Draft PR

Create a draft PR linked to the issue. The PR body should include:

```markdown
Closes #<ISSUE_NUMBER>

## Description
[Brief description from the issue]

## Checklist
- [ ] Implementation complete
- [ ] Tests passing
- [ ] Documentation updated (if applicable)
```

Use `gh pr create` with labels, milestone, and assignee from the issue:

```bash
# Extract labels and milestone from the issue
ISSUE_LABELS=$(gh issue view <ISSUE_NUMBER> --repo Svagtlys/Octave --json labels --jq '[.labels[].name] | join(",")')
ISSUE_MILESTONE=$(gh issue view <ISSUE_NUMBER> --repo Svagtlys/Octave --json milestone --jq '.milestone.title // empty')

# Build PR create command with metadata
gh pr create \
  --repo Svagtlys/Octave \
  --base <target-branch> \
  --head <branch-name> \
  --title "<type>(main area of change): <issue title>" \
  --body "$(cat pr-body.md)" \
  --label "$ISSUE_LABELS" \
  --milestone "$ISSUE_MILESTONE" \
  --assignee "@me" \
  --draft
```

**Metadata propagation:**
- **Labels:** Copy all labels from the source issue to the PR.
- **Milestone:** Copy the milestone from the source issue (if set).
- **Assignee:** Always assign `@me` (the authenticated user) to the PR.

**Target branch** for the PR:

| Branch prefix | PR target |
|---|---|
| `feature/*`, `docs/*`, `chore/*` | `develop` |
| `fix/*` | `release/x.y` (same as source) |
| `hotfix/*` | `main` |

GitHub automation will move the issue to **In Progress** when the draft PR is created.

## Step 6: Inform the User

Once the branch is created and the draft PR is open, inform the user that the work item setup is complete. Provide a copy-able prompt for the user to pass to the architect mode with the `plan-work-item` skill, which should start with `/plan-work-item` and include:

- The branch name
- The draft PR URL
- An overview of the requested feature or the suspected bug
- A reminder to use the **brainstorming** skill and **writing-plans** skill to design the implementation before coding

**Do NOT switch to architect mode or begin planning.** The brainstorming and planning process should happen in a separate session initiated by the user. This keeps the setup (code mode, git operations) separate from the design work (architect mode).

## Branch Naming Examples

| Issue Title | Branch Name |
|---|---|
| "Add MCP server health monitoring" | `feature/mcp-server-health-monitoring` |
| "Fix context vault stale index" | `fix/context-vault-stale-index` |
| "Update API documentation for tool listing" | `docs/api-docs-tool-listing` |

## Common Mistakes

- **Branching from `main`** — Only `hotfix/*` branches from `main`. Features branch from `develop`.
- **Skipping blocker check** — Always verify blockers are merged before starting.
- **Creating a regular PR** — Always start with a draft PR. Mark ready only after tests pass.
- **Wrong branch prefix** — Match the prefix to the issue type (bug → `fix/`, feature → `feature/`).
