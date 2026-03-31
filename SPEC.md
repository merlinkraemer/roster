# Roster

A minimal CLI tool for splitting a development plan across multiple AI coding agents working in parallel on the same codebase.

## Problem

When running multiple AI agents (Claude Code, Cursor, OpenCode, Antigravity, etc.) in parallel on one repo, you need:
- Non-overlapping task assignments so agents don't clobber each other's work
- Self-contained prompts with clear file boundaries
- A way to review what happened after the run

## Design Principles

- Thin wrapper around API calls — not an agentic system itself
- State lives in plain markdown/JSON files
- Agents are run manually by the user (paste prompt, let it run)
- No live monitoring — review is done after the fact via git log analysis
- Each file in `.roster/` has exactly one writer (append-only invariant)
  - `split-plan.json` — written by `split`, read by `prompts` and `review`
  - `prompts/<agent>.md` — written by `prompts`, read only by the human user
  - `outputs/<agent>.md` — written only by the human user (paste agent output here)
  - `COORDINATION.md` — written by `prompts`, read-only for all agents
  - `review.md` — written by `review`, never mutated by agents

## Architecture

```
roster/
├── cli.py              # typer CLI entry point
├── run.py              # orchestrator (prepare_run) + interactive monitor
├── decompose.py        # plan → atomic tasks via LLM
├── assign.py           # tasks × agents → assignments (confidence × domain fit)
├── prompts.py          # assignments → per-agent prompt files
├── review.py           # git log + output files → review doc
├── config.py           # agent roster persistence
└── llm.py              # thin API client (Z.AI coding endpoint)
```

## Commands

### `roster run <plan-path>`

One command to set up agents, split the plan, generate prompts, and start monitoring.

1. **Roster check**: If `.roster/roster.json` exists, asks to reuse or reconfigure. If not, runs the init flow.
2. **Split + Prompts**: Decomposes the plan into tasks, validates assignments, generates COORDINATION.md and per-agent prompt files.
3. **Monitor**: Starts an interactive monitoring session (see below).

```
$ roster run plan.md
Found roster (claude-code, cursor, opencode-glm5). Reuse? [y/n]: y
⠋ Decomposing plan...
┌──────┬─────────────────────────────┬──────────┬─────────────┬──────────┐
│ ID   │ Description                 │ Agent    │ Complexity  │ Files    │
│ ...  │ ...                         │ ...      │ ...         │ ...      │
└──────┴─────────────────────────────┴──────────┴─────────────┴──────────┘
✓ Prompts written to .roster/prompts/
✓ COORDINATION.md at .roster/COORDINATION.md

Starting monitor...
```

### Monitoring

The monitor watches the repo for git commits and file changes, and provides an interactive REPL:

| Command | Description |
|---------|-------------|
| `done <agent>` | Mark agent as done |
| `blocked <agent>` | Mark agent as blocked |
| `output <agent>` | Record agent output (multiline, blank line to end) |
| `status` | Show current status table |
| `review` | Generate review summary |
| `q` / `quit` | Stop monitoring |

Status table shows: Agent \| Status (working/done/blocked) \| Commits \| Files Changed

New commits with `[agent-name]` prefix are automatically attributed. File changes are tracked per agent based on ownership in the split plan.

Interactive setup of the agent roster. Saved to `.roster/roster.json`.

```
$ roster init
How many agents? 3
Agent 1 name: claude-code
Agent 1 archetype (optional — craftsman/architect/explorer/reviewer, or skip): craftsman
  → domains pre-filled: code quality, refactoring, tests, best practices
  Confidence (0-100): 100
  Override domains? (leave blank to keep pre-filled): backend, api, tests, infra
  Max complexity (low/medium/high/any): any
Agent 2 name: cursor
Agent 2 archetype (optional): architect
  → domains pre-filled: system design, api contracts, data models, infra
  Confidence (0-100): 90
  Override domains? frontend, ios, swift
  Max complexity: high
Agent 3 name: opencode-glm5
Agent 3 archetype (optional): reviewer
  → domains pre-filled: docs, comments, tests, simple refactors
  Confidence (0-100): 50
  Override domains? (leave blank):
  Max complexity: low
Roster saved.
```

**Archetypes** are optional presets that auto-fill domain hints and inject persona framing into the agent's prompt:

| Archetype | Persona framing | Default domains |
|-----------|----------------|-----------------|
| `craftsman` | Code quality, precision, best practices | code quality, refactoring, tests |
| `architect` | System design, contracts, structure | system design, api contracts, infra |
| `explorer` | New features, prototyping, breadth | feature implementation, prototyping |
| `reviewer` | Low-risk, documentation, simple fixes | docs, comments, tests, simple refactors |

