from pathlib import Path

from .models import ROLE_DEFAULTS, Agent, SplitPlan, Task


def generate_coordination_md(plan: SplitPlan, roster: list[Agent]) -> str:
    lines = [
        "# COORDINATION.md",
        "",
        "> **Read this before doing anything.**  ",
        "> This file describes the full task split and file ownership for this parallel development run.",
        "",
        "## Task Overview",
        "",
    ]

    for task in plan.tasks:
        files_str = ", ".join(f"`{f}`" for f in task.files)
        lines += [
            f"### {task.id}: {task.description}",
            f"- **Owner**: `{task.agent}`",
            f"- **Complexity**: {task.complexity}",
            f"- **Files**: {files_str}",
            f"- **Reason**: {task.reason}",
            "",
        ]

    lines += [
        "## File Ownership Map",
        "",
        "| File / Pattern | Owner |",
        "|----------------|-------|",
    ]
    for task in plan.tasks:
        for f in task.files:
            lines.append(f"| `{f}` | `{task.agent}` |")

    lines += [
        "",
        "## Commit Convention",
        "",
        "Prefix every commit with your agent name in square brackets:",
        "```",
        "[agent-name] short description of change",
        "```",
        "Commit after each logical change, not just at the end.",
        "",
        "## Agent Roster",
        "",
    ]
    for a in roster:
        role_str = f" ({a.role})" if a.role else ""
        lines.append(
            f"- **{a.name}**{role_str} — tier: {a.tier}, "
            f"domains: {', '.join(a.domains)}"
        )

    return "\n".join(lines) + "\n"


def generate_agent_prompt(
    agent: Agent,
    tasks: list[Task],
    plan_context: str,
    coordination_path: str,
) -> str:
    lines: list[str] = []

    if agent.role and agent.role in ROLE_DEFAULTS:
        persona = ROLE_DEFAULTS[agent.role]["persona"]
        lines += ["# Role", "", persona, ""]

    lines += [
        "# Your Assignment",
        "",
        f"You are **{agent.name}**. You have been assigned the following tasks in this parallel development run.",
        "",
        f"**First**, read `{coordination_path}` to understand the full picture — what every agent is doing and which files belong to whom.",
        "",
        "## Tasks",
        "",
    ]

    for task in tasks:
        lines += [
            f"### {task.id}: {task.description}",
            "",
            "**Files you own** (do NOT touch any other files):",
        ]
        for f in task.files:
            lines.append(f"- `{f}`")
        lines += ["", f"**Complexity**: {task.complexity}", ""]

    lines += [
        "## Context from the Plan",
        "",
        plan_context,
        "",
        "## Rules",
        "",
        "1. **Stay in your lane.** Only modify the files listed above.",
        f"2. **Commit convention.** Prefix every commit with `[{agent.name}]`, e.g. `[{agent.name}] implement X`.",
        "3. **Commit often.** Commit after each logical change.",
        "4. **Read COORDINATION.md first** to understand the full scope and avoid conflicts.",
        "",
        f"COORDINATION.md path: `{coordination_path}`",
    ]

    return "\n".join(lines) + "\n"


def write_prompts(
    plan: SplitPlan,
    roster: list[Agent],
    plan_text: str,
    repo_path: Path,
) -> None:
    ros_dir = repo_path / ".roster"
    prompts_dir = ros_dir / "prompts"
    prompts_dir.mkdir(parents=True, exist_ok=True)

    (ros_dir / "COORDINATION.md").write_text(generate_coordination_md(plan, roster))

    agent_map = {a.name: a for a in roster}
    tasks_by_agent: dict[str, list[Task]] = {}
    for task in plan.tasks:
        tasks_by_agent.setdefault(task.agent, []).append(task)

    for agent_name, agent_tasks in tasks_by_agent.items():
        agent = agent_map[agent_name]
        prompt = generate_agent_prompt(
            agent=agent,
            tasks=agent_tasks,
            plan_context=plan_text,
            coordination_path=".roster/COORDINATION.md",
        )
        (prompts_dir / f"{agent_name}.md").write_text(prompt)
