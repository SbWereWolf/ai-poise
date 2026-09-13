"""Domain contract for the one authorized Task process-snapshot migration."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass

from ...common import digest, exact_keys
from ..backups.domain import exact_backup_name
from ..foundation.errors import DomainError


AUTHORIZED_TASK_IDS = ("0077", "0081", "0079", "0078", "0074", "0063")
TASK_0082_AUTHORIZED_IDS = ("0082",)
AUTHORIZED_TASK_IDS_BY_SCHEMA = {
    "task-process-migration-1": AUTHORIZED_TASK_IDS,
    "task-process-migration-2": TASK_0082_AUTHORIZED_IDS,
}
NONTERMINAL_STATUSES = frozenset({"available", "active", "verified", "accepted"})


@dataclass(frozen=True)
class MigrationRequest:
    request_id: str
    backup_name: str
    task_ids: tuple[str, ...]
    authorization: str
    digest: str

    @classmethod
    def parse(cls, value: object) -> "MigrationRequest":
        fields = {"schema", "request_id", "backup_name", "task_ids", "authorization"}
        exact_keys(value, fields, "task process migration request")
        schema = value["schema"]
        if schema not in AUTHORIZED_TASK_IDS_BY_SCHEMA:
            raise DomainError("Unsupported task process migration schema")
        if not isinstance(value["request_id"], str) or not value["request_id"].strip():
            raise DomainError("Task process migration request_id must be a nonempty string")
        if not isinstance(value["authorization"], str) or not value["authorization"].strip():
            raise DomainError("Task process migration authorization must be a nonempty string")
        backup_name = exact_backup_name(value["backup_name"])
        authorized_task_ids = AUTHORIZED_TASK_IDS_BY_SCHEMA[schema]
        if not isinstance(value["task_ids"], list) or tuple(value["task_ids"]) != authorized_task_ids:
            if schema == "task-process-migration-1":
                raise DomainError(
                    "Task process migration accepts exactly 0077, 0081, 0079, 0078, 0074 and 0063 in that order"
                )
            raise DomainError("Task process migration accepts exactly 0082")
        normalized = {
            "schema": schema,
            "request_id": value["request_id"],
            "backup_name": backup_name,
            "task_ids": list(authorized_task_ids),
            "authorization": value["authorization"],
        }
        return cls(
            request_id=value["request_id"],
            backup_name=backup_name,
            task_ids=authorized_task_ids,
            authorization=value["authorization"],
            digest=digest(normalized),
        )


@dataclass(frozen=True)
class ProcessUpdate:
    task_id: str
    process: dict
    old_process_digest: str
    new_process_digest: str


@dataclass(frozen=True)
class MigrationPlan:
    updates: tuple[ProcessUpdate, ...]


class ProcessSnapshotMigration:
    @staticmethod
    def plan(request: MigrationRequest, rows: list[dict], processes: dict[str, dict]) -> MigrationPlan:
        by_id = {row.get("id"): row for row in rows if isinstance(row, dict)}
        if len(by_id) != len(rows):
            raise DomainError("Task process migration targets contain duplicate or invalid Task IDs")
        missing = [task_id for task_id in request.task_ids if task_id not in by_id]
        extra = sorted(task_id for task_id in by_id if task_id not in request.task_ids)
        if missing or extra:
            raise DomainError(f"Task process migration target mismatch; missing {missing}; extra {extra}")

        updates = []
        for task_id in request.task_ids:
            row = by_id[task_id]
            if row.get("status") not in NONTERMINAL_STATUSES:
                raise DomainError(f"Task {task_id} is terminal or has an incompatible status")
            metadata = row.get("metadata")
            if not isinstance(metadata, dict):
                raise DomainError(f"Task {task_id} metadata is incompatible")
            contract = metadata.get("contract")
            process = metadata.get("process")
            if not isinstance(contract, dict) or not isinstance(process, dict):
                raise DomainError(f"Task {task_id} process definition is incompatible")
            goal_type = contract.get("goal_type")
            if not isinstance(goal_type, str) or not goal_type:
                raise DomainError(f"Task {task_id} immutable goal_type is incompatible")
            if process.get("goal_type") != goal_type:
                raise DomainError(f"Task {task_id} goal_type does not match its immutable contract")
            configured = processes.get(goal_type)
            if not isinstance(configured, dict) or configured.get("goal_type") != goal_type:
                raise DomainError(f"Task {task_id} goal_type has no matching configured process")
            worktree_required = configured.get("worktree_required")
            if type(worktree_required) is not bool:
                raise DomainError(f"Task {task_id} configured worktree_required is not explicit")
            if "worktree_required" in process:
                raise DomainError(f"Task {task_id} already has a conflicting worktree_required value")
            candidate = deepcopy(process)
            candidate["worktree_required"] = worktree_required
            updates.append(
                ProcessUpdate(task_id, candidate, digest(process), digest(candidate))
            )
        return MigrationPlan(tuple(updates))
