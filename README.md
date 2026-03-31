# roster

Split a development plan across parallel AI agents, each working in its own context window.

## Install

```bash
uv tool install git+https://github.com/merlinkramer/roster.git
```

Or with pip:

```bash
pip install git+https://github.com/merlinkramer/roster.git
```

## Usage

```bash
roster auth          # save your Z.AI API key
roster run plan.md   # full flow: roster → assign work → generate prompts → monitor
```

Or step by step:

```bash
roster init          # define your agent roster manually
roster split plan.md # assign work packages to agents
roster prompts       # generate per-agent prompt files
roster review        # review the completed run
```

## How it works

`roster run plan.md` does everything in one go:

1. **Roster** — you set a premium agent budget; the model proposes a team (premium agents for implementation, budget agents for docs/config/tests)
2. **Assign** — the model maps work packages from your plan to agents, with exclusive file ownership per agent
3. **Prompts** — generates a per-agent prompt file and a `COORDINATION.md` for the whole team; no LLM call
4. **Monitor** — watches git commits in real time to track agent progress

Copy each agent's prompt into a separate coding agent session (Claude Code, Cursor, etc.) and start them in parallel.

## Commands

```
roster auth [--test]     Manage your Z.AI API key
roster run <plan>        Full run flow
roster init              Set up agent roster manually
roster split <plan>      Assign work from a plan to agents
roster prompts           Generate prompt files from a saved assignment
roster review            Generate a post-run review from git log
```

Run `roster --help` or `roster <command> --help` for details.
