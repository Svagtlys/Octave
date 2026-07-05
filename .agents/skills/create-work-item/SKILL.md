---
name: create-work-item
description: Use when creating a GitHub issue for the Octave project — new features, bugs, chores, or documentation. Triggers when the model discovers a problem during coding that should be tracked separately, or the user asks to file an issue, create a ticket, or log a bug.
---

# Create Work Item

## Overview

Create a GitHub issue following Octave's naming convention, labeling, milestone assignment, and minimal-body rules. Issues must be actionable for future implementation or debugging.

**Core principle:** An issue is a work specification, not a conversation. Include only what's needed to recreate the work.

## When to Use

- User reports a bug and asks to "file an issue" or "create a ticket"
- User proposes a feature and wants it tracked
- You discover a problem during coding that should be tracked separately
- User says "log this", "create an issue", "open a ticket"

**Do NOT use when:** The work is trivial enough to commit directly (typos, one-line fixes).

## Pre-creation Checks

### 1. Check for Similar Open Issues

Before creating a new issue, search for existing open GitHub issues that cover the same problem:

```bash
gh issue list --repo Svagtlys/Octave --state open --search "<keyword1>|<keyword2>"
```

Search using keywords from the error message, affected module, or function names. If a matching open issue exists, reference it instead of creating a duplicate.

### 2. Verify Branch is Up to Date with Develop

Check that the current branch is based on (or rebased onto) the latest `develop`:

```bash
git fetch origin develop
git merge-base HEAD origin/develop
git rev-parse origin/develop
```

If the merge-base does not match `origin/develop`, the branch is behind. Ask the user to rebase:

```bash
git rebase origin/develop
```

After rebasing, verify the bug still exists (or the feature is still needed) — it may have been fixed or addressed in a merged PR.

## Issue Title Convention

**Format:** `<type>(<area>): <short description>`

| Type | When | Example |
|------|------|---------|
| `bug` | Something broken | `bug(mcp-gateway): connection pool leaks on server crash` |
| `feat` | New feature | `feat(ui): MCP server management dashboard` |
| `fix` | UI/code fix (not a bug report) | `fix(ui): show 'connecting' state when MCP starts` |
| `ci` | CI/CD pipeline | `ci: investigate arm64 frontend build failure` |
| `chore` | Maintenance, deps | `chore: update pytest to 8.3` |
| `docs` | Documentation | `docs: add API endpoint for MCP tool listing` |

**Area** — the module or component affected: `ui`, `mcp-gateway`, `context-assembler`, `context-vault`, `reasoning`, `auth`, `scheduler`, `db`, etc. Omit area for cross-cutting concerns (`ci:`, `chore:`).

**Description** — imperative, under 60 characters. No period at end.

## Labels

Apply exactly ONE primary label AND exactly ONE component label per issue.

### Primary Labels

| Label | Color | Issue type |
|-------|-------|-----------|
| `bug` | #d73a4a | Something isn't working |
| `enhancement` | #a2eeef | New feature or request |
| `documentation` | #0075ca | Docs only |

### Component Labels

| Label | Color | Covers |
|-------|-------|--------|
| `area:backend` | #7057ff | Cross-cutting backend (foundation, CI, testing) |
| `area:inference` | #e99695 | Inference Engine Connector |
| `area:mcp` | #bf40bf | MCP Connector |
| `area:context` | #0075ca | Context Manager |
| `area:agent` | #c5def0 | Agent Manager |
| `area:ui` | #fbca04 | Frontend views |
| `area:db` | #2da44e | Database and migrations |

### Secondary Labels (Optional)

| Label | Color | Usage |
|-------|-------|-------|
| `good first issue` | #7057ff | Suitable for new contributors |
| `help wanted` | #008672 | Extra attention is needed |

### Other Available Labels

| Label | Color | Usage |
|-------|-------|-------|
| `duplicate` | #cfd3d7 | This issue or PR already exists |
| `invalid` | #e4e669 | This doesn't seem right |
| `question` | #d876e3 | Further information is requested |
| `wontfix` | #ffffff | This will not be worked on |

## Milestone

### Prerequisite: Install `gh-milestone` extension

The `gh milestone` commands used in this skill require the `gh-milestone` extension:

```bash
gh extension install valeriobelli/gh-milestone
```

### Assign Milestone

Assign to the most appropriate open milestone. Check available milestones first:

