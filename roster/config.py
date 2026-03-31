import json
import os
from pathlib import Path

from .models import Agent

_AUTH_FILE = Path.home() / ".config" / "roster" / "auth.json"


def get_roster_dir(repo_path: Path | None = None) -> Path:
    return (repo_path or Path.cwd()) / ".roster"


def save_api_key(key: str) -> None:
    _AUTH_FILE.parent.mkdir(parents=True, exist_ok=True)
    _AUTH_FILE.write_text(json.dumps({"zai_api_key": key}))
    _AUTH_FILE.chmod(0o600)


def load_api_key() -> str | None:
    """Return key from auth file, falling back to ZAI_API_KEY env var."""
    if env_key := os.environ.get("ZAI_API_KEY"):
        return env_key
    if _AUTH_FILE.exists():
        return json.loads(_AUTH_FILE.read_text()).get("zai_api_key")
    return None


def load_roster(repo_path: Path | None = None) -> list[Agent]:
    path = get_roster_dir(repo_path) / "roster.json"
    if not path.exists():
        return []
    data = json.loads(path.read_text())
    return [Agent(**a) for a in data["agents"]]


def save_roster(agents: list[Agent], repo_path: Path | None = None) -> None:
    d = get_roster_dir(repo_path)
    d.mkdir(parents=True, exist_ok=True)
    path = d / "roster.json"
    data = {
        "agents": [
            {
                "name": a.name,
                "tier": a.tier,
                "role": a.role,
                "domains": a.domains,
            }
            for a in agents
        ]
    }
    path.write_text(json.dumps(data, indent=2))
