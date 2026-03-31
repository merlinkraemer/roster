# Roster — CLI Spec (Revised)

---

## What it does

Roster reads a development plan, builds an agent team, generates per-agent prompts, and monitors git while agents work in parallel. The user copies each prompt into a separate coding agent session (Claude Code, Cursor, etc.).

---

## Commands

6 commands: `auth`, `run`, `init`, `split`, `prompts`, `review`

---

## Core flow: `roster run`

```
$ roster run plan.md
```

### Step 1 — Agent roster

```
? Max high-tier agents available: 3
  Low-tier agents: unlimited (model decides how many)

⠋ Analyzing plan to build team...
```

The model reads the plan and proposes a team. It decides how many high-tier agents to actually use (up to the budget). It may use fewer than the max if the plan doesn't warrant it.

```
┌──────────┬──────┬───────────────────────────────────┐
│ Name     │ Tier │ Domains                           │
├──────────┼──────┼───────────────────────────────────┤
│ core     │ high │ onboarding, navigation, models    │
│ polish   │ high │ cards, room header, empty states  │
│ docs     │ low  │ copy system, language guidelines   │
│ tests    │ low  │ test scaffolding                  │
└──────────┴──────┴───────────────────────────────────┘

Used 2 of 3 available high-tier agents.

? Accept this roster?
> Accept
  Edit manually
  Cancel
```

**What changed from v1:** Role field removed. The model decides agent count within a budget rather than filling an exact number. Roster table is simpler: name, tier, domains.

### Step 2 — Work assignment

```
⠋ Mapping plan to agents...
```

The model maps work from the plan to agents. If the plan contains structured work packages (WPs, sections, phases), it assigns those as units — it does not re-decompose them into smaller file-level tasks. If the plan is unstructured prose, it decomposes into logical work units first, then assigns.

The model also produces a file ownership map: every file that will be touched is assigned to exactly one agent.

```
┌───────┬────────────────────────────────────┬────────┬───────────────────────────┐
│ Agent │ Work                               │ Tier   │ Files owned               │
├───────┼────────────────────────────────────┼────────┼───────────────────────────┤
│ core  │ WP1 (onboarding funnel) +          │ high   │ RoomOnboardingViews.swift  │
│       │ WP2 (creator bottom bar)           │        │ RoomViews.swift            │
│       │                                    │        │ RoomModels.swift           │
├───────┼────────────────────────────────────┼────────┼───────────────────────────┤
│ polish│ WP3 (simplify home cards) +        │ high   │ RoomSubscriberViews.swift  │
│       │ WP4 (room header + empty states)   │        │ RoomCreatorViews.swift     │
│       │                                    │        │ RoomServices.swift         │
├───────┼────────────────────────────────────┼────────┼───────────────────────────┤
│ docs  │ WP5 (copy and language system)     │ low    │ docs/plan/copy-system.md   │
├───────┼────────────────────────────────────┼────────┼───────────────────────────┤
│ tests │ Test scaffolding for onboarding    │ low    │ Tests/RoomOnboardingTests… │
│       │ and navigation                     │        │ Tests/RoomNavigationTests… │
└───────┴────────────────────────────────────┴────────┴───────────────────────────┘

? Accept this assignment?
> Accept
  Edit manually
  Cancel
```

**What changed from v1:** Work assignment respects plan structure instead of re-slicing by file. The table shows work units (WPs or logical chunks), not atomic task IDs. File ownership is derived from the work, not the other way around.

### Step 3 — Generate prompts

No LLM call. Prompts are generated locally from templates. **Two different templates based on tier.**

#### High-tier prompt template

Lean. The model is smart — give it boundaries and let it plan.

```
╭─────────────────────────── core ────────────────────────────╮
│                                                              │
│  # Assignment                                                │
│                                                              │
│  You are **core**. You own the following work in this        │
│  parallel agent run:                                         │
│                                                              │
│  - WP1 — Rebuild onboarding into 5-screen funnel            │
│  - WP2 — Implement creator-only 2-item bottom bar            │
│                                                              │
│  ## Files you own                                            │
│                                                              │
│  You may ONLY modify these files:                            │
│  - `apps/ios/RoomIOS/RoomOnboardingViews.swift`              │
│  - `apps/ios/RoomIOS/RoomViews.swift`                        │
│  - `apps/ios/RoomIOS/RoomModels.swift`                       │
│                                                              │
│  ## References                                               │
│                                                              │
│  - Design spec: `docs/plan/design-spec.md`                   │
│  - Refactor plan: `docs/refactor-ui.md`                      │
│  - Coordination: `.roster/COORDINATION.md`                   │
│                                                              │
│  Read these before starting. The spec is the source of       │
│  truth for what to build. The refactor plan describes the    │
│  gap between current state and spec.                         │
│                                                              │
│  ## Parallel context                                         │
│                                                              │
│  Other agents are working at the same time:                  │
│  - **polish** owns RoomSubscriberViews.swift,                │
│    RoomCreatorViews.swift, RoomServices.swift                │
│  - **docs** owns docs/plan/copy-system.md                    │
│  - **tests** owns Tests/RoomOnboardingTests.swift,           │
│    Tests/RoomNavigationTests.swift                           │
│                                                              │
│  Do not touch their files. If you need something from        │
│  their domain, leave a TODO comment and move on.             │
│                                                              │
│  ## Rules                                                    │
│                                                              │
│  1. Only modify your files.                                  │
│  2. Prefix commits with [core].                              │
│  3. Commit after each logical change.                        │
│  4. Read COORDINATION.md first.                              │
│                                                              │
╰──────────────────────────────────────────────────────────────╯
```

