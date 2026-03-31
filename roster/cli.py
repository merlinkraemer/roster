import json
from pathlib import Path

import typer
from rich.console import Console
from rich.prompt import IntPrompt, Prompt
from rich.table import Table

from .assign import validate_assignments
from .config import (
    _AUTH_FILE,
    get_roster_dir,
    load_api_key,
    load_roster,
    save_api_key,
    save_roster,
)
from .decompose import decompose_plan
from .models import ARCHETYPE_DEFAULTS, Agent, SplitPlan, Task
from .prompts import write_prompts
from .review import generate_review

app = typer.Typer(help="Manage parallel AI agent development runs.")
console = Console()


@app.command()
def auth() -> None:
    """Save your Z.AI API key (stored at ~/.config/roster/auth.json)."""
    existing = load_api_key()
    if existing:
        masked = existing[:6] + "…" + existing[-4:]
        console.print(f"[dim]Current key: {masked}[/dim]")

    key = Prompt.ask("Z.AI API key", password=True)
    if not key.strip():
        console.print("[red]Aborted — no key entered.[/red]")
        raise typer.Exit(1)

    save_api_key(key.strip())
    console.print(f"[green]✓ Key saved to {_AUTH_FILE} (chmod 600)[/green]")


@app.command()
def init(
    repo: Path = typer.Option(Path("."), "--repo", "-r", help="Target repo path"),
) -> None:
    """Set up the agent roster interactively."""
    console.print("[bold]Roster Init[/bold] — configure your agent roster\n")

    n = IntPrompt.ask("How many agents?", default=2)
    agents: list[Agent] = []
    archetype_choices = "craftsman/architect/explorer/reviewer"

    for i in range(1, n + 1):
        console.print(f"\n[bold cyan]Agent {i}[/bold cyan]")
        name = Prompt.ask("  Name")

        archetype_raw = Prompt.ask(
            f"  Archetype (optional — {archetype_choices}, or skip)", default=""
        ).strip()
        archetype = archetype_raw or None

        default_domains: list[str] = []
        if archetype and archetype in ARCHETYPE_DEFAULTS:
            default_domains = ARCHETYPE_DEFAULTS[archetype]["domains"]
            console.print(
                f"  [dim]→ domains pre-filled: {', '.join(default_domains)}[/dim]"
            )

        confidence = IntPrompt.ask("  Confidence (0-100)", default=80)

        override = Prompt.ask(
            "  Override domains? (comma-separated, leave blank to keep pre-filled)",
            default="",
        ).strip()
        if override:
            domains = [d.strip() for d in override.split(",")]
        elif default_domains:
            domains = default_domains
        else:
            raw = Prompt.ask("  Domains (comma-separated)")
            domains = [d.strip() for d in raw.split(",")]

        max_complexity = Prompt.ask(
            "  Max complexity",
            default="any",
            choices=["low", "medium", "high", "any"],
            show_choices=True,
        )

        agents.append(
            Agent(
                name=name,
                archetype=archetype,
                confidence=confidence,
                domains=domains,
                max_complexity=max_complexity,  # type: ignore[arg-type]
            )
        )

    save_roster(agents, repo)

    table = Table(title="Agent Roster")
    table.add_column("Name")
    table.add_column("Archetype")
    table.add_column("Confidence")
    table.add_column("Domains")
    table.add_column("Max Complexity")
    for a in agents:
        table.add_row(
            a.name,
            a.archetype or "—",
            str(a.confidence),
            ", ".join(a.domains),
            a.max_complexity,
        )
    console.print(table)
    console.print(
        f"\n[green]✓ Roster saved to {get_roster_dir(repo) / 'roster.json'}[/green]"
    )
    console.print("[dim]Tip: add .roster/ to your .gitignore[/dim]")


