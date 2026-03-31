import json
from pathlib import Path

import questionary
import typer
from rich.console import Console
from rich.panel import Panel
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
from .decompose import decompose_plan, suggest_roster
from .models import ROLE_DEFAULTS, Agent, SplitPlan, Task
from .prompts import write_prompts
from .llm import test_api_key
from .review import generate_review
from .run import RunError, Monitor, prepare_run

app = typer.Typer(
    no_args_is_help=True,
    add_completion=False,
)
console = Console()


@app.command(hidden=True)
def help(ctx: typer.Context) -> None:
    """Show help."""
    typer.echo(ctx.parent.get_help() if ctx.parent else ctx.get_help())
    raise typer.Exit()


@app.command()
def auth(
    test: bool = typer.Option(False, "--test", "-t", help="Test saved API key"),
) -> None:
    """Manage Z.AI API key."""
    if test:
        return _auth_test()

    existing = load_api_key()
    if existing:
        masked = existing[:6] + "…" + existing[-4:]
        console.print(f"[dim]Current key: {masked}[/dim]")

    key = questionary.text("Z.AI API key").ask()
    if not key or not key.strip():
        console.print("[red]Aborted — no key entered.[/red]")
        raise typer.Exit(1)

    key = key.strip()
    save_api_key(key)

    with console.status("[bold]Testing API key...[/bold]"):
        result = test_api_key(key)

    if result["ok"]:
        console.print(
            f"[green]✓ Key saved to {_AUTH_FILE} (chmod 600) — API test passed[/green]"
        )
    else:
        console.print(f"[green]✓ Key saved to {_AUTH_FILE} (chmod 600)[/green]")
        console.print(f"[red]✗ API test failed: {result['error']}[/red]")
        console.print("[dim]Run 'roster auth --test' to retry.[/dim]")


def _auth_test() -> None:
    """Test saved API key against the Z.AI coding endpoint."""
    key = load_api_key()
    if not key:
        console.print("[red]No API key found. Run 'roster auth' to save one.[/red]")
        raise typer.Exit(1)

    masked = key[:6] + "…" + key[-4:]
    console.print(f"[dim]Key: {masked}[/dim]\n")

    with console.status("[bold]Testing API connection...[/bold]"):
        result = test_api_key()

    table = Table(show_header=False, padding=(0, 2))
    table.add_column("Field", style="bold")
    table.add_column("Value")
    table.add_row("Endpoint", result["endpoint"])
    table.add_row("Model", result["model"])
    table.add_row("Prompt", result["prompt"])

    if result["ok"]:
        reply = result.get("response", "")
        usage = result.get("usage", {})
        table.add_row("Response", f"[green]{reply}[/green]")
        if usage:
            parts = []
            if "prompt_tokens" in usage:
                parts.append(f"in={usage['prompt_tokens']}")
            if "completion_tokens" in usage:
                parts.append(f"out={usage['completion_tokens']}")
            if "total_tokens" in usage:
                parts.append(f"total={usage['total_tokens']}")
            if parts:
                table.add_row("Tokens", "  ".join(parts))
        console.print(table)
        console.print("\n[green]✓ API connection successful[/green]")
    else:
        table.add_row("Error", f"[red]{result['error']}[/red]")
        console.print(table)
        console.print("\n[red]✗ API test failed[/red]")
        console.print("[dim]Run 'roster auth' to update your key.[/dim]")
        raise typer.Exit(1)


@app.command()
def run(
    plan_path: Path = typer.Argument(
        None,
        help="Path to plan file or directory",
    ),
    repo: Path = typer.Option(Path("."), "--repo", "-r", help="Target repo path"),
) -> None:
    """Set up agents, split plan, generate prompts, and start monitoring."""
    if not plan_path:
        plan_path = Path(questionary.text("Path to plan file or directory").ask())

    _do_run(plan_path, repo)


def _do_run(plan_path: Path, repo: Path) -> None:
    """Execute the run flow: roster check, split, prompts, monitor."""
    # --- Step 1: Roster ---
    roster = load_roster(repo)
    if roster:
        names = ", ".join(f"{a.name}" for a in roster)
        reuse = questionary.select(
            f"Found roster ({names}). Reuse?",
            choices=["Yes", "No"],
            default="Yes",
        ).ask()
        if reuse == "No":
            roster = _suggest_and_confirm_roster(plan_path, repo)
    else:
        roster = _suggest_and_confirm_roster(plan_path, repo)

    if not roster:
        console.print("[red]No roster configured. Aborting.[/red]")
        raise typer.Exit(1)

    # --- Step 2: Split + Prompts ---
    console.print()
    try:
        with console.status("[bold]Decomposing plan...[/bold]"):
            result = prepare_run(repo, plan_path)
    except RunError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)

    plan = result["plan"]
    _print_task_table(plan.tasks)
    console.print()

    # --- Print prompts inline ---
    for agent_name, prompt in result["prompts"].items():
        console.print(
            Panel(
                prompt,
                title=f"[bold cyan]{agent_name}[/]",
                border_style="cyan",
                padding=(1, 2),
            )
        )
        console.print()

    console.print(f"[green]✓ Prompts also saved to {result['prompts_dir']}/[/green]")
    console.print(f"[green]✓ COORDINATION.md at {result['coordination_path']}[/green]")

    # --- Step 3: Monitor ---
    start = questionary.select(
        "Start monitoring?",
        choices=["Yes", "Skip"],
        default="Yes",
    ).ask()
    if start == "Skip":
        console.print(
            "[dim]Done. Run 'roster run' or 'roster review' when ready.[/dim]"
        )
        return

    console.print(
        "\n[dim]Agents are working in parallel. Track progress below.[/dim]\n"
    )
    Monitor(repo, plan).start()