**What's NOT in the high-tier prompt:** No plan text dump. No detailed implementation instructions. No step-by-step task breakdown. The model reads the spec and plan files itself and figures out the implementation.

#### Low-tier prompt template

Detailed. Spell out exactly what to produce.

```
╭─────────────────────────── docs ────────────────────────────╮
│                                                              │
│  # Assignment                                                │
│                                                              │
│  You are **docs**. You have one task in this parallel run.   │
│                                                              │
│  ## Task                                                     │
│                                                              │
│  Create a centralized copy and language system document.     │
│                                                              │
│  The document must include:                                  │
│  - Vocabulary decisions: recommend one verb for room         │
│    subscription (join vs visit vs follow) with rationale     │
│  - Product voice guidelines: warm, playful, personal tone    │
│    with examples of good and bad copy                        │
│  - DM usage rules: when to use "DM" vs "message" vs         │
│    "notification" to avoid implying two-way messaging        │
│  - Reference copy strings for:                               │
│    - All 5 onboarding screens (exact headlines, supporting   │
│      lines, button labels from design-spec.md)               │
│    - Empty states (no rooms joined, new creator room)        │
│    - Core CTAs (join, create, post, settings)                │
│    - Error messages                                          │
│                                                              │
│  ## Output file                                              │
│                                                              │
│  Write to: `docs/plan/copy-system.md`                        │
│  Do NOT touch any other files.                               │
│                                                              │
│  ## References                                               │
│                                                              │
│  - Design spec: `docs/plan/design-spec.md`                   │
│    (Read the Language and Terminology section carefully)      │
│  - Refactor plan: `docs/refactor-ui.md`                      │
│    (See Gap 6: Language and Copy System)                      │
│  - Coordination: `.roster/COORDINATION.md`                   │
│                                                              │
│  ## Parallel context                                         │
│                                                              │
│  Other agents are working at the same time:                  │
│  - **core** owns RoomOnboardingViews.swift,                  │
│    RoomViews.swift, RoomModels.swift                         │
│  - **polish** owns RoomSubscriberViews.swift,                │
│    RoomCreatorViews.swift, RoomServices.swift                │
│  - **tests** owns test files                                 │
│                                                              │
│  Your copy document will be referenced by core and polish    │
│  after their work. Focus on getting the reference strings    │
│  right.                                                      │
│                                                              │
│  ## Rules                                                    │
│                                                              │
│  1. Only modify your file.                                   │
│  2. Prefix commits with [docs].                              │
│  3. Commit after each logical change.                        │
│  4. Read COORDINATION.md first.                              │
│                                                              │
╰──────────────────────────────────────────────────────────────╯
```

**The difference:** Low-tier gets the task fully specified — what sections to include, what to reference, what format. High-tier gets the work scope and boundaries, then plans its own approach.

### Step 4 — Monitor (optional)

```
? Start monitoring?
> Yes
  Skip
```

Passive only. Watches git, no manual commands except `q` to quit.

```
┌────────┬──────────┬─────────┬───────────────┬──────────────┐
│ Agent  │ Status   │ Commits │ Files Changed │ Last Commit  │
├────────┼──────────┼─────────┼───────────────┼──────────────┤
│ core   │ active   │ 3       │ 2             │ 2m ago       │
│ polish │ active   │ 1       │ 1             │ 5m ago       │
│ docs   │ active   │ 2       │ 1             │ 1m ago       │
│ tests  │ idle     │ 0       │ 0             │ —            │
└────────┴──────────┴─────────┴───────────────┴──────────────┘

[core] abc1234 [core] rebuild onboarding as 5-screen state machine
  + RoomOnboardingViews.swift (modified)
[docs] def5678 [docs] initial copy system with vocabulary decisions
  + docs/plan/copy-system.md (created)
```

Status is auto-detected: "active" = committed in last 5 minutes, "idle" = no commits in 5+ minutes, "done" = all owned files touched + idle for 10 minutes.

No manual `done`, `blocked`, or `output` commands. The monitor is a dashboard, not a control plane.

### Step 5 — Review (optional)

```
$ roster review
```

Generates a summary from the git log. Optional LLM call — can also be a local summary of commits per agent + files changed.

---

## LLM calls

| Step | LLM call? | What happens |
|------|-----------|--------------|
| Max budget | No | User types a number |
| Suggest roster | Yes | Plan → agent team (names, tiers, domains, count) |
| Accept roster | No | User picks Accept/Edit/Cancel |
| Map work | Yes | Plan + roster → work assignment + file ownership |
| Accept assignment | No | User picks Accept/Edit/Cancel |
| Generate prompts | No | Template-based, tier-aware, no LLM |
| Monitor | No | Passive git polling |
| Review | Optional | Git log → summary |

