import re
import subprocess
from pathlib import Path

from .llm import call_llm
from .models import SplitPlan

_SYSTEM = """You are a technical lead reviewing the output of a parallel AI agent development run.
Given the git log and task plan, write a concise structured review in markdown.

Include:
- Per-agent summary: commits made, files changed, tasks addressed
- Overall assessment of the run
- Open items or things to manually verify

Be factual and concise. Return only the markdown document, starting with a # heading."""


def _parse_git_log(repo_path: Path) -> str:
    result = subprocess.run(
        ["git", "log", "--pretty=format:COMMIT: %s", "--name-only"],
        cwd=repo_path,
        capture_output=True,
        text=True,
    )
    return result.stdout


def detect_violations(plan: SplitPlan, git_log: str) -> list[str]:
    """Best-effort detection of files touched outside assigned ownership."""
    file_owners: dict[str, str] = {}
    for task in plan.tasks:
        for f in task.files:
            file_owners[f] = task.agent

    violations: list[str] = []
    current_agent: str | None = None

    for line in git_log.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("COMMIT:"):
            match = re.search(r"\[([^\]]+)\]", line)
            current_agent = match.group(1) if match else None
        elif current_agent:
            owner = file_owners.get(line)
            if owner and owner != current_agent:
                violations.append(
                    f"`{current_agent}` touched `{line}` (owned by `{owner}`)"
                )

    return violations


def generate_review(plan: SplitPlan, repo_path: Path, outputs_dir: Path) -> str:
    git_log = _parse_git_log(repo_path)

    outputs_text = ""
    if outputs_dir.exists():
        for f in sorted(outputs_dir.glob("*.md")):
            outputs_text += f"\n### {f.stem}\n\n{f.read_text()}\n"

    task_summary = "\n".join(
        f"- {t.id} ({t.agent}): {t.description} — files: {', '.join(t.files)}"
        for t in plan.tasks
    )

    user_msg = f"## Task Plan\n\n{task_summary}\n\n## Git Log\n\n{git_log}"
    if outputs_text:
        user_msg += f"\n\n## Agent Outputs\n{outputs_text}"

    review_body = call_llm(_SYSTEM, user_msg)

    violations = detect_violations(plan, git_log)
    if violations:
        violation_lines = "\n".join(f"- {v}" for v in violations)
        review_body += f"\n\n## Boundary Violations\n\n{violation_lines}\n"
    else:
        review_body += "\n\n## Boundary Violations\n\nNone detected.\n"

    return review_body