def _suggest_and_confirm_roster(plan_path: Path, repo: Path) -> list[Agent]:
    """Ask agent count + tiers, then LLM suggests names/roles/domains."""
    if plan_path.is_dir():
        plan_text = "\n\n---\n\n".join(
            f.read_text() for f in sorted(plan_path.glob("**/*.md"))
        )
    else:
        plan_text = plan_path.read_text()

    # Ask user for agent count and tiers
    console.print(
        "[dim]Tiers: low (docs, config), medium (standard), high (complex)[/dim]"
    )
    n = questionary.text("How many agents?", default="2").ask()
    if not n:
        n = "2"
    n = int(n)

    _TIER_OPTIONS = [
        questionary.Choice("low  — docs, config, simple refactors", value="low"),
        questionary.Choice("medium  — standard implementation", value="medium"),
        questionary.Choice("high  — complex architecture, hard problems", value="high"),
    ]
    agent_specs = []
    for i in range(1, n + 1):
        tier = questionary.select(
            f"Agent {i} tier",
            choices=_TIER_OPTIONS,
            default="medium",
        ).ask()
        agent_specs.append({"tier": tier})

    # Ask LLM to name them and pick roles/domains
    console.print("\n[bold]Analyzing plan to name agents and assign roles...[/bold]")
    with console.status("[bold]Generating agent roster...[/bold]"):
        try:
            roster = suggest_roster(plan_text, agent_specs)
        except Exception as e:
            console.print(f"[red]Failed to suggest roster: {e}[/red]")
            console.print("[dim]Falling back to manual setup.[/dim]")
            init(repo=repo)
            return load_roster(repo)

    table = Table(title="Suggested Agent Roster")
    table.add_column("Name", style="cyan")
    table.add_column("Role")
    table.add_column("Tier")
    table.add_column("Domains")
    for a in roster:
        table.add_row(
            a.name,
            a.role or "—",
            a.tier,
            ", ".join(a.domains),
        )
    console.print(table)

    confirm = questionary.select(
        "Accept this roster?",
        choices=["Accept", "Edit manually", "Cancel"],
        default="Accept",
    ).ask()
    if confirm == "Cancel":
        console.print(
            "[dim]Run 'roster init' to set up manually, then 'roster run' again.[/dim]"
        )
        return []
    if confirm == "Edit manually":
        init(repo=repo)
        return load_roster(repo)

    save_roster(roster, repo)
    console.print(
        f"[green]✓ Roster saved to {get_roster_dir(repo) / 'roster.json'}[/green]"
    )
    return roster


def _print_task_table(tasks: list[Task]) -> None:
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


@app.command()
def init(
    repo: Path = typer.Option(Path("."), "--repo", "-r", help="Target repo path"),
) -> None:
    """Set up the agent roster interactively."""
    console.print("[bold]Roster Init[/bold] — configure your agent roster\n")

    n_raw = questionary.text("How many agents?", default="2").ask()
    n = int(n_raw) if n_raw else 2
    agents: list[Agent] = []

    _TIER_OPTIONS = [
        questionary.Choice("low  — docs, config, simple refactors", value="low"),
        questionary.Choice("medium  — standard implementation", value="medium"),
        questionary.Choice("high  — complex architecture, hard problems", value="high"),
    ]

    _ROLE_OPTIONS = [
        questionary.Choice("builder  — code quality, implementation", value="builder"),
        questionary.Choice("architect  — system design, structure", value="architect"),
        questionary.Choice("explorer  — new features, prototyping", value="explorer"),
        questionary.Choice("reviewer  — docs, simple fixes", value="reviewer"),
        questionary.Choice("skip", value=None),
    ]

    for i in range(1, n + 1):
        console.print(f"\n[bold cyan]Agent {i}[/bold cyan]")
        name = questionary.text("  Name").ask() or ""

        role = questionary.select(
            "  Role",
            choices=_ROLE_OPTIONS,
            default="builder",
        ).ask()

        default_domains: list[str] = []
        if role and role in ROLE_DEFAULTS:
            default_domains = ROLE_DEFAULTS[role]["domains"]
            console.print(
                f"  [dim]→ domains pre-filled: {', '.join(default_domains)}[/dim]"
            )

        tier = questionary.select(
            "  Tier",
            choices=_TIER_OPTIONS,
            default="medium",
        ).ask()

        override = (
            questionary.text(
                "  Override domains? (comma-separated, leave blank to keep pre-filled)",
                default="",
            ).ask()
            or ""
        )
        if override:
            domains = [d.strip() for d in override.split(",")]
        elif default_domains:
            domains = default_domains
        else:
            raw = questionary.text("  Domains (comma-separated)").ask() or ""
            domains = [d.strip() for d in raw.split(",") if d.strip()]

        agents.append(
            Agent(
                name=name,
                tier=tier,  # type: ignore[arg-type]
                role=role,
                domains=domains,
            )
        )

    save_roster(agents, repo)

    table = Table(title="Agent Roster")
    table.add_column("Name")
    table.add_column("Role")
    table.add_column("Tier")
    table.add_column("Domains")
    for a in agents:
        table.add_row(
            a.name,
            a.role or "—",
            a.tier,
            ", ".join(a.domains),
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
        delegation_strategy="tier-based",
        tasks=tasks,
    )

    _print_task_table(tasks)

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
