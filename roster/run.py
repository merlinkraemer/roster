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

from .config import get_roster_dir
from .models import Agent, Assignment, SplitPlan
from .prompts import write_prompts


class RunError(Exception):
    """Raised when run preparation fails."""

    pass


def prepare_run(
    repo: Path,
    plan_path: Path,
    roster: list[Agent],
    assignments: list[Assignment],
) -> dict:
    """
    Finalize a parallel agent run: save plan, write prompts.

    Args:
        repo: Path to the git repository (contains .roster/ dir)
        plan_path: Path to the plan file or directory
        roster: Confirmed agent roster
        assignments: Confirmed work assignments

    Returns:
        dict with keys:
            - "plan": SplitPlan
            - "prompts": dict[str, str]
            - "prompts_dir": Path to .roster/prompts/
            - "coordination_path": Path to .roster/COORDINATION.md
    """
    roster_dir = get_roster_dir(repo)

    plan = SplitPlan(source=str(plan_path), assignments=assignments)
    _save_split_plan(plan, roster_dir)

    agent_prompts = write_prompts(plan, roster, repo)

    return {
        "plan": plan,
        "prompts": agent_prompts,
        "prompts_dir": roster_dir / "prompts",
        "coordination_path": roster_dir / "COORDINATION.md",
    }


def _save_split_plan(plan: SplitPlan, roster_dir: Path) -> None:
    """Save the split plan to .roster/split-plan.json."""
    roster_dir.mkdir(parents=True, exist_ok=True)
    path = roster_dir / "split-plan.json"
    data = {
        "source": plan.source,
        "assignments": [
            {"agent": a.agent, "work": a.work, "files": a.files}
            for a in plan.assignments
        ],
    }
    path.write_text(json.dumps(data, indent=2))


class Monitor:
    """
    Passive monitor for parallel agent runs.

    Watches the repo for git commits, displays per-agent status,
    and auto-detects active/idle/done state from git activity.
    """

    def __init__(self, repo: Path, plan: SplitPlan):
        self.repo = repo
        self.plan = plan
        self.console = Console()

        # Build file → agent ownership map
        self._file_owners: dict[str, str] = {}
        for assignment in plan.assignments:
            for f in assignment.files:
                self._file_owners[f] = assignment.agent

        # Per-agent tracking
        agents = [a.agent for a in plan.assignments]
        self.agent_commits: dict[str, list[dict]] = {a: [] for a in agents}
        self.agent_files: dict[str, set[str]] = {a: set() for a in agents}
        self.agent_last_commit: dict[str, float | None] = {a: None for a in agents}

        # Track which commit hashes we've already processed
        self.seen_commits: set[str] = set()

        # Capture initial git state
        self.baseline_commit = self._get_head_commit()

        self._running = True

    def start(self) -> None:
        """Main loop: poll git, display status, accept q to quit."""
        self.console.print(
            "\n[dim]watching for commits... press Enter to refresh, q to quit.[/dim]\n"
        )
        self._render_status()

        while self._running:
            new_commits = self._poll_git()
            changed_files = self._poll_files()

            if new_commits:
                self._display_new_commits(new_commits)
            if new_commits or changed_files:
                self._render_status()

            try:
                line = Prompt.ask("\n[dim]>[/dim]", console=self.console, default="")
                cmd = line.strip().lower()
                if cmd in ("q", "quit", "exit"):
                    self._running = False
                    self.console.print("[dim]done watching.[/dim]")
                elif cmd:
                    self.console.print("[dim]press Enter to refresh, q to quit.[/dim]")
            except (KeyboardInterrupt, EOFError):
                self._running = False

    def _get_head_commit(self) -> str:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=self.repo,
            capture_output=True,
            text=True,
        )
        return result.stdout.strip()

    def _poll_git(self) -> list[dict]:
        """Check for new commits since baseline. Returns only unseen commits."""
        result = subprocess.run(
            ["git", "log", "--pretty=format:%H %at %s", f"{self.baseline_commit}..HEAD"],
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
            parts = line.split(" ", 2)
            if len(parts) < 3:
                continue
            commit_hash, timestamp_str, message = parts[0], parts[1], parts[2]

            if commit_hash in self.seen_commits:
                continue
            self.seen_commits.add(commit_hash)

            try:
                timestamp = float(timestamp_str)
            except ValueError:
                timestamp = time.time()

            match = re.match(r"\[([^\]]+)\]", message)
            agent = match.group(1) if match else "unknown"

            commit_info = {
                "agent": agent,
                "hash": commit_hash[:7],
                "message": message,
                "timestamp": timestamp,
            }
            new_commits.append(commit_info)

            if agent in self.agent_commits:
                self.agent_commits[agent].append(commit_info)
                last = self.agent_last_commit.get(agent)
                if last is None or timestamp > last:
                    self.agent_last_commit[agent] = timestamp

        return new_commits

    def _poll_files(self) -> list[str]:
        """Check for changed files since baseline."""
        result = subprocess.run(
            ["git", "diff", "--name-only", self.baseline_commit],
            cwd=self.repo,
            capture_output=True,
            text=True,
        )

        if not result.stdout.strip():
            return []

        changed = result.stdout.strip().split("\n")
        for f in changed:
            owner = self._file_owners.get(f)
            if owner:
                self.agent_files[owner].add(f)

        return changed

    def _get_agent_status(self, agent: str) -> str:
        commits = self.agent_commits.get(agent, [])
        last_time = self.agent_last_commit.get(agent)

        if not commits:
            return "idle"

        now = time.time()
        elapsed = now - last_time if last_time is not None else float("inf")

        # Done: all owned files touched and idle for 10+ minutes
        owned = {f for f, owner in self._file_owners.items() if owner == agent}
        touched = self.agent_files.get(agent, set())
        all_touched = bool(owned) and owned.issubset(touched)

        if all_touched and elapsed > 600:
            return "done"
        elif elapsed < 300:
            return "active"
        else:
            return "idle"

    def _format_last_commit(self, agent: str) -> str:
        last_time = self.agent_last_commit.get(agent)
        if last_time is None:
            return "—"
        elapsed = time.time() - last_time
        if elapsed < 60:
            return f"{int(elapsed)}s ago"
        elif elapsed < 3600:
            return f"{int(elapsed / 60)}m ago"
        else:
            return f"{int(elapsed / 3600)}h ago"

    def _display_new_commits(self, commits: list[dict]) -> None:
        for c in commits:
            self.console.print(
                f"[green][{c['agent']}][/green] {c['hash']} {c['message']}"
            )

    def _render_status(self) -> None:
        table = Table(show_header=True, header_style="bold")
        table.add_column("Agent", style="cyan")
        table.add_column("Status")
        table.add_column("Commits", justify="right")
        table.add_column("Files Changed", justify="right")
        table.add_column("Last Commit")

        status_styles = {
            "active": "[green]doing stuff...[/]",
            "idle": "[yellow]idle[/]",
            "done": "[bold green]done ✓[/]",
        }

        for assignment in self.plan.assignments:
            agent = assignment.agent
            status = self._get_agent_status(agent)
            status_str = status_styles.get(status, status)
            commits = len(self.agent_commits.get(agent, []))
            files = len(self.agent_files.get(agent, set()))
            last_commit = self._format_last_commit(agent)

            table.add_row(agent, status_str, str(commits), str(files), last_commit)

        self.console.print(
            Panel(table, title="[bold]Agent Status[/]", border_style="blue")
        )