```bash
gh milestone list --repo Svagtlys/Octave
```

If no suitable milestone exists, create one before creating the issue:

```bash
gh milestone create --repo Svagtlys/Octave --title "<milestone-title>"
```

For example: `gh milestone create --repo Svagtlys/Octave --title "1.0 — Initial Release"`

## Issue Body

### Bug Reports

```markdown
## Description

[What happens vs what should happen — 2-3 sentences max]

## Root Cause

[If known: which function, what condition triggers it. If unknown: "Investigate needed"]

## Reproduction

1. [Step 1]
2. [Step 2]
3. [Observed result]

## Fix

[If known: suggested approach. If unknown: omit this section]
```

### Feature Requests

```markdown
## Description

[What the feature does — 2-3 sentences]

## Why

[User problem this solves — not "it would be nice"]

## Scope

- [ ] [Backend task if applicable]
- [ ] [Frontend task if applicable]
- [ ] [Tests]
```

## Creation Command

```bash
gh issue create \
  --repo Svagtlys/Octave \
  --title "<type>(<area>): <description>" \
  --label "<label>" \
  --milestone "<milestone-title>" \
  --body "$(cat issue-body.md)"
```

## Add to Project

After creation, add the issue to the Octave Milestone Tracker (project `7`):

```bash
gh project item-add 7 --owner Svagtlys --url "<issue-url>"
```

The issue URL is output by `gh issue create` (e.g., `https://github.com/Svagtlys/Octave/issues/42`).

## Set Issue Status

After adding the issue to the project, check for blocking issues and set the status accordingly.

### 1. Check for Blocking Issues

Search open issues that might block the new issue:

```bash
gh issue list --repo Svagtlys/Octave --state open --search "<related-keyword>"
```

Look for issues covering the same area, dependencies, or prerequisites.

### 2. Set Status Based on Blockers

Get the project item ID for the new issue:

```bash
ITEM_ID=$(gh project item-list 7 --owner Svagtlys --format json \
  --jq '.items[] | select(.content.url | contains("issues/<ISSUE_NUMBER>")) | .id')
```

Set status to **Ready** (`1905902e`) if no blockers, or **Blocked** (`de29ef41`) if blockers exist:

```bash
gh project item-edit 7 \
  --project-id $(gh project view 7 --owner Svagtlys --format json --jq '.id') \
  --id "$ITEM_ID" \
  --field-id PVTSSF_lAHOArV0zM4Bb38AzhWlGD8 \
  --single-select-option-id 1905902e
```

Status option IDs:
| Status | Option ID |
|--------|-----------|
| Blocked | `de29ef41` |
| Ready | `1905902e` |
| In Progress | `47fc9ee4` |
| In Review | `3d7c30db` |
| Done | `98236657` |

### 3. Check for Issues Blocked by the New Issue

If existing open issues depend on the new issue, inform the user and ask whether to update their status to Blocked or adjust the new issue as a sub-issue.

## Full Workflow

```bash
ISSUE_URL=$(gh issue create \
  --repo Svagtlys/Octave \
  --title "bug(mcp-gateway): connection pool leaks on server crash" \
  --label "bug" \
  --milestone "1.1 — MCP Gateway" \
  --body "$(cat issue-body.md)")

gh project item-add 7 --owner Svagtlys --url "$ISSUE_URL"

# Set status to Ready (no blockers)
ITEM_ID=$(gh project item-list 7 --owner Svagtlys --format json \
  --jq '.items[] | select(.content.url | contains("'"$ISSUE_URL"'")) | .id')
gh project item-edit 7 \
  --project-id $(gh project view 7 --owner Svagtlys --format json --jq '.id') \
  --id "$ITEM_ID" \
  --field-id PVTSSF_lAHOArV0zM4Bb38AzhWlGD8 \
  --single-select-option-id 1905902e
```

## Common Mistakes

- **Narrative titles** — "I noticed that when I click the button nothing happens" → `bug(ui): button click does nothing`
- **No area** — `bug: something broken` → `bug(mcp-gateway): connection pool leaks on server crash`
- **Over-detailed body** — Include only recreation info, not implementation plans or conversation history
- **No milestone** — Every issue must have a milestone
- **Wrong label** — `bug` = broken behavior, `enhancement` = new capability

## Red Flags

- Title exceeds 70 characters
- Body includes "I think we should..." or "maybe we could..."
- No reproduction steps for bugs
- No milestone assigned
