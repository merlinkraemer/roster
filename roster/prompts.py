from pathlib import Path

from .models import Agent, Assignment, SplitPlan


def generate_coordination_md(plan: SplitPlan, roster: list[Agent]) -> str:
    lines = [
        "# COORDINATION.md",
        "",
        "> **Read this before doing anything.**  ",
        "> This file describes the full work assignment and file ownership for this parallel development run.",
        "",
        "## File ownership",
        "",
    ]

    assignment_map = {a.agent: a for a in plan.assignments}
    for agent in roster:
        assignment = assignment_map.get(agent.name)
        if not assignment:
            continue
        lines.append(f"{agent.name}:")
        for f in assignment.files:
            lines.append(f"  - {f}")
        lines.append("")

    lines += [
        "## Work scope",
        "",
    ]
    for agent in roster:
        assignment = assignment_map.get(agent.name)
        if not assignment:
            continue
        work_str = "; ".join(assignment.work)
        lines.append(f"- **{agent.name}**: {work_str}")
    lines.append("")

    lines += [
        "## Dependencies",
        "",
        "If you need something from another agent's domain:",
        "- Leave a TODO comment in your code describing what you need",
        "- Do not block on it — use a stub, mock, or placeholder",
        "- Do not modify their files",
        "",
        "## Commit convention",
        "",
        "Prefix every commit with your agent name in square brackets:",
        "```",
        "[agent-name] short description of change",
        "```",
        "Commit after each logical change, not just at the end.",
    ]

    return "\n".join(lines) + "\n"


def _generate_high_tier_prompt(
    agent: Agent,
    assignment: Assignment,
    plan_source: str,
    other_assignments: list[Assignment],
) -> str:
    lines = [
        "# Assignment",
        "",
        f"You are **{agent.name}**. You own the following work in this parallel agent run:",
        "",
    ]
    for w in assignment.work:
        lines.append(f"- {w}")
    lines += [
        "",
        "## Files you own",
        "",
        "You may ONLY modify these files:",
    ]
    for f in assignment.files:
        lines.append(f"- `{f}`")
    lines += [
        "",
        "## References",
        "",
        f"- Plan: `{plan_source}`",
        "- Coordination: `.roster/COORDINATION.md`",
        "",
        "Read these before starting. The plan is the source of truth for what to build.",
        "",
        "## Parallel context",
        "",
        "Other agents are working at the same time:",
    ]
    for other in other_assignments:
        files_str = ", ".join(f"`{f}`" for f in other.files)
        lines.append(f"- **{other.agent}** owns {files_str}")
    lines += [
        "",
        "Do not touch their files. If you need something from their domain, leave a TODO comment and move on.",
        "",
        "## Rules",
        "",
        "1. Only modify your files.",
        f"2. Prefix commits with `[{agent.name}]`.",
        "3. Commit after each logical change.",
        "4. Read COORDINATION.md first.",
    ]
    return "\n".join(lines) + "\n"


def _generate_low_tier_prompt(
    agent: Agent,
    assignment: Assignment,
    plan_source: str,
    other_assignments: list[Assignment],
) -> str:
    lines = [
        "# Assignment",
        "",
        f"You are **{agent.name}**. You have the following task in this parallel run.",
        "",
        "## Task",
        "",
    ]
    for w in assignment.work:
        lines.append(f"- {w}")
    lines += [
        "",
        "## Output files",
        "",
        "Write to:",
    ]
    for f in assignment.files:
        lines.append(f"- `{f}`")
    lines += [
        "",
        "Do NOT touch any other files.",
        "",
        "## References",
        "",
        f"- Plan: `{plan_source}`",
        "- Coordination: `.roster/COORDINATION.md`",
        "",
        "## Parallel context",
        "",
        "Other agents are working at the same time:",
    ]
    for other in other_assignments:
        files_str = ", ".join(f"`{f}`" for f in other.files)
        lines.append(f"- **{other.agent}** owns {files_str}")
    lines += [
        "",
        "## Rules",
        "",
        "1. Only modify your files.",
        f"2. Prefix commits with `[{agent.name}]`.",
        "3. Commit after each logical change.",
        "4. Read COORDINATION.md first.",
    ]
    return "\n".join(lines) + "\n"


def generate_agent_prompt(
    agent: Agent,
    assignment: Assignment,
    plan_source: str,
    other_assignments: list[Assignment],
) -> str:
    if agent.tier == "premium":
        return _generate_high_tier_prompt(agent, assignment, plan_source, other_assignments)
    else:
        return _generate_low_tier_prompt(agent, assignment, plan_source, other_assignments)


def write_prompts(
    plan: SplitPlan,
    roster: list[Agent],
    repo_path: Path,
) -> dict[str, str]:
    """Generate and save COORDINATION.md + per-agent prompts. Returns {agent_name: prompt_text}."""
    ros_dir = repo_path / ".roster"
    prompts_dir = ros_dir / "prompts"
    prompts_dir.mkdir(parents=True, exist_ok=True)

    (ros_dir / "COORDINATION.md").write_text(generate_coordination_md(plan, roster))

    agent_map = {a.name: a for a in roster}
    result: dict[str, str] = {}

    for assignment in plan.assignments:
        agent = agent_map.get(assignment.agent)
        if not agent:
            continue
        other = [a for a in plan.assignments if a.agent != assignment.agent]
        prompt = generate_agent_prompt(
            agent=agent,
            assignment=assignment,
            plan_source=plan.source,
            other_assignments=other,
        )
        (prompts_dir / f"{assignment.agent}.md").write_text(prompt)
        result[assignment.agent] = prompt

    return result
