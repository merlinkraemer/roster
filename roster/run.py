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
    Passive monitor for parallel agent runs.

    Auto-polls git every 30s, displays per-agent status,
    and accepts commands: update <agent> <note> | done | q
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
        self.agent_notes: dict[str, str] = {a: "" for a in agents}

        # Track which commit hashes we've already processed
        self.seen_commits: set[str] = set()

        # Capture initial git state
        self.baseline_commit = self._get_head_commit()

        self._running = True
        self._lock = threading.Lock()

    def start(self) -> None:
        """Main loop: auto-poll in background, accept commands from stdin."""
        self.console.print(
            "\n[dim]auto-refreshing every 30s. commands: update <agent> <note> | done | q[/dim]\n"
        )
        self._render_status()

        poll_thread = threading.Thread(target=self._auto_poll, daemon=True)
        poll_thread.start()

        while self._running:
            try:
                line = input("> ").strip()
            except (KeyboardInterrupt, EOFError):
                self._running = False
                break

            if not line:
                with self._lock:
                    self._render_status()
            else:
                cmd = line.lower()
                if cmd in ("q", "quit", "exit"):
                    self._running = False
                    self.console.print("[dim]done watching.[/dim]")
                elif cmd == "done":
                    path = self._write_cycle_summary()
                    self.console.print(f"\n[green]cycle summary → {path}[/green]")
                    self._running = False
                elif line.lower().startswith("update "):
                    self._handle_update_cmd(line[7:])
                else:
                    self.console.print(
                        "[dim]commands: update <agent> <note> | done | q[/dim]"
                    )

    def _auto_poll(self) -> None:
        """Background thread: poll git every REFRESH_INTERVAL seconds."""
        while self._running:
            time.sleep(self.REFRESH_INTERVAL)
            if not self._running:
                break
            with self._lock:
                new_commits = self._poll_git()
                changed_files = self._poll_files()
            if new_commits:
                self._display_new_commits(new_commits)
            if new_commits or changed_files:
                self._render_status()

    def _handle_update_cmd(self, rest: str) -> None:
        """Parse and store: update <agent> <note>"""
        parts = rest.split(" ", 1)
        if len(parts) < 2:
            self.console.print("[dim]usage: update <agent> <note>[/dim]")
            return
        agent_name, note = parts[0], parts[1]
        # case-insensitive match
        if agent_name not in self.agent_notes:
            matches = [a for a in self.agent_notes if a.lower() == agent_name.lower()]
            if matches:
                agent_name = matches[0]
            else:
                self.console.print(f"[red]unknown agent: {agent_name}[/red]")
                return
        self.agent_notes[agent_name] = note
        self.console.print(f"[dim]note saved for {agent_name}[/dim]")
        self._render_status()

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
            status = self._get_agent_status(agent)
            note = self.agent_notes.get(agent, "")

            lines += [
                f"### {agent}",
                "",
                f"**Work:** {'; '.join(assignment.work)}",
                f"**Files:** {', '.join(assignment.files)}",
                f"**Status:** {status}",
                f"**Commits:** {len(commits)}",
                f"**Files changed:** {len(files)}",
            ]
            if note:
                lines.append(f"**Summary:** {note}")
            lines.append("")

        all_commits = []
        for agent_commits in self.agent_commits.values():
            all_commits.extend(agent_commits)
        all_commits.sort(key=lambda c: c["timestamp"])

        if all_commits:
            lines += ["## Commit log", ""]
            for c in all_commits:
                lines.append(f"- `{c['hash']}` [{c['agent']}] {c['message']}")
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

        # Show any agent notes below the table
        notes = {a: n for a, n in self.agent_notes.items() if n}
        if notes:
            for agent, note in notes.items():
                self.console.print(f"  [cyan]{agent}[/cyan] [dim]→[/dim] {note}")
