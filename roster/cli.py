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
from .decompose import DecomposeError, assign_work, suggest_roster
from .llm import APIError, test_api_key
from .models import Agent, Assignment, SplitPlan
from .prompts import write_prompts
from .review import generate_review
from .run import RunError, Monitor, prepare_run

app = typer.Typer(
    no_args_is_help=True,
    add_completion=False,
)
console = Console()


def _format_error(e: BaseException) -> None:
    """Print a clean error message without traceback."""
    if isinstance(e, (APIError, DecomposeError)):
        console.print(f"[red]✗ {e.message}[/red]")
        if e.hint:
            console.print(f"[dim]{e.hint}[/dim]")
    elif isinstance(e, RunError):
        console.print(f"[red]✗ {e}[/red]")
    elif isinstance(e, (FileNotFoundError, PermissionError)):
        console.print(f"[red]✗ {e}[/red]")
    elif isinstance(e, KeyboardInterrupt):
        console.print("\n[dim]Cancelled.[/dim]")
    else:
        console.print(f"[red]✗ Unexpected error: {e}[/red]")


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

    with console.status("[bold]checking your key...[/bold]"):
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

    with console.status("[bold]testing the connection...[/bold]"):
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
    repo: Path = typer.Option(None, "--repo", "-r", help="Target repo path (defaults to git root)"),
) -> None:
    """Set up agents, assign work, generate prompts, and start monitoring."""
    if not plan_path:
        plan_path = Path(questionary.text("Path to plan file or directory").ask())

    plan_path = plan_path.resolve()
    repo = _resolve_repo(repo, plan_path)

    try:
        _do_run(plan_path, repo)
    except (
        APIError,
        DecomposeError,
        RunError,
        FileNotFoundError,
        PermissionError,
    ) as e:
        _format_error(e)
        raise typer.Exit(1)


def _do_run(plan_path: Path, repo: Path) -> None:
    """Execute the run flow: roster → assignment → prompts → monitor."""
    console.print(f"[dim]project root: {repo}[/dim]\n")
    plan_text = _read_plan_text(plan_path)

    # --- Step 1: Roster ---
    roster = _suggest_and_confirm_roster(plan_text, plan_path, repo)
    if not roster:
        return

    # --- Step 2: Work assignment ---
    console.print()
    assignments = _assign_and_confirm_work(plan_text, roster)
    if assignments is None:
        return

    # --- Step 3: Generate prompts ---
    console.print()
    with console.status("[bold]writing prompts...[/bold]"):
        result = prepare_run(repo, plan_path, roster, assignments)

    roster_map = {a.name: a for a in roster}
    for agent_name, prompt in result["prompts"].items():
        agent = roster_map.get(agent_name)
        tier_label = f" · {agent.tier}" if agent else ""
        console.print(
            Panel(
                prompt,
                title=f"[bold cyan]{agent_name}[/][dim]{tier_label}[/]",
                border_style="cyan",
                padding=(1, 2),
            )
        )
        console.print()

    console.print(f"[green]✓ Prompts saved to {result['prompts_dir']}/[/green]")
    console.print(f"[green]✓ COORDINATION.md at {result['coordination_path']}[/green]")
    console.print()
    console.print(
        "[dim]Copy each agent's prompt and paste it into a separate coding agent session "
        "(Claude Code, Cursor, etc.). Start all agents before hitting Enter below.[/dim]"
    )

    # --- Step 4: Monitor ---
    start = questionary.select(
        "Start monitoring?",
        choices=["Yes", "Skip"],
        default="Yes",
    ).ask()
    if start == "Skip":
        console.print("[dim]all good — run 'roster review' when the agents are done.[/dim]")
        return

    console.print("\n[dim]doing stuff... track progress below.[/dim]\n")
    Monitor(repo, result["plan"]).start()


def _find_git_root(start: Path) -> Path | None:
    """Walk up from start looking for a .git directory. Returns None if not found."""
    current = start if start.is_dir() else start.parent
    while True:
        if (current / ".git").exists():
            return current
        parent = current.parent
        if parent == current:
            return None
        current = parent


def _resolve_repo(repo: Path | None, plan_path: Path) -> Path:
    """Resolve the target repo path, preferring git root over cwd."""
    if repo is not None:
        return repo.resolve()
    git_root = _find_git_root(plan_path)
    if git_root:
        return git_root
    return Path.cwd()


def _read_plan_text(plan_path: Path) -> str:
    """Read plan text from file or directory."""
    if not plan_path.exists():
        console.print(f"[red]✗ File not found: {plan_path}[/red]")
        raise typer.Exit(1)

    if plan_path.is_dir():
        md_files = sorted(plan_path.glob("**/*.md"))
        if not md_files:
            console.print(f"[red]✗ No .md files found in {plan_path}[/red]")
            raise typer.Exit(1)
        return "\n\n---\n\n".join(f.read_text() for f in md_files)
    else:
        return plan_path.read_text()


