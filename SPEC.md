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
├── assign.py           # tasks × agents → assignments (tier × domain fit)
├── prompts.py          # assignments → per-agent prompt files
├── review.py           # git log + output files → review doc
├── config.py           # agent roster persistence
└── llm.py              # thin API client (Z.AI coding endpoint)
```

## Commands

### `roster run <plan-path>`

One command to set up agents, split the plan, generate prompts, and start monitoring.

1. **Roster check**: If `.roster/roster.json` exists, asks to reuse or reconfigure. If not, the LLM suggests agents based on the plan.
2. **Split + Prompts**: Decomposes the plan into tasks, validates assignments, generates COORDINATION.md and per-agent prompt files.
3. **Monitor**: Starts an interactive monitoring session (see below).

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

Status table shows: Agent | Status (working/done/blocked) | Commits | Files Changed

### `roster init`

Interactive setup of the agent roster. Saved to `.roster/roster.json`.

```
$ roster init
How many agents? 3
Agent 1
  Name: backend
  Role (optional — builder/architect/explorer/reviewer, or skip): builder
    → domains pre-filled: code quality, refactoring, tests, best practices
  Tier (low/medium/high): high
Agent 2
  Name: frontend
  Role: explorer
  Tier: medium
Agent 3
  Name: docs
  Role: reviewer
  Tier: low
```

**Roles** are optional presets that auto-fill domain hints and inject persona framing into the agent's prompt:

| Role | Persona framing | Default domains |
|------|----------------|-----------------|
| `builder` | Code quality, precision, best practices | code quality, refactoring, tests |
| `architect` | System design, contracts, structure | system design, api contracts, infra |
| `explorer` | New features, prototyping, breadth | feature implementation, prototyping |
| `reviewer` | Low-risk, documentation, simple fixes | docs, comments, tests, simple refactors |

**Tiers** determine what complexity of tasks an agent can handle:

| Tier | Can handle | Typical use |
|------|-----------|-------------|
| `low` | Low complexity tasks only | Docs, config, git chores, simple refactors |
| `medium` | Low + medium tasks | Standard implementation |
| `high` | Any task complexity | Complex architecture, hard problems, cross-cutting concerns |

Roster can be edited manually or re-run `init` to overwrite.

### `roster split <path>`

Takes a plan doc (single file or directory of docs). Sends it to the LLM along with the agent roster. Returns a split plan with:

- Atomic tasks, each with:
  - Description
  - File/directory ownership (exclusive — no overlap)
  - Complexity estimate (low/medium/high)
  - Assigned agent + reasoning
- Task complexity gated by agent tier (low-tier agents only get low-complexity tasks)
- File boundaries enforced: no two agents touch the same files

### `roster prompts`

Reads the approved split plan. Generates:

1. **`.roster/COORDINATION.md`** — shared read-only doc, given to all agents. Contains:
   - Full task list with owners
   - File ownership boundaries for every agent
   - Commit convention reminder

2. **`.roster/prompts/<agent>.md`** — one per agent. Contains:
   - Role persona framing (if set)
   - Task description with full context from the original plan
   - Explicit file/directory boundary
   - Commit convention: prefix all commits with `[agent-name]`
   - Path to COORDINATION.md for situational awareness

### `roster review`

Reads git log (parses `[agent-name]` commit prefixes) and any output files in `.roster/outputs/`. Generates `.roster/review.md`.

## Configuration

### `.roster/roster.json`

```json
{
  "agents": [
    {
      "name": "backend",
      "tier": "high",
      "role": "builder",
      "domains": ["backend", "api", "tests", "infra"]
    },
    {
      "name": "frontend",
      "tier": "medium",
      "role": "explorer",
      "domains": ["frontend", "ui", "components"]
    },
    {
      "name": "docs",
      "tier": "low",
      "role": "reviewer",
      "domains": ["docs", "readme", "examples"]
    }
  ]
}
```

## Assignment Logic

Task assignment in `assign.py`:

1. **Hard gate (structural)**: filter agents by tier. A task with complexity `high` is never assigned to an agent with `tier: low`. Enforced in code before any LLM reasoning.

2. **LLM assignment**: among eligible agents, the LLM assigns by domain fit. The LLM knows each agent's tier and domains.

## LLM Usage

All LLM calls are simple structured generation (no tool use, no agentic loops):

1. **Suggest roster** (in `roster run`): plan text → JSON array of agents with names, roles, tiers, domains
2. **Decompose + assign**: plan text + roster → JSON list of tasks, gated by tier, assigned by domain fit
3. **Prompt generation**: task + plan context + role persona → markdown prompt + COORDINATION.md
4. **Review summarization**: git log + outputs → markdown summary

Uses Z.AI coding endpoint by default. Configurable via `ROSTER_MODEL` and `ROSTER_BASE_URL` env vars.

## Tech Stack

- Python 3.12+
- `typer` — CLI framework
- `requests` — HTTP client for LLM API
- `rich` — terminal output formatting
- No other dependencies

## Target Repo

The CLI operates on a target repo (defaults to cwd). All roster state lives in `.roster/` within that repo (add to `.gitignore`).

## Non-Goals

- Auto-launching agents
- Real-time conflict resolution
- Supporting non-git repos
- Complex agent orchestration (this is a coordination tool, not an agent runtime)
