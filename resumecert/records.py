"""Shared records; policy input excludes the trusted audit record."""

from dataclasses import dataclass
from enum import Enum

from .contract import ActionContract, Facts


class Decision(str, Enum):
    ALLOW = "allow"
    BLOCK = "block"
    DEFER = "defer"
    INCONSISTENT = "inconsistent"


@dataclass(frozen=True, slots=True)
class Scope:
    action_id: str
    contract_digest: str
    snapshot_id: str
    run_id: str


@dataclass(frozen=True, slots=True)
class Context:
    contract: ActionContract
    scope: Scope
    budget: int
    initial_facts: Facts
    checkpoint: str
    resume_message: str


@dataclass(frozen=True, slots=True)
class Read:
    scope: Scope
    ordinal: int
    query: str
    values: Facts
    cost: int


@dataclass(frozen=True, slots=True)
class Certificate:
    scope: Scope
    budget: int
    initial_facts: Facts
    reads: tuple[Read, ...]
    decision: Decision
    compatible_worlds: int
    spent: int


@dataclass(frozen=True, slots=True)
class Audit:
    context: Context
    live: Facts
    reads: tuple[Read, ...]
    spent: int


@dataclass(frozen=True, slots=True)
class Verification:
    accepted: bool
    reason: str
