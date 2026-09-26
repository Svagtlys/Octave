# Draft: follow-up work item — concurrent tool execution

Architect mode cannot run `gh`. Run this from the repo root after the
orchestration-loop PR (#103) is planned/merged. Check open milestones first
(`gh milestone list --repo Svagtlys/Octave`) and substitute `<milestone>`.

```bash
gh issue create \
  --repo Svagtlys/Octave \
  --title "feat(agent): execute parallel tool calls concurrently" \
  --label "enhancement" \
  --label "area:agent" \
  --milestone "<milestone>" \
  --body "$(cat plans/2026-09-25-issue-concurrent-tool-execution-body.md)"
```

Then add to the Milestone Tracker project (7) and set status per the
create-work-item skill.

---

# Issue body (start)

## Description

The tool-use orchestration loop (`octave.agent.loop`, issue #79) executes
multiple tool calls returned in one completion sequentially, making a turn's
tool latency the sum of all call latencies. Execute them concurrently via an
anyio task group inside a turn, so latency becomes the slowest call.

## Why

MCP servers include slow operations (search, scraping, long jobs). A model
emitting 4 fast + 1 slow call pays the full sum today; users watch a stalled
turn. `McpClient` is already safe for concurrent use within one event loop
(SDK request-ID correlation), so the capability is unused.

## Scope

- [ ] `octave/agent/loop.py`: fan out tool calls of one turn via
      `anyio.create_task_group`; collect `ToolOutcome`s; append `role="tool"`
      messages in model call order (deterministic history)
- [ ] Preserve the two-tier error split: per-call failures stay
      model-correctable tool messages; one failing call must not cancel
      siblings (contain within the task group)
- [ ] Optional `parallel_tool_calls: bool = True` loop option (and pass-through
      of the provider field via `CompletionRequest.extra` if desired)
- [ ] Tests: concurrent happy path (fake executor with staggered sleeps),
      sibling isolation on failure, ordering of appended tool messages

## Notes

- Protocol invariant unchanged: all N results for an assistant `tool_calls`
  message must be appended before the next completion.
- Sequential execution ships first in #79; this is the promotion path recorded
  in the #79 design spec's deferral table.

# Issue body (end)
