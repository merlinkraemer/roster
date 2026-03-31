from .models import Agent, Assignment


def validate_assignments(assignments: list[Assignment], roster: list[Agent]) -> list[str]:
    """Return a list of violation messages (empty = clean)."""
    violations: list[str] = []
    agent_names = {a.name for a in roster}
    file_owners: dict[str, str] = {}

    for assignment in assignments:
        if assignment.agent not in agent_names:
            violations.append(f"Assignment for unknown agent '{assignment.agent}'")
            continue

        for f in assignment.files:
            if f in file_owners:
                violations.append(
                    f"File '{f}' claimed by both '{file_owners[f]}' and '{assignment.agent}'"
                )
            else:
                file_owners[f] = assignment.agent

    return violations