def _suggest_and_confirm_roster(
    plan_text: str, plan_path: Path, repo: Path
) -> list[Agent]:
    """LLM call 1: propose team, show table, ask user to accept/edit/cancel."""
    high_raw = questionary.text("How many premium agents? (max)", default="2").ask()
    high_count = int(high_raw) if high_raw and high_raw.strip().isdigit() else 2

    console.print(f"\n[dim]Budget agents: unlimited — model picks how many[/dim]")

    with console.status("[bold]figuring out the team...[/bold]"):
        try:
            roster = suggest_roster(plan_text, high_count)
        except (APIError, DecomposeError) as e:
            _format_error(e)
            return []

    table = Table(title="Suggested Agent Roster", show_lines=True)
    table.add_column("Name", style="cyan")
    table.add_column("Tier")
    table.add_column("Domains")
    for a in roster:
        table.add_row(a.name, a.tier, ", ".join(a.domains))
    console.print(table)

    high_used = sum(1 for a in roster if a.tier == "premium")
    console.print(f"\n[dim]using {high_used} of {high_count} premium slots.[/dim]")

    confirm = questionary.select(
        "Accept this roster?",
        choices=["Accept", "Edit manually", "Cancel"],
        default="Accept",
    ).ask()
    if confirm == "Cancel":
        console.print("[dim]no worries — run 'roster init' to set up manually, then 'roster run' again.[/dim]")
        return []
    if confirm == "Edit manually":
        init(repo=repo)
        return load_roster(repo)

    save_roster(roster, repo)
    console.print(
        f"[green]✓ Roster saved to {get_roster_dir(repo) / 'roster.json'}[/green]"
    )
    return roster


def _assign_and_confirm_work(
    plan_text: str, roster: list[Agent]
) -> list[Assignment] | None:
    """LLM call 2: map plan to agents, show table, ask user to accept/cancel."""
    with console.status("[bold]dividing up the work...[/bold]"):
        try:
            assignments = assign_work(plan_text, roster)
        except (APIError, DecomposeError) as e:
            _format_error(e)
            return None

    violations = validate_assignments(assignments, roster)
    if violations:
        console.print("[yellow]⚠ Assignment warnings:[/yellow]")
        for v in violations:
            console.print(f"  {v}")

    _print_assignment_table(assignments, roster)

    confirm = questionary.select(
        "Accept this assignment?",
        choices=["Accept", "Cancel"],
        default="Accept",
    ).ask()
    if confirm == "Cancel":
        console.print("[dim]cancelled.[/dim]")
        return None

    return assignments


def _print_assignment_table(assignments: list[Assignment], roster: list[Agent]) -> None:
    tier_map = {a.name: a.tier for a in roster}

    table = Table(title="Work Assignment", show_lines=True)
    table.add_column("Agent", style="cyan")
    table.add_column("Work")
    table.add_column("Tier")
    table.add_column("Files owned", style="dim")

    for a in assignments:
        work_str = "\n".join(f"- {w}" for w in a.work)
        files_preview = "\n".join(a.files[:3])
        if len(a.files) > 3:
            files_preview += f"\n+{len(a.files) - 3} more"
        tier = tier_map.get(a.agent, "?")
        table.add_row(a.agent, work_str, tier, files_preview)

    console.print(table)


@app.command()
def init(
    repo: Path = typer.Option(None, "--repo", "-r", help="Target repo path (defaults to git root)"),
) -> None:
    """Set up the agent roster interactively."""
    repo = _resolve_repo(repo, Path.cwd())
    console.print("[bold]Roster Init[/bold] — configure your agent roster\n")

    _TIER_OPTIONS = [
        questionary.Choice("premium  — complex implementation, features, architecture", value="premium"),
        questionary.Choice("budget   — docs, config, test scaffolding, CI/CD", value="budget"),
    ]

    n_raw = questionary.text("How many agents?", default="2").ask()
    n = int(n_raw) if n_raw else 2
    agents: list[Agent] = []

    for i in range(1, n + 1):
        console.print(f"\n[bold cyan]Agent {i}[/bold cyan]")
        name = questionary.text("  Name").ask() or ""
        tier = questionary.select(
            "  Tier",
            choices=_TIER_OPTIONS,
            default="premium",
        ).ask()
        raw = questionary.text("  Domains (comma-separated)").ask() or ""
        domains = [d.strip() for d in raw.split(",") if d.strip()]
        agents.append(Agent(name=name, tier=tier, domains=domains))  # type: ignore[arg-type]

    save_roster(agents, repo)

    table = Table(title="Agent Roster", show_lines=True)
    table.add_column("Name")
    table.add_column("Tier")
    table.add_column("Domains")
    for a in agents:
        table.add_row(a.name, a.tier, ", ".join(a.domains))
    console.print(table)
    console.print(
        f"\n[green]✓ Roster saved to {get_roster_dir(repo) / 'roster.json'}[/green]"
    )
    console.print("[dim]Tip: add .roster/ to your .gitignore[/dim]")


