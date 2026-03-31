"""Orchestrator for `roster run` - prepares execution and monitors progress."""

from __future__ import annotations

import json
import re
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
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
    Monitor for parallel agent runs.

    Shows a factual git-based table (commits, files changed, last commit).
    Accepts freeform notes from the user. 'done' compiles everything into
    a cycle summary file.
    """

    REFRESH_INTERVAL = 30  # seconds between auto-polls

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

        # Freeform notes log: list of (timestamp, text)
        self.notes: list[tuple[float, str]] = []

        # Track which commit hashes we've already processed
        self.seen_commits: set[str] = set()

        # Capture initial git state
        self.baseline_commit = self._get_head_commit()

        self._running = True
        self._lock = threading.Lock()

    def start(self) -> None:
        """Main loop: auto-poll in background, accept notes from stdin."""
        self.console.print(
            "\n[dim]type a note and press Enter to save it. 'done' to finish and write cycle summary, 'q' to quit.[/dim]\n"
        )
        self._render()

        poll_thread = threading.Thread(target=self._auto_poll, daemon=True)
        poll_thread.start()

        while self._running:
            try:
                line = input("> ").strip()
            except (KeyboardInterrupt, EOFError):
                self._running = False
                break

            if not line:
                self._render()
            elif line.lower() in ("q", "quit", "exit"):
                self._running = False
                self.console.print("[dim]done watching.[/dim]")
            elif line.lower() == "done":
                path = self._write_cycle_summary()
                self.console.print(f"\n[green]cycle summary → {path}[/green]")
                self._running = False
            else:
                self.notes.append((time.time(), line))
                self.console.print("[dim]saved.[/dim]")

    def _auto_poll(self) -> None:
        """Background thread: poll git every REFRESH_INTERVAL seconds."""
        while self._running:
            time.sleep(self.REFRESH_INTERVAL)
            if not self._running:
                break
            with self._lock:
                new_commits = self._poll_git()
                self._poll_files()
            if new_commits:
                self._display_new_commits(new_commits)
                self._render()

    def _render(self) -> None:
        """Print the status table followed by any saved notes."""
        table = Table(show_header=True, header_style="bold", show_lines=True)
        table.add_column("Agent", style="cyan")
        table.add_column("Commits", justify="right")
        table.add_column("Files Changed", justify="right")
        table.add_column("Last Commit")

        for assignment in self.plan.assignments:
            agent = assignment.agent
            commits = len(self.agent_commits.get(agent, []))
            files = len(self.agent_files.get(agent, set()))
            last_commit = self._format_last_commit(agent)
            table.add_row(agent, str(commits), str(files), last_commit)

        self.console.print(
            Panel(table, title="[bold]Agent Progress[/]", border_style="blue")
        )

        if self.notes:
            self.console.print("[bold]Notes[/bold]")
            for ts, text in self.notes:
                dt = datetime.fromtimestamp(ts).strftime("%H:%M")
                self.console.print(f"  [dim]{dt}[/dim]  {text}")
            self.console.print()

    def _write_cycle_summary(self) -> Path:
        """Write a cycle summary markdown file to .roster/cycle-N.md."""
        roster_dir = get_roster_dir(self.repo)
        existing = list(roster_dir.glob("cycle-*.md"))
        cycle_num = len(existing) + 1

        now = datetime.now()
        lines = [
            f"# Cycle {cycle_num} Summary",
            "",
            f"Date: {now.strftime('%Y-%m-%d %H:%M')}",
            "",
            "## Agents",
            "",
        ]

        for assignment in self.plan.assignments:
            agent = assignment.agent
            commits = self.agent_commits.get(agent, [])
            files = self.agent_files.get(agent, set())
            lines += [
                f"### {agent}",
                "",
                f"**Work:** {'; '.join(assignment.work)}",
                f"**Owned files:** {', '.join(assignment.files)}",
                f"**Commits:** {len(commits)}",
                f"**Files changed:** {len(files)}",
                "",
            ]

        all_commits: list[dict] = []
        for agent_commits in self.agent_commits.values():
            all_commits.extend(agent_commits)
        all_commits.sort(key=lambda c: c["timestamp"])

        if all_commits:
            lines += ["## Commit log", ""]
            for c in all_commits:
                lines.append(f"- `{c['hash']}` [{c['agent']}] {c['message']}")
            lines.append("")

        if self.notes:
            lines += ["## Notes", ""]
            for ts, text in self.notes:
                dt = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
                lines.append(f"- **{dt}** {text}")
            lines.append("")

        path = roster_dir / f"cycle-{cycle_num}.md"
        path.write_text("\n".join(lines))
        return path

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
