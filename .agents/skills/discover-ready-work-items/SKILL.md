---
name: discover-ready-work-items
description: Discover ready-state issues on the active GitHub milestone, summarize each issue, and assign a priority & difficulty rating. Use when you need an overview of what work remains before a release.
---

Identify all *ready* issues from the GitHub Project board, produce a short summary for each, and assign a priority & difficulty rating.

## Prerequisite

### Install `gh-milestone` extension

The `gh milestone` commands used in this skill require the `gh-milestone` extension:

```bash
gh extension install valeriobelli/gh-milestone
```

---

## Scripts

All scripts are located alongside this skill definition in `.agents/skills/discover-ready-work-items/`:

| Script | Purpose |
|---|---|
| [`unblock-ready-items.sh`](unblock-ready-items.sh) | Moves `Blocked` items to `Ready` when their dependencies are complete. |
| [`discover-ready-work-items.sh`](discover-ready-work-items.sh) | Fetches ready items from the project board and prints a markdown report. |

---

## Steps

### 1. Unblock blocked items (optional but recommended)

Before discovering ready items, run the companion unblock script to move any `Blocked` items to `Ready` whose dependencies are now complete:

```bash
bash ".agents/skills/discover-ready-work-items/unblock-ready-items.sh"
```

This script checks all `Blocked` items on the project board, extracts dependency references from the issue body (e.g. `[#123]`), and moves items to `Ready` when all referenced issues are closed.

If the user explicitly requests unblocking, or if no ready items appear in step 2, always run this script first.

### 2. Discover ready items

Run the discover script to fetch ready items and generate a markdown report:

```bash
bash ".agents/skills/discover-ready-work-items/discover-ready-work-items.sh"
```

The script will:
1. Determine the active milestone (first open milestone on the repo).
2. Fetch all items in the `Ready` column from the GitHub Project board (Project ID 7).
3. Fetch full issue metadata for each ready item (labels, assignees, dates, comments).
4. Print a markdown table with each issue's number, title, age, assignee, and a priority/difficulty rating.

**Rating heuristics:**
- **Priority** (`P1`‑`P4`): Based on labels — `critical` → P1, `high` → P2, `medium` → P3, default → P4.
- **Difficulty** (`D1`‑`D3`): Based on comment count (>15 → D3, >5 → D2, else → D1) and a `blocked` label (→ D3).

### 3. Save the report (optional)

Redirect output to a file for planning documentation:

```bash
bash ".agents/skills/discover-ready-work-items/discover-ready-work-items.sh" > issue_report.md
```

---

**Notes**
- The skill assumes the `gh` CLI is authenticated and has access to the Octave repository.
- Adjust label names (`critical`, `high`, `medium`, `blocked`) in the script to match your project's conventions.
- The heuristics for priority/difficulty are simple and can be refined in the script.
