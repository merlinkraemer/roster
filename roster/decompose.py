import json

from .llm import call_llm
from .models import Agent, Task

_SUGGEST_SYSTEM = """You are a technical project manager. Given a development plan, propose an optimal team of AI coding agents to execute it in parallel.

Analyze the plan and determine:
- How many agents are needed (usually 2-4 for most plans)
- What each agent should specialize in based on the plan's domains/areas
- Names must be short, simple, lowercase labels: e.g. "backend", "frontend", "cli", "api", "web", "docs", "auth", "infra", "mobile", "config". One word, no hyphens or numbers.

Return a JSON array of agents. Each agent must have:
- name: string (one lowercase word, e.g. "backend")
- tier: "low" | "medium" | "high"
  - low: suited for simple tasks — docs, config, git chores, simple refactors
  - medium: suited for standard implementation tasks
  - high: suited for complex architecture, hard problems, cross-cutting concerns
- role: one of "builder", "architect", "explorer", "reviewer"
- domains: array of domain strings (e.g. ["backend", "api", "auth"])

Rules:
- Agents should have non-overlapping domain responsibilities
- Match role to the type of work (builder for implementation, architect for design, etc.)
- Keep agent count minimal — merge related work into fewer agents
- A low-tier agent should get simpler work, a high-tier agent should get harder work

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


def suggest_roster(plan_text: str) -> list[Agent]:
    """Ask the LLM to propose agents based on the plan content."""
    raw = call_llm(_SUGGEST_SYSTEM, f"## Development Plan\n\n{plan_text}").strip()
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
