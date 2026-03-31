import json

from .llm import APIError, call_llm
from .models import Agent, Assignment

_ASSIGN_SYSTEM = """You are a technical project manager. Given a development plan and an agent roster, assign work to agents and produce a file ownership map.

CRITICAL: Each agent must be fully self-contained. Agents run in parallel and cannot communicate or depend on each other's output.
- If work item B requires the output of work item A, assign BOTH to the same agent.
- Never split sequential work across agents.
- Never design an assignment that requires one agent to "hand off" to another.

If the plan contains structured work packages (WPs, sections, phases, numbered items), assign those as whole units. Do not break them into smaller pieces. Group related or sequential WPs onto the same agent.

If the plan is unstructured, decompose it into logical work units first, then assign.

For each agent, list:
- work: array of work descriptions (WP names or logical units)
- files: array of file paths this agent exclusively owns

Rules:
- No two agents may own the same file
- Budget agents may only own documentation, config, test scaffolding, or example files
- Every file that will be touched must be assigned
- Coupled or sequential work MUST go to the same agent

Return a JSON array. Each entry: {agent, work[], files[]}.
No markdown fences, no explanation."""


class DecomposeError(Exception):
    """Raised when decomposition fails with a user-friendly message."""

    def __init__(self, message: str, hint: str = ""):
        self.message = message
        self.hint = hint
        super().__init__(message)


def suggest_roster(plan_text: str, high_count: int) -> list[Agent]:
    """Ask the LLM to propose agents based on the plan content and high-tier budget."""
    system = f"""You are a technical project manager. Given a development plan and a premium agent budget, propose the optimal team.

Rules:
- Use UP TO {high_count} premium agents (may use fewer if the plan doesn't warrant it)
- Budget agents are unlimited; add as many as useful
- Premium agents handle ALL implementation work
- Budget agents handle ONLY: documentation, config, test scaffolding, examples, CI/CD
- Budget agents must NEVER build features or modify production code
- Choose short one-word lowercase names (e.g. "core", "ui", "docs")
- Agents should have non-overlapping domains
- Tier values must be exactly "premium" or "budget"

Return a JSON array. Each agent: {{name, tier, domains[]}}.
No markdown fences, no explanation."""

    user_msg = (
        f"## Premium Agent Budget: up to {high_count}\n\n"
        f"## Development Plan\n\n{plan_text}"
    )
    raw = call_llm(system, user_msg).strip()
    return _parse_agents(raw)


def assign_work(plan_text: str, roster: list[Agent]) -> list[Assignment]:
    """Ask the LLM to assign work packages to agents and produce file ownership."""
    roster_json = json.dumps(
        [{"name": a.name, "tier": a.tier, "domains": a.domains} for a in roster],
        indent=2,
    )
    user_msg = f"## Agent Roster\n\n{roster_json}\n\n## Development Plan\n\n{plan_text}"
    raw = call_llm(_ASSIGN_SYSTEM, user_msg).strip()
    return _parse_assignments(raw)


def _parse_agents(raw: str) -> list[Agent]:
    """Parse JSON array of agents from LLM output."""
    raw = _strip_fences(raw)
    try:
        agents_data = json.loads(raw)
    except json.JSONDecodeError:
        raise DecomposeError(
            "Failed to parse agent suggestions from the API",
            hint="The model returned invalid JSON. Try again.",
        )
    if not isinstance(agents_data, list):
        raise DecomposeError(
            "Expected a list of agents from the API",
            hint="The model returned an unexpected format. Try again.",
        )
    try:
        return [Agent(**a) for a in agents_data]
    except TypeError as e:
        raise DecomposeError(
            f"Invalid agent format: {e}",
            hint="The model returned agents with missing or wrong fields. Try again.",
        )


def _parse_assignments(raw: str) -> list[Assignment]:
    """Parse JSON array of assignments from LLM output."""
    raw = _strip_fences(raw)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        raise DecomposeError(
            "Failed to parse work assignment from the API",
            hint="The model returned invalid JSON. Try again.",
        )
    if not isinstance(data, list):
        raise DecomposeError(
            "Expected a list of assignments from the API",
            hint="The model returned an unexpected format. Try again.",
        )
    try:
        return [Assignment(**a) for a in data]
    except TypeError as e:
        raise DecomposeError(
            f"Invalid assignment format: {e}",
            hint="The model returned assignments with missing or wrong fields. Try again.",
        )


def _strip_fences(raw: str) -> str:
    """Strip markdown code fences from LLM output."""
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1]
        raw = raw.rsplit("```", 1)[0].strip()
    return raw
