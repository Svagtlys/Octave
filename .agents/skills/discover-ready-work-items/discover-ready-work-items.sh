#!/usr/bin/env bash
set -euo pipefail

REPO="Svagtlys/Octave"

# 1. Determine active milestone
ACTIVE_MILESTONE=$(gh milestone list --repo "$REPO" --state open --json title --jq '.[0].title')
if [[ -z "$ACTIVE_MILESTONE" ]]; then
  echo "No open milestone found" >&2
  exit 1
fi

# Header for the markdown report
echo "# Issue Report – $ACTIVE_MILESTONE (generated $(date))"
echo ""
echo "| # | Title | Age | Assignee | Rating |"
echo "|---|---|---|---|---|"

# 2. Fetch ready items from project board
READY_NUMBERS=$(gh project item-list 7 --owner Svagtlys --limit 100 --format json \
  --jq '[.items[] | select(.status == "Ready")] | .[] | .content.number')

if [[ -z "$READY_NUMBERS" ]]; then
  echo ""
  echo "No items in Ready status."
  exit 0
fi

# 3. Fetch full issue details and build JSON array
ISSUES_JSON="["
first=true
for num in $READY_NUMBERS; do
  if [[ "$first" == "true" ]]; then
    first=false
  else
    ISSUES_JSON="${ISSUES_JSON},"
  fi
  ISSUES_JSON="${ISSUES_JSON}$(gh issue view "$num" --repo "$REPO" \
    --json number,title,labels,assignees,createdAt,updatedAt,comments)"
done
ISSUES_JSON="${ISSUES_JSON}]"

# 4. Process with jq and emit markdown table rows
echo "$ISSUES_JSON" | jq -r '
  map(
    . as $issue |
    # Age in days
    ((now - ($issue.createdAt | fromdate)) / 86400 | floor) as $age |
    # First assignee or "unassigned"
    (if $issue.assignees and ($issue.assignees|length)>0 then $issue.assignees[0].login else "unassigned" end) as $assignee |
    # Priority heuristic from labels
    (if ($issue.labels|map(.name)|index("critical")) != null then "P1"
     elif ($issue.labels|map(.name)|index("high")) != null then "P2"
     elif ($issue.labels|map(.name)|index("medium")) != null then "P3"
     else "P4" end) as $priority |
    # Difficulty heuristic from comment count and a possible "blocked" label
    (if ($issue.comments|length) > 15 or ($issue.labels|map(.name)|index("blocked")) != null then "D3"
     elif ($issue.comments|length) > 5 then "D2"
     else "D1" end) as $difficulty |
    "\($priority)/\($difficulty)" as $rating |
    "| #\($issue.number) | \($issue.title) | \($age)d | \($assignee) | \($rating) |"
  ) | .[]
'
