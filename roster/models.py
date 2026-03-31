from dataclasses import dataclass
from typing import Literal

Complexity = Literal["low", "medium", "high"]
MaxComplexity = Literal["low", "medium", "high", "any"]

ARCHETYPE_DEFAULTS: dict[str, dict] = {
    "craftsman": {
        "domains": ["code quality", "refactoring", "tests", "best practices"],
        "persona": "You are The Craftsman: prioritize code quality, precision, and best practices above all else.",
    },
    "architect": {
        "domains": ["system design", "api contracts", "data models", "infra"],
        "persona": "You are The Architect: focus on system design, contracts, and structure.",
    },
    "explorer": {
        "domains": ["feature implementation", "prototyping"],
        "persona": "You are The Explorer: embrace new features, prototyping, and breadth.",
    },
    "reviewer": {
        "domains": ["docs", "comments", "tests", "simple refactors"],
        "persona": "You are The Reviewer: focus on low-risk improvements, documentation, and simple fixes.",
    },
}


@dataclass
class Agent:
    name: str
    confidence: int  # 0-100
    domains: list[str]
    max_complexity: MaxComplexity = "any"
    archetype: str | None = None


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
