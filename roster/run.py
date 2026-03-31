"""Orchestrator for `roster run` - prepares execution and monitors progress."""

from __future__ import annotations

import json
import re
import subprocess
import time
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt
from rich.table import Table

from .assign import validate_assignments
from .config import get_roster_dir, load_roster, save_roster
from .decompose import decompose_plan, suggest_roster
from .models import Agent, SplitPlan
from .prompts import write_prompts
from .review import generate_review


class RunError(Exception):
    """Raised when run preparation fails."""

    pass


def prepare_run(repo: Path, plan_path: Path) -> dict:
    """
    Prepare a parallel agent run.

    Args:
        repo: Path to the git repository (contains .roster/ dir)
        plan_path: Path to the plan file or directory

    Returns:
        dict with keys:
            - "roster": list[Agent] (may be empty if not yet configured)
            - "plan": SplitPlan
            - "prompts_dir": Path to .roster/prompts/
            - "coordination_path": Path to .roster/COORDINATION.md

    Raises:
        RunError: if preparation fails
    """
    roster_dir = get_roster_dir(repo)

    # 1. Check for existing roster
    roster = load_roster(repo)

    # 2. Read plan text
    plan_text = _read_plan(plan_path)

    # 3. Decompose plan into tasks
    if not roster:
        raise RunError("No roster configured. Run 'roster init' first.")

    tasks = decompose_plan(plan_text, roster)

    # 4. Validate assignments
    violations = validate_assignments(tasks, roster)
    if violations:
        violation_list = "\n".join(f"  - {v}" for v in violations)
        raise RunError(f"Assignment violations detected:\n{violation_list}")

    # 5. Build and save SplitPlan
    plan = SplitPlan(
        source=str(plan_path),
        delegation_strategy="domain-fit",
        tasks=tasks,
    )
    _save_split_plan(plan, roster_dir)

    # 6. Write prompts (COORDINATION.md + per-agent prompts)
    write_prompts(plan, roster, plan_text, repo)

    return {
        "roster": roster,
        "plan": plan,
        "prompts_dir": roster_dir / "prompts",
        "coordination_path": roster_dir / "COORDINATION.md",
    }


def _read_plan(plan_path: Path) -> str:
    """Read plan from file or directory."""
    if plan_path.is_dir():
        # Concatenate all .md files in the directory
        parts = []
        for f in sorted(plan_path.glob("*.md")):
            parts.append(f.read_text())
        if not parts:
            raise RunError(f"No .md files found in {plan_path}")
        return "\n\n---\n\n".join(parts)
    else:
        if not plan_path.exists():
            raise RunError(f"Plan file not found: {plan_path}")
        return plan_path.read_text()


