import json

from .llm import call_llm
from .models import Agent, Task

_SYSTEM = """You are a technical project manager. Given a development plan and a team of AI coding agents, decompose the plan into atomic tasks and assign each task to the most suitable agent.

Return a JSON array of tasks. Each task must have:
- id: string (task-1, task-2, ...)
- description: string
- files: array of file paths or directory patterns this task owns exclusively
- complexity: "low" | "medium" | "high"
- agent: agent name from the roster
- reason: brief explanation of the assignment

Rules:
- Tasks must not share file ownership (no two tasks may list the same file)
- A task's complexity must not exceed the agent's max_complexity (unless max_complexity is "any")
- Assign by domain fit first, confidence as tiebreaker
- Be specific about file paths; use directory patterns (e.g. "src/auth/") for broad ownership

Return ONLY the JSON array, no markdown fences, no explanation."""


def decompose_plan(plan_text: str, roster: list[Agent]) -> list[Task]:
    roster_json = json.dumps(
        [
            {
                "name": a.name,
                "archetype": a.archetype,
                "confidence": a.confidence,
                "domains": a.domains,
                "max_complexity": a.max_complexity,
            }
            for a in roster
        ],
        indent=2,
    )

    user_msg = f"## Agent Roster\n\n{roster_json}\n\n## Development Plan\n\n{plan_text}"
    raw = call_llm(_SYSTEM, user_msg).strip()

    # Strip markdown code fences if present
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1]
        raw = raw.rsplit("```", 1)[0].strip()

    tasks_data = json.loads(raw)
    return [Task(**t) for t in tasks_data]