Roster can be edited manually or re-run `init` to overwrite.

### `roster split <path>`

Takes a plan doc (single file or directory of docs). Sends it to the LLM along with the agent roster. Returns a split plan with:

- Atomic tasks, each with:
  - Description
  - File/directory ownership (exclusive — no overlap)
  - Complexity estimate (low/medium/high)
  - Assigned agent + reasoning
- Higher complexity tasks → higher confidence agents
- File boundaries enforced: no two agents touch the same files

Outputs `.roster/split-plan.json` and prints a human-readable summary for approval.

The user reviews and either approves or edits the JSON before proceeding.

### `roster prompts`

Reads the approved split plan. Generates:

1. **`.roster/COORDINATION.md`** — shared read-only doc, given to all agents. Contains:
   - Full task list with owners
   - File ownership boundaries for every agent
   - Sequencing dependencies
   - Commit convention reminder

   All agent prompts instruct: "Read COORDINATION.md first to understand the full picture."

2. **`.roster/prompts/<agent-name>.md`** — one per agent. Contains:
   - Archetype persona framing (if set): "You are The Craftsman: prioritize code quality..."
   - Task description with full context from the original plan
   - Explicit file/directory boundary ("you own these files, do NOT touch anything else")
   - Commit convention: prefix all commits with `[agent-name]`, commit after each logical change
   - Any dependencies or sequencing notes
   - Path to COORDINATION.md for situational awareness

### `roster review`

Reads:
- `git log` from the target repo (parses `[agent-name]` commit prefixes)
- Any output files dropped in `.roster/outputs/<agent-name>.md`

Generates `.roster/review.md` with:
- Per-agent summary: tasks completed, commits, files touched
- Boundary violations (files touched outside assigned ownership)
- Timeline of changes
- Pasted agent outputs (if any)
- Open items / things to manually verify

## Configuration

### `.roster/roster.json`

```json
{
  "agents": [
    {
      "name": "claude-code",
      "archetype": "craftsman",
      "confidence": 100,
      "domains": ["backend", "api", "tests", "infra"],
      "max_complexity": "any"
    },
    {
      "name": "cursor",
      "archetype": "architect",
      "confidence": 90,
      "domains": ["frontend", "ios", "swift"],
      "max_complexity": "high"
    },
    {
      "name": "opencode-glm5",
      "archetype": "reviewer",
      "confidence": 50,
      "domains": ["docs", "tests", "simple-refactors"],
      "max_complexity": "low"
    }
  ]
}
```

### `.roster/split-plan.json`

```json
{
  "source": "path/to/plan.md",
  "delegation_strategy": "expertise_based",
  "tasks": [
    {
      "id": "task-1",
      "description": "Implement session token expiry endpoint",
      "files": ["apps/api/room_api/api/auth.py", "apps/api/tests/test_auth.py"],
      "complexity": "medium",
      "agent": "claude-code",
      "reason": "Backend task, matches craftsman archetype + backend domain"
    }
  ]
}
```

## Assignment Logic

Task assignment in `assign.py` follows two steps:

1. **Hard gate (structural)**: filter agents by `max_complexity`. A task with complexity `high` is never assigned to an agent with `max_complexity: low`, regardless of domain fit. Enforced in code before any LLM reasoning.

2. **Soft scoring (LLM)**: among eligible agents, the LLM scores by domain match × confidence and picks the best fit. Strategy is hardcoded to `expertise_based` — domains that match the task's files/area, confidence as tiebreaker.

## LLM Usage

All LLM calls are simple structured generation (no tool use, no agentic loops):

1. **Decompose + assign**: plan text + roster (with archetypes, domains, max_complexity) → JSON list of tasks, pre-filtered by hard gates, assigned by domain fit
2. **Prompt generation**: task + plan context + archetype persona → markdown prompt + COORDINATION.md
3. **Review summarization**: git log + outputs → markdown summary

Any model works. Default to Claude via Anthropic SDK. Can swap in OpenAI-compatible endpoints for GLM5 or others via `ROSTER_MODEL` and `ROSTER_BASE_URL` env vars.

## Tech Stack

- Python 3.12+
- `typer` — CLI framework
- `anthropic` — LLM API client
- `rich` — terminal output formatting
- No other dependencies

## Target Repo

The CLI operates on a target repo (defaults to cwd). All roster state lives in `.roster/` within that repo (add to `.gitignore`).

## Non-Goals

- Auto-launching agents
- Real-time conflict resolution
- Supporting non-git repos
- Complex agent orchestration (this is a coordination tool, not an agent runtime)