**Total: 2 required LLM calls** (roster + assignment). Review is optional third.

---

## Prompt design principles

### High-tier agents

The model is the planner. The prompt provides:
- What work packages / scope they own
- Which files they may touch (hard boundary)
- Where to find specs and plans (file paths, not content)
- Who else is working and what they own (collision avoidance)
- Rules (commit convention, stay in lane, read COORDINATION.md)

The prompt does NOT provide:
- The plan text (the model reads it from the file path)
- Implementation instructions (the model figures this out)
- Step-by-step task breakdowns (the model plans its own approach)
- Role personas (unnecessary — domains and work scope are sufficient)

### Low-tier agents

The prompt is the plan. It provides everything a high-tier prompt provides, plus:
- Detailed task description (what to produce, what format, what sections)
- Specific references within the spec/plan (which sections to read)
- Expected output shape (file structure, content outline)

Low-tier agents need rails. Without explicit instructions they drift into irrelevant work or produce the wrong format.

---

## COORDINATION.md

Generated locally (no LLM call) after the work assignment step. Contains:

### File ownership map

```
## File ownership

core:
  - apps/ios/RoomIOS/RoomOnboardingViews.swift
  - apps/ios/RoomIOS/RoomViews.swift
  - apps/ios/RoomIOS/RoomModels.swift

polish:
  - apps/ios/RoomIOS/RoomSubscriberViews.swift
  - apps/ios/RoomIOS/RoomCreatorViews.swift
  - apps/ios/RoomIOS/RoomServices.swift

docs:
  - docs/plan/copy-system.md

tests:
  - apps/ios/RoomIOS/Tests/RoomOnboardingTests.swift
  - apps/ios/RoomIOS/Tests/RoomNavigationTests.swift
```

### Work scope per agent

Brief description of what each agent is doing, so any agent can understand the full picture without reading everyone else's prompt.

### Cross-agent dependency rule

```
## Dependencies

If you need something from another agent's domain:
- Leave a TODO comment in your code describing what you need
- Do not block on it — use a stub, mock, or placeholder
- Do not modify their files
```

This is intentionally simple. No contracts, no interface definitions. Agents leave TODOs, the user reconciles after the run. This is v1 — sophistication comes later if needed.

---

## Roster suggest prompt (LLM call 1)

```
System:
You are a technical project manager. Given a development plan
and a high-tier agent budget, propose the optimal team.

Rules:
- Use UP TO N high-tier agents (may use fewer if the plan
  doesn't warrant it)
- Low-tier agents are unlimited; add as many as useful
- High-tier agents handle ALL implementation work
- Low-tier agents handle ONLY: documentation, config,
  test scaffolding, examples, CI/CD
- Low-tier agents must NEVER build features or modify
  production code
- Choose short one-word lowercase names (e.g. "core",
  "ui", "docs")
- Agents should have non-overlapping domains

Return a JSON array. Each agent: {name, tier, domains[]}.
No markdown fences, no explanation.

User:
## High-Tier Budget: up to N
## Development Plan
{plan text}
```

## Work assignment prompt (LLM call 2)

```
System:
You are a technical project manager. Given a development plan
and an agent roster, assign work to agents and produce a file
ownership map.

If the plan contains structured work packages (WPs, sections,
phases, numbered items), assign those as whole units. Do not
break them into smaller pieces. Group related WPs onto the
same agent when they are tightly coupled.

If the plan is unstructured, decompose it into logical work
units first, then assign.

For each agent, list:
- work: array of work descriptions (WP names or logical units)
- files: array of file paths this agent exclusively owns

Rules:
- No two agents may own the same file
- Low-tier agents may only own documentation, config,
  test scaffolding, or example files
- Every file that will be touched must be assigned
- Coupled work should go to the same agent

Return a JSON array. Each entry:
{agent, work[], files[]}.
No markdown fences, no explanation.

User:
## Agent Roster
{roster JSON}
## Development Plan
{plan text}
```

---

## Changes from v1

| Area | v1 | Revised |
|------|-----|---------|
| Agent count | User sets exact high-tier count | User sets max budget, model decides count |
| Role field | builder/architect/explorer/reviewer | Removed |
| Decomposition | File-level task split | Work-package-level assignment |
| Task table | task-1, task-2... with complexity ratings | Work units mapped to agents |
| High-tier prompts | Full plan dump + detailed task instructions | File paths + scope + boundaries only |
| Low-tier prompts | Same as high-tier | Detailed task spec with format and sections |
| Plan in prompts | Entire plan text embedded | Referenced by file path (model reads it) |
| Monitor | Manual commands (done, blocked, output) | Passive git watching only |
| Monitor status | User-reported | Auto-detected from git activity |
| COORDINATION.md | Not yet written | File ownership + work scope + TODO rule |
| LLM calls | 2-3 | 2 required, 1 optional |
