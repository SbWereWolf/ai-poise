from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Liveness(str, Enum):
    LIVE = "live"
    DEAD = "dead"
    UNCERTAIN = "uncertain"


class UnobservedSessionLiveness:
    """Fail-closed policy when no authoritative native lifecycle is available."""

    def __call__(self, actor):
        return Liveness.UNCERTAIN


@dataclass(frozen=True)
class OwnershipSnapshot:
    actor: str
    task_id: str | None
    worktree_task_id: str | None


@dataclass(frozen=True)
class OwnershipPreflight:
    actor: str
    task_id: str
    task_owner: str | None
    worktree_owner: str | None


@dataclass(frozen=True)
class OwnershipChange:
    before: OwnershipSnapshot
    after: OwnershipSnapshot
    recovered_sessions: tuple[str, ...]
