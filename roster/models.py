from dataclasses import dataclass
from typing import Literal

Complexity = Literal["low", "medium", "high"]
Tier = Literal["low", "medium", "high"]

ROLE_DEFAULTS: dict[str, dict] = {
    "builder": {
        "domains": ["code quality", "refactoring", "tests", "best practices"],
        "persona": "You are a builder: prioritize code quality, precision, and best practices above all else.",
    },
    "architect": {
        "domains": ["system design", "api contracts", "data models", "infra"],
        "persona": "You are an architect: focus on system design, contracts, and structure.",
    },
    "explorer": {
        "domains": ["feature implementation", "prototyping"],
        "persona": "You are an explorer: embrace new features, prototyping, and breadth.",
    },
    "reviewer": {
        "domains": ["docs", "comments", "tests", "simple refactors"],
        "persona": "You are a reviewer: focus on low-risk improvements, documentation, and simple fixes.",
    },
}

# Tier determines max task complexity an agent can handle.
# low  → low complexity tasks only (docs, simple fixes, config)
# medium → low + medium tasks (standard implementation)
# high → any task complexity (complex architecture, hard problems)

TIER_ORDER = {"low": 0, "medium": 1, "high": 2}


@dataclass
class Agent:
    name: str
    tier: Tier
    domains: list[str]
    role: str | None = None


@dataclass
class Task:
    id: str
    description: str
    files: list[str]
    complexity: Complexity
    agent: str
    reason: str


@dataclass
class SplitPlan:
    source: str
    delegation_strategy: str
    tasks: list[Task]
