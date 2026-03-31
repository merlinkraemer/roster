from dataclasses import dataclass
from typing import Literal

Tier = Literal["budget", "premium"]


@dataclass
class Agent:
    name: str
    tier: Tier
    domains: list[str]


@dataclass
class Assignment:
    agent: str
    work: list[str]
    files: list[str]


@dataclass
class SplitPlan:
    source: str
    assignments: list[Assignment]
