---
name: discover-ready-work-items
description: Discover ready-state issues on the active GitHub milestone, summarize each issue, and assign a priority & difficulty rating. Use when you need an overview of what work remains before a release.
---

Identify all *ready* issues on the repository's current active milestone, produce a short summary for each, and assign a priority & difficulty rating.

## Prerequisite

### Install `gh-milestone` extension

The `gh milestone` commands used in this skill require the `gh-milestone` extension:

```bash
gh extension install valeriobelli/gh-milestone
```

---

## Steps

### 1. Determine the active milestone

```bash
gh milestone list --repo Svagtlys/Octave --state open --json title --jq '.[0].title'
```

- The command lists open milestones; the first one (chronologically newest) is considered the active milestone.
- Store the title in a variable, e.g. `ACTIVE_MILESTONE`.
- If no open milestone is found, **stop and tell the user**.

### 2. List ready-state issues for that milestone

```bash
gh issue list \
  --repo Svagtlys/Octave \
  --milestone "$ACTIVE_MILESTONE" \
  --state open \
  --json number,title,labels,assignees,createdAt,updatedAt,comments
```

- The `ready` label is used to denote issues that are ready to be worked on. Adjust the label name if your project uses a different convention.
- Save the JSON output to a variable (`ISSUES_JSON`) for processing.

### 3. Summarize and rate each issue (script)

Create a script (e.g. `discover_ready_issues.sh`) with the following content and make it executable (`chmod +x`). The script prints a markdown report to stdout.

```bash
#!/usr/bin/env bash
set -euo pipefail

# 1️⃣ Determine active milestone
ACTIVE_MILESTONE=$(gh milestone list --repo Svagtlys/Octave --state open --json title --jq '.[0].title')
if [[ -z "$ACTIVE_MILESTONE" ]]; then
  echo "No open milestone found" >&2
  exit 1
fi

# Header for the markdown report
echo "# Issue Report – $ACTIVE_MILESTONE (generated $(date))"
echo "| # | Title | Age | Assignee | Rating |"
echo "|---|---|---|---|---|"

# 2️⃣ Fetch issues JSON
ISSUES_JSON=$(gh issue list \
  --repo Svagtlys/Octave \
  --milestone "$ACTIVE_MILESTONE" \
  --state open \
  --json number,title,labels,assignees,createdAt,updatedAt,comments)

# 3️⃣ Process with jq
echo "$ISSUES_JSON" | jq -r '
  # Keep only issues that have the "ready" label
  [ .[] | select(.labels != null and (.labels | map(.name) | index("ready")) != null) ] |
  map(
    # Age in days
    (now - (.createdAt | fromdate)) / 86400 | floor as $age |
    # First assignee or "unassigned"
    (if .assignees and (.assignees|length)>0 then .assignees[0].login else "unassigned" end) as $assignee |
    # Priority heuristic from labels
    (if (.labels|map(.name)|index("critical")) != null then "P1"
     elif (.labels|map(.name)|index("high")) != null then "P2"
     elif (.labels|map(.name)|index("medium")) != null then "P3"
     else "P4" end) as $priority |
    # Difficulty heuristic from comment count and a possible "blocked" label
    (if (.comments|length) > 15 or (.labels|map(.name)|index("blocked")) != null then "D3"
     elif (.comments|length) > 5 then "D2"
     else "D1" end) as $difficulty |
    "\($priority)/\($difficulty)" as $rating |
    "| #\(.number) | \(.title) | \($age)d | \($assignee) | \($rating) |"
  ) | .[]
'
```

**Explanation of the `jq` pipeline**
- Filters for issues with the `ready` label.
- Calculates the age in days (`$age`).
- Picks the first assignee (or `unassigned`).
- Determines **priority** (`P1`‑`P4`) from `critical`, `high`, `medium` labels (default `P4`).
- Determines **difficulty** (`D1`‑`D3`) from comment count and a possible `blocked` label.
- Emits a markdown table row with the combined rating `P?/D?`.

### 4. Run the script

```bash
./discover_ready_issues.sh > issue_report.md
```

The report will be printed to `issue_report.md`. You can copy it into release notes, a planning document, or a project board.

### 5. Optional CSV export

If you need a CSV for further analysis, replace the final `jq` block with a CSV output format, e.g.:

```bash
... | "\($priority)/\($difficulty),\(.number),\(.title),\($age),\($assignee)" ...
```

---

**Notes**
- The skill assumes the `gh` CLI is authenticated and has access to the Octave repository.
- Adjust label names (`ready`, `critical`, `high`, `medium`, `blocked`) to match your project's conventions.
- The heuristics for priority/difficulty are simple and can be refined.

---

**Usage example**
```bash
./discover_ready_issues.sh
```

The script will print the markdown report to stdout.