@app.command()
def split(
    plan_path: Path = typer.Argument(..., help="Path to plan doc or directory of docs"),
    repo: Path = typer.Option(Path("."), "--repo", "-r", help="Target repo path"),
) -> None:
    """Decompose a plan and assign tasks to agents."""
    roster = load_roster(repo)
    if not roster:
        console.print("[red]No roster found. Run `roster init` first.[/red]")
        raise typer.Exit(1)

    if plan_path.is_dir():
        plan_text = "\n\n---\n\n".join(
            f.read_text() for f in sorted(plan_path.glob("**/*.md"))
        )
    else:
        plan_text = plan_path.read_text()

    with console.status("[bold]Decomposing plan with LLM...[/bold]"):
        tasks = decompose_plan(plan_text, roster)

    violations = validate_assignments(tasks, roster)
    if violations:
        console.print("[yellow]⚠ Assignment warnings:[/yellow]")
        for v in violations:
            console.print(f"  {v}")

    plan = SplitPlan(
        source=str(plan_path),
        delegation_strategy="expertise_based",
        tasks=tasks,
    )

    table = Table(title="Task Split")
    table.add_column("ID", style="dim")
    table.add_column("Description")
    table.add_column("Agent", style="cyan")
    table.add_column("Complexity")
    table.add_column("Files", style="dim")
    for t in tasks:
        desc = t.description[:60] + "…" if len(t.description) > 60 else t.description
        files_preview = "\n".join(t.files[:3])
        if len(t.files) > 3:
            files_preview += f"\n+{len(t.files) - 3} more"
        table.add_row(t.id, desc, t.agent, t.complexity, files_preview)
    console.print(table)

    ros_dir = get_roster_dir(repo)
    ros_dir.mkdir(parents=True, exist_ok=True)
    out_path = ros_dir / "split-plan.json"
    plan_data = {
        "source": plan.source,
        "delegation_strategy": plan.delegation_strategy,
        "tasks": [
            {
                "id": t.id,
                "description": t.description,
                "files": t.files,
                "complexity": t.complexity,
                "agent": t.agent,
                "reason": t.reason,
            }
            for t in plan.tasks
        ],
    }
    out_path.write_text(json.dumps(plan_data, indent=2))
    console.print(f"\n[green]✓ Split plan saved to {out_path}[/green]")
    console.print(
        "[dim]Review/edit the JSON if needed, then run `roster prompts`.[/dim]"
    )


@app.command()
def prompts(
    repo: Path = typer.Option(Path("."), "--repo", "-r", help="Target repo path"),
) -> None:
    """Generate COORDINATION.md and per-agent prompt files."""
    ros_dir = get_roster_dir(repo)

    roster = load_roster(repo)
    if not roster:
        console.print("[red]No roster found. Run `roster init` first.[/red]")
        raise typer.Exit(1)

    plan_path = ros_dir / "split-plan.json"
    if not plan_path.exists():
        console.print("[red]No split plan found. Run `roster split` first.[/red]")
        raise typer.Exit(1)

    plan_data = json.loads(plan_path.read_text())
    tasks = [Task(**t) for t in plan_data["tasks"]]
    plan = SplitPlan(
        source=plan_data["source"],
        delegation_strategy=plan_data["delegation_strategy"],
        tasks=tasks,
    )

    try:
        plan_text = Path(plan.source).read_text()
    except Exception:
        plan_text = "(original plan file not found)"

    with console.status("[bold]Generating prompts...[/bold]"):
        write_prompts(plan, roster, plan_text, repo)

    console.print(f"[green]✓ {ros_dir / 'COORDINATION.md'}[/green]")
    for f in sorted((ros_dir / "prompts").glob("*.md")):
        console.print(f"[green]✓ {f}[/green]")

    console.print(
        "\n[dim]Paste each agent's prompt file into the corresponding AI tool to start the run.[/dim]"
    )


@app.command()
def review(
    repo: Path = typer.Option(Path("."), "--repo", "-r", help="Target repo path"),
) -> None:
    """Review the run using git log and optional agent output files."""
    ros_dir = get_roster_dir(repo)

    plan_path = ros_dir / "split-plan.json"
    if not plan_path.exists():
        console.print("[red]No split plan found. Run `roster split` first.[/red]")
        raise typer.Exit(1)

    plan_data = json.loads(plan_path.read_text())
    tasks = [Task(**t) for t in plan_data["tasks"]]
    plan = SplitPlan(
        source=plan_data["source"],
        delegation_strategy=plan_data["delegation_strategy"],
        tasks=tasks,
    )

    with console.status("[bold]Generating review...[/bold]"):
        review_content = generate_review(plan, repo, ros_dir / "outputs")

    review_out = ros_dir / "review.md"
    review_out.write_text(review_content)
    console.print(f"[green]✓ Review written to {review_out}[/green]\n")
    console.print(review_content)
