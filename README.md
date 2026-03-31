# roster

Split a development plan across parallel AI agents, each working in its own context window.

## Install

```bash
uv tool install git+https://github.com/merlinkraemer/roster.git
```

Or with pip:

```bash
pip install git+https://github.com/merlinkraemer/roster.git
```

## Usage

```bash
roster auth          # save your LLM API key
roster init          # define your agent roster
roster split plan.md # decompose plan into agent tasks
roster prompts       # generate per-agent prompt files
roster review        # review the completed run
```

Run `roster --help` or `roster <command> --help` for details.
