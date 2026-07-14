#!/usr/bin/env bash
set -euo pipefail

# unblock-ready-items.sh
# Automatically moves Blocked items to Ready when all their dependencies are Done.
# Uses GitHub Project API (v2) and issue body parsing for dependency tracking.
# Outputs a markdown report to stdout.

REPO="Svagtlys/Octave"
PROJECT_ID="PVT_kwHOArV0zM4Bb38A"
STATUS_FIELD_ID="PVTSSF_lAHOArV0zM4Bb38AzhWlGD8"
BLOCKED_OPTION_ID="de29ef41"
READY_OPTION_ID="1905902e"

MOVED_ITEMS=""
BLOCKED_REPORT=""

echo "=== Fetching Blocked items ==="
BLOCKED_ITEMS=$(gh project item-list 7 --owner Svagtlys --limit 100 --format json \
  --jq '[.items[] | select(.status == "Blocked")] | .[] | {id, number: .content.number, title: .title}')

echo "$BLOCKED_ITEMS" | jq -r '.number' | while read -r issue_num; do
  ISSUE_TITLE=$(echo "$BLOCKED_ITEMS" | jq -r "select(.number == ${issue_num}) | .title")
  
  # Fetch issue body to extract dependencies
  DEPS=$(gh issue view "$issue_num" --repo "$REPO" --json body --jq '.body' \
    | grep -oP '\[\#(\d+)\]' \
    | grep -oP '\d+' \
    || true)

  if [[ -z "$DEPS" ]]; then
    echo "Issue #${issue_num}: No dependencies found → Moving to Ready"
    ITEM_ID=$(echo "$BLOCKED_ITEMS" | jq -r "select(.number == ${issue_num}) | .id")
    gh project item-edit --id "$ITEM_ID" --project-id "$PROJECT_ID" \
      --field-id "$STATUS_FIELD_ID" --single-select-option-id "$READY_OPTION_ID"
    echo "- [#${issue_num}](${REPO}/issues/${issue_num}) — ${ISSUE_TITLE}" >> /tmp/moved_items.md
    continue
  fi

  ALL_DONE=true
  BLOCKERS=""
  for dep in $DEPS; do
    DEP_STATUS=$(gh issue view "$dep" --repo "$REPO" --json state --jq '.state')
    if [[ "$DEP_STATUS" != "CLOSED" ]]; then
      BLOCKERS="${BLOCKERS} #${dep}"
      ALL_DONE=false
    fi
  done

  if $ALL_DONE; then
    echo "Issue #${issue_num}: All dependencies closed → Moving to Ready"
    ITEM_ID=$(echo "$BLOCKED_ITEMS" | jq -r "select(.number == ${issue_num}) | .id")
    gh project item-edit --id "$ITEM_ID" --project-id "$PROJECT_ID" \
      --field-id "$STATUS_FIELD_ID" --single-select-option-id "$READY_OPTION_ID"
    echo "- [#${issue_num}](${REPO}/issues/${issue_num}) — ${ISSUE_TITLE}" >> /tmp/moved_items.md
  else
    echo "Issue #${issue_num}: Still blocked by${BLOCKERS}"
  fi
done

# Print summary report
echo ""
echo "=== Summary Report ==="
if [[ -f /tmp/moved_items.md ]] && [[ -s /tmp/moved_items.md ]]; then
  echo ""
  echo "## 🟢 Items moved to Ready"
  cat /tmp/moved_items.md
  echo ""
  rm /tmp/moved_items.md
else
  echo ""
  echo "No items were moved to Ready."
fi
echo "=== Done ==="