def _save_split_plan(plan: SplitPlan, roster_dir: Path) -> None:
    """Save the split plan to .roster/split-plan.json."""
    roster_dir.mkdir(parents=True, exist_ok=True)
    path = roster_dir / "split-plan.json"
    data = {
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
    path.write_text(json.dumps(data, indent=2))


class Monitor:
    """
    Interactive monitor for parallel agent runs.

    Watches the repo for git and filesystem changes, displays status,
    and accepts user commands.
    """

    def __init__(self, repo: Path, plan: SplitPlan):
        self.repo = repo
        self.plan = plan
        self.console = Console()

        # Agent status tracking
        self.agent_status: dict[str, str] = {
            task.agent: "working" for task in plan.tasks
        }

        # Track commits and files per agent
        self.agent_commits: dict[str, list[dict]] = {
            task.agent: [] for task in plan.tasks
        }
        self.agent_files: dict[str, set[str]] = {
            task.agent: set() for task in plan.tasks
        }

        # Capture initial git state
        self.baseline_commit = self._get_head_commit()
        self.baseline_log = self._get_commit_list()

        # Running state
        self._running = True

    def start(self) -> None:
        """Main loop: poll git + filesystem, display status, accept user input."""
        self._show_welcome()
        self._render_status()

        while self._running:
            # Poll for changes
            new_commits = self._poll_git()
            changed_files = self._poll_files()

            # Display any new activity
            if new_commits:
                self._display_new_commits(new_commits)
            if changed_files:
                self._display_changed_files(changed_files)

            # Re-render status if there was activity
            if new_commits or changed_files:
                self._render_status()

            # Wait for user input
            try:
                line = Prompt.ask("\n[bold cyan]>[/]", console=self.console, default="")
                if line.strip():
                    response = self._handle_input(line.strip())
                    if response:
                        self.console.print(response)
                    # Re-render status after commands
                    if line.strip() not in ("status",):
                        self._render_status()
            except KeyboardInterrupt:
                self.console.print("\n[yellow]Interrupted. Type 'q' to exit.[/]")
            except EOFError:
                self._running = False

    def _get_head_commit(self) -> str:
        """Get current HEAD commit hash."""
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=self.repo,
            capture_output=True,
            text=True,
        )
        return result.stdout.strip()

    def _get_commit_list(self) -> list[str]:
        """Get list of commit hashes from git log."""
        result = subprocess.run(
            ["git", "log", "--pretty=format:%H"],
            cwd=self.repo,
            capture_output=True,
            text=True,
        )
        return result.stdout.strip().split("\n") if result.stdout.strip() else []

    def _poll_git(self) -> list[dict]:
        """
        Check for new commits since baseline.

        Returns list of dicts with keys: agent, hash, message
        """
        result = subprocess.run(
            ["git", "log", "--pretty=format:%H %s", f"{self.baseline_commit}..HEAD"],
            cwd=self.repo,
            capture_output=True,
            text=True,
        )

        if not result.stdout.strip():
            return []

        new_commits = []
        for line in result.stdout.strip().split("\n"):
            if not line:
                continue
            parts = line.split(" ", 1)
            if len(parts) < 2:
                continue
            commit_hash, message = parts[0], parts[1]

            # Parse agent name from [agent-name] prefix
            match = re.match(r"\[([^\]]+)\]", message)
            agent = match.group(1) if match else "unknown"

            commit_info = {
                "agent": agent,
                "hash": commit_hash[:7],
                "message": message,
            }
            new_commits.append(commit_info)

            # Track per agent
            if agent in self.agent_commits:
                self.agent_commits[agent].append(commit_info)

        return new_commits

    def _poll_files(self) -> list[str]:
        """
        Check for changed files since baseline.

        Returns list of changed file paths.
        """
        result = subprocess.run(
            ["git", "diff", "--name-only", self.baseline_commit],
            cwd=self.repo,
            capture_output=True,
            text=True,
        )

        if not result.stdout.strip():
            return []

        changed = result.stdout.strip().split("\n")

        # Track per agent based on file ownership in plan
        for f in changed:
            for task in self.plan.tasks:
                if self._file_matches_pattern(f, task.files):
                    self.agent_files[task.agent].add(f)
                    break

        return changed

    def _file_matches_pattern(self, filepath: str, patterns: list[str]) -> bool:
        """Check if filepath matches any of the ownership patterns."""
        for pattern in patterns:
            if pattern.endswith("/"):
                # Directory pattern
                if filepath.startswith(pattern):
                    return True
            else:
                # Exact file match
                if filepath == pattern:
                    return True
        return False

    def _display_new_commits(self, commits: list[dict]) -> None:
        """Display new commits as they appear."""
        for c in commits:
            self.console.print(
                f"[green][{c['agent']}][/green] {c['hash']} {c['message']}"
            )

    def _display_changed_files(self, files: list[str]) -> None:
        """Display changed files."""
        for f in files:
            self.console.print(f"  [dim]+[/dim] {f}")

    def _render_status(self) -> None:
        """Build and display a status panel showing per-agent state."""
        table = Table(show_header=True, header_style="bold")
        table.add_column("Agent", style="cyan")
        table.add_column("Status")
        table.add_column("Commits", justify="right")
        table.add_column("Files Changed", justify="right")

        status_styles = {
            "working": "[yellow]working[/]",
            "done": "[green]done[/]",
            "blocked": "[red]blocked[/]",
        }

        for agent in self.agent_status:
            status = self.agent_status[agent]
            status_str = status_styles.get(status, status)
            commits = len(self.agent_commits.get(agent, []))
            files = len(self.agent_files.get(agent, set()))

            table.add_row(agent, status_str, str(commits), str(files))

        self.console.print(
            Panel(table, title="[bold]Agent Status[/]", border_style="blue")
        )

    def _show_welcome(self) -> None:
        """Show welcome message with available commands."""
        help_text = """[bold]Available Commands:[/]
  [cyan]done <agent>[/]     Mark agent as done
  [cyan]blocked <agent>[/]  Mark agent as blocked
  [cyan]status[/]           Show current status
  [cyan]output <agent>[/]   Record agent output (multiline, end with blank line)
  [cyan]review[/]           Generate review summary
  [cyan]quit / q[/]         Stop monitoring"""

        self.console.print(
            Panel(help_text, title="[bold]Roster Monitor[/]", border_style="green")
        )

    def _handle_input(self, line: str) -> str | None:
        """
        Process user commands.

        Returns response message or None.
        """
        if not line:
            return None

        parts = line.split()
        cmd = parts[0].lower()

        # Handle quit/exit
        if cmd in ("quit", "exit", "q"):
            self._running = False
            return "[yellow]Stopping monitor...[/]"

        # Handle status
        if cmd == "status":
            return None  # Status is rendered every loop

        # Handle done
        if cmd == "done" or (len(parts) >= 2 and parts[-1].lower() == "done"):
            agent = parts[1] if cmd == "done" else parts[0]
            return self._mark_agent_status(agent, "done")

        # Handle blocked
        if cmd == "blocked":
            if len(parts) < 2:
                return "[red]Usage: blocked <agent>[/]"
            return self._mark_agent_status(parts[1], "blocked")

        # Handle output
        if cmd == "output":
            if len(parts) < 2:
                return "[red]Usage: output <agent>[/]"
            return self._capture_agent_output(parts[1])

        # Handle review
        if cmd == "review":
            return self._run_review()

        # Unknown command - show help
        return f"[dim]Unknown command. Available: done, blocked, status, output, review, quit[/]"

    def _mark_agent_status(self, agent: str, status: str) -> str:
        """Mark an agent with a new status."""
        if agent not in self.agent_status:
            return f"[red]Unknown agent: {agent}[/]"

        self.agent_status[agent] = status
        return f"[green]Marked {agent} as {status}[/]"

    def _capture_agent_output(self, agent: str) -> str:
        """Capture multiline output for an agent."""
        if agent not in self.agent_status:
            return f"[red]Unknown agent: {agent}[/]"

        self.console.print(f"[dim]Enter output for {agent}. End with a blank line:[/]")

        lines = []
        while True:
            try:
                line = Prompt.ask("", console=self.console, default="")
                if not line:
                    break
                lines.append(line)
            except (EOFError, KeyboardInterrupt):
                break

        if not lines:
            return "[yellow]No output recorded.[/]"

        output_text = "\n".join(lines)

        # Save to .roster/outputs/<agent>.md
        outputs_dir = get_roster_dir(self.repo) / "outputs"
        outputs_dir.mkdir(parents=True, exist_ok=True)
        output_path = outputs_dir / f"{agent}.md"

        # Append if file exists
        if output_path.exists():
            existing = output_path.read_text()
            output_text = existing + "\n\n---\n\n" + output_text

        output_path.write_text(output_text)

        return f"[green]Saved output for {agent} to {output_path.relative_to(self.repo)}[/]"

    def _run_review(self) -> str:
        """Generate and display review."""
        outputs_dir = get_roster_dir(self.repo) / "outputs"

        try:
            review = generate_review(self.plan, self.repo, outputs_dir)

            # Save review
            review_path = get_roster_dir(self.repo) / "review.md"
            review_path.write_text(review)

            self.console.print(
                Panel(review, title="[bold]Review[/]", border_style="magenta")
            )
            return f"[green]Review saved to {review_path.relative_to(self.repo)}[/]"
        except Exception as e:
            return f"[red]Failed to generate review: {e}[/]"
