import json

from .llm import call_llm
from .models import Agent, Task

_SUGGEST_SYSTEM = """You are a technical project manager. Given a development plan and the user's available high-tier agents, propose the optimal team configuration.

The user has specified how many high-tier agents they have available. Low-tier agents are unlimited. You must:
- Decide the total number of agents needed based on the plan's complexity and scope
- Assign high-tier agents to the most complex/critical parts of the plan
- Fill remaining work with low-tier agents (docs, config, tests, simple tasks)
- Choose short, simple names (one lowercase word: e.g. "backend", "frontend", "cli", "docs")

Return a JSON array of agents. Each agent must have:
- name: string (one lowercase word, e.g. "backend")
- tier: "low" or "high" (exactly {high_count} agents must be "high", the rest "low")
- role: one of "builder", "architect", "explorer", "reviewer"
- domains: array of domain strings (e.g. ["backend", "api", "auth"])

Rules:
- Use exactly {high_count} high-tier agents, no more
- High-tier agents get complex tasks (architecture, core implementation, hard problems)
- Low-tier agents get simpler tasks (docs, config, tests, simple refactors)
- Agents should have non-overlapping domain responsibilities
- Match role to the type of work

Return ONLY the JSON array, no markdown fences, no explanation."""

_DECOMPOSE_SYSTEM = """You are a technical project manager. Given a development plan and a team of AI coding agents, decompose the plan into atomic tasks and assign each task to the most suitable agent.

Return a JSON array of tasks. Each task must have:
- id: string (task-1, task-2, ...)
- description: string
- files: array of file paths or directory patterns this task owns exclusively
- complexity: "low" | "medium" | "high"
- agent: agent name from the roster
- reason: brief explanation of the assignment

Rules:
- Tasks must not share file ownership (no two tasks may list the same file)
- A task's complexity must not exceed the agent's tier (low-tier agents get low-complexity tasks, high-tier can handle anything)
- Assign by domain fit first
- Be specific about file paths; use directory patterns (e.g. "src/auth/") for broad ownership

Return ONLY the JSON array, no markdown fences, no explanation."""


def suggest_roster(plan_text: str, high_count: int) -> list[Agent]:
    """Ask the LLM to propose agents based on the plan content and high-tier budget.

    high_count: number of high-tier agents available (low-tier are unlimited).
    """
    user_msg = (
        f"## High-Tier Agents Available: {high_count}\n\n"
        f"## Development Plan\n\n{plan_text}"
    )
    system = _SUGGEST_SYSTEM.format(high_count=high_count)
    raw = call_llm(system, user_msg).strip()
    raw = _strip_fences(raw)
    agents_data = json.loads(raw)
    return [Agent(**a) for a in agents_data]


def _strip_fences(raw: str) -> str:
    """Strip markdown code fences from LLM output."""
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1]
        raw = raw.rsplit("```", 1)[0].strip()
    return raw


def decompose_plan(plan_text: str, roster: list[Agent]) -> list[Task]:
    roster_json = json.dumps(
        [
            {
                "name": a.name,
                "tier": a.tier,
                "role": a.role,
                "domains": a.domains,
            }
            for a in roster
        ],
        indent=2,
    )

    user_msg = f"## Agent Roster\n\n{roster_json}\n\n## Development Plan\n\n{plan_text}"
    raw = call_llm(_DECOMPOSE_SYSTEM, user_msg).strip()

    raw = _strip_fences(raw)

    tasks_data = json.loads(raw)
    return [Task(**t) for t in tasks_data]