@app.command()
def split(
    plan_path: Path = typer.Argument(..., help="Path to plan doc or directory of docs"),
    repo: Path = typer.Option(None, "--repo", "-r", help="Target repo path (defaults to git root)"),
) -> None:
    """Assign work packages to agents from a plan."""
    plan_path = plan_path.resolve()
    repo = _resolve_repo(repo, plan_path)
    try:
        _do_split(plan_path, repo)
    except (
        APIError,
        DecomposeError,
        RunError,
        FileNotFoundError,
        PermissionError,
    ) as e:
        _format_error(e)
        raise typer.Exit(1)


def _do_split(plan_path: Path, repo: Path) -> None:
    roster = load_roster(repo)
    if not roster:
        console.print("[red]No roster found. Run `roster init` first.[/red]")
        raise typer.Exit(1)

    plan_text = _read_plan_text(plan_path)

    with console.status("[bold]dividing up the work...[/bold]"):
        assignments = assign_work(plan_text, roster)

    violations = validate_assignments(assignments, roster)
    if violations:
        console.print("[yellow]⚠ Assignment warnings:[/yellow]")
        for v in violations:
            console.print(f"  {v}")

    _print_assignment_table(assignments, roster)

    ros_dir = get_roster_dir(repo)
    ros_dir.mkdir(parents=True, exist_ok=True)
    out_path = ros_dir / "split-plan.json"
    data = {
        "source": str(plan_path),
        "assignments": [
            {"agent": a.agent, "work": a.work, "files": a.files}
            for a in assignments
        ],
    }
    out_path.write_text(json.dumps(data, indent=2))
    console.print(f"\n[green]✓ Assignment saved to {out_path}[/green]")
    console.print("[dim]Review/edit the JSON if needed, then run `roster prompts`.[/dim]")


@app.command()
def prompts(
    repo: Path = typer.Option(None, "--repo", "-r", help="Target repo path (defaults to git root)"),
) -> None:
    """Generate COORDINATION.md and per-agent prompt files."""
    repo = _resolve_repo(repo, Path.cwd())
    try:
        _do_prompts(repo)
    except (
        APIError,
        DecomposeError,
        RunError,
        FileNotFoundError,
        PermissionError,
    ) as e:
        _format_error(e)
        raise typer.Exit(1)


def _do_prompts(repo: Path) -> None:
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
    assignments = [Assignment(**a) for a in plan_data["assignments"]]
    plan = SplitPlan(source=plan_data["source"], assignments=assignments)

    with console.status("[bold]writing prompts...[/bold]"):
        write_prompts(plan, roster, repo)

    console.print(f"[green]✓ {ros_dir / 'COORDINATION.md'}[/green]")
    for f in sorted((ros_dir / "prompts").glob("*.md")):
        console.print(f"[green]✓ {f}[/green]")

    console.print(
        "\n[dim]Copy each agent's prompt and paste it into a separate coding agent session to kick things off.[/dim]"
    )


@app.command()
def review(
    repo: Path = typer.Option(None, "--repo", "-r", help="Target repo path (defaults to git root)"),
) -> None:
    """Review the run using git log and optional agent output files."""
    repo = _resolve_repo(repo, Path.cwd())
    try:
        _do_review(repo)
    except (
        APIError,
        DecomposeError,
        RunError,
        FileNotFoundError,
        PermissionError,
    ) as e:
        _format_error(e)
        raise typer.Exit(1)


def _do_review(repo: Path) -> None:
    ros_dir = get_roster_dir(repo)

    plan_path = ros_dir / "split-plan.json"
    if not plan_path.exists():
        console.print("[red]No split plan found. Run `roster split` first.[/red]")
        raise typer.Exit(1)

    plan_data = json.loads(plan_path.read_text())
    assignments = [Assignment(**a) for a in plan_data["assignments"]]
    plan = SplitPlan(source=plan_data["source"], assignments=assignments)

    with console.status("[bold]reviewing the run...[/bold]"):
        review_content = generate_review(plan, repo, ros_dir / "outputs")

    review_out = ros_dir / "review.md"
    review_out.write_text(review_content)
    console.print(f"[green]✓ Review written to {review_out}[/green]\n")
    console.print(review_content)
