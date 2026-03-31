from .models import Agent, Task

_TIER_ORDER = {"low": 0, "medium": 1, "high": 2}


def hard_gate_filter(agents: list[Agent], complexity: str) -> list[Agent]:
    """Return agents whose tier can handle the given task complexity."""
    task_level = _TIER_ORDER[complexity]
    return [a for a in agents if _TIER_ORDER[a.tier] >= task_level]


def validate_assignments(tasks: list[Task], roster: list[Agent]) -> list[str]:
    """Return a list of violation messages (empty = clean)."""
    violations: list[str] = []
    agent_map = {a.name: a for a in roster}
    file_owners: dict[str, str] = {}

    for task in tasks:
        agent = agent_map.get(task.agent)
        if not agent:
            violations.append(f"{task.id}: assigned to unknown agent '{task.agent}'")
            continue

        eligible = hard_gate_filter(roster, task.complexity)
        if agent not in eligible:
            violations.append(
                f"{task.id}: '{task.agent}' (tier={agent.tier}) "
                f"cannot handle complexity '{task.complexity}'"
            )

        for f in task.files:
            if f in file_owners:
                violations.append(
                    f"{task.id}: file '{f}' already owned by '{file_owners[f]}'"
                )
            else:
                file_owners[f] = task.agent

    return violations
