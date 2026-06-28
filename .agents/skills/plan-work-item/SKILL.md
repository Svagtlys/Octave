---
name: plan-work-item
description: >-
  Use in architect mode after start-work-item has set up the branch and PR.
  Orchestrates brainstorming and writing-plans skills to produce a design doc
  and implementation plan. Use when the user says "plan this work item",
  "start planning", or after being directed here from start-work-item.
modeSlugs:
  - architect
---

# Plan Work Item

## Overview

Load issue context, run brainstorming to produce a design doc, then flow into writing-plans to produce an implementation plan. This skill orchestrates the full design-to-plan pipeline.

**This skill MUST run in architect mode.** It is invoked after `start-work-item` completes the git setup (branch + draft PR).

**Announce at start:** "I'm using the plan-work-item skill to design and plan this work item."

## Prerequisites

- `start-work-item` skill has already been run (branch created, draft PR open)
- You are in **architect mode**

## Step 1: Load Issue Context

Architect mode cannot run CLI commands. Gather context by reading files:

- Read [`AGENTS.md`](AGENTS.md) for project conventions, commands, and critical rules
- Read [`.agents/context/architecture.md`](.agents/context/architecture.md) for system design and component overview
- Read any existing spec or plan files referenced in the issue body
- Read the draft PR description (the `start-work-item` skill includes the issue description in the PR body)
- Ask the user to paste the issue title and body if not already available in context

## Step 2: Invoke Brainstorming

**REQUIRED SUB-SKILL:** Use the `brainstorming` skill

Pass the issue context to the brainstorming skill. It will handle:

1. Exploring project context
2. Asking clarifying questions (one at a time)
3. Proposing 2-3 approaches with trade-offs
4. Presenting the design for user approval
5. Writing the design doc to `.agents/specs/YYYY-MM-DD-<topic>-design.md`
6. Spec self-review and user review gate

**Do NOT skip to writing-plans.** The brainstorming skill must complete its full flow, including user approval of the design.

## Step 3: Writing Plans

The brainstorming skill's terminal state is invoking the writing-plans skill. Once brainstorming completes:

**REQUIRED SUB-SKILL:** Use the `writing-plans` skill

The writing-plans skill will:
1. Map out file structure (which files to create/modify)
2. Break the implementation into bite-sized tasks (2-5 minutes each)
3. Include TDD steps (write failing test, run it, implement, verify)
4. Save the plan to `.agents/specs/YYYY-MM-DD-<feature-name>.md`
5. Present the plan to the user for approval

## Step 4: Direct to Implementation

Once the plan is approved, inform the user that planning is complete. Provide a copy-able prompt for the user to pass to the code mode with the `executing-plans` skill, which should start with `/executing-plans` and include:

- The branch name
- The draft PR URL
- The links to the spec and implementation plan

**Do NOT switch to code mode or begin executing the plan.** The plan execution process should happen in a separate session initiated by the user. This keeps the design (architect mode) separate from the code work (code mode).

## Flow Diagram

```
start-work-item (code mode)
    └── creates branch + draft PR
    └── directs user to architect mode

plan-work-item (architect mode)  ← YOU ARE HERE
    ├── Step 1: Load issue context
    ├── Step 2: Invoke brainstorming
    │   └── produces design doc
    │   └── terminal state → invokes writing-plans
    ├── Step 3: writing-plans
    │   └── produces implementation plan
    └── Step 4: Direct to executing-plans

executing-plans (code mode)
    └── implements task-by-task
```

## Common Mistakes

- **Running in code mode** — This skill requires architect mode. Switch if needed.
- **Skipping brainstorming** — Always run brainstorming first, even for simple issues.
- **Skipping user review gates** — Both the design doc and implementation plan require user approval.
- **Starting implementation without an approved plan** — Wait for plan approval before touching code.
