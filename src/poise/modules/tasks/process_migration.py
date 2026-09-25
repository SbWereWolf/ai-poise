"""Domain contract for the one authorized Task process-snapshot migration."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass

from ...common import digest, exact_keys
from ..backups.domain import exact_backup_name
from ..foundation.errors import DomainError
from ..workflow.domain import RouteDefinition
from .contracts import candidate_content_policy_from_metadata
from .domain import TaskStageContracts


AUTHORIZED_TASK_IDS = ("0077", "0081", "0079", "0078", "0074", "0063")
TASK_0082_AUTHORIZED_IDS = ("0082",)
CURRENT_PLANNING_TASK_IDS = ("ERP-HARNESS-COMPARISON-PLAN",)
ROLE_PROJECTION_SCHEMA = "task-process-role-migration-1"
AUTHORIZED_TASK_IDS_BY_SCHEMA = {
    "task-process-migration-1": AUTHORIZED_TASK_IDS,
    "task-process-migration-2": TASK_0082_AUTHORIZED_IDS,
    "task-process-migration-3": TASK_0082_AUTHORIZED_IDS,
    "task-process-migration-4": CURRENT_PLANNING_TASK_IDS,
}
STAGE_CONTRACT_RECOVERY_SCHEMAS = frozenset({
    "task-process-migration-3",
    "task-process-migration-4",
})
NONTERMINAL_STATUSES = frozenset({"available", "active", "verified", "accepted"})


@dataclass(frozen=True)
class MigrationRequest:
    schema: str
    request_id: str
    backup_name: str
    task_ids: tuple[str, ...]
    authorization: str
    sprint_id: str | None
    role_source: str | None
    digest: str

    @classmethod
    def parse(cls, value: object) -> "MigrationRequest":
        if not isinstance(value, dict):
            raise DomainError("task process migration request: expected object")
        schema = value.get("schema")
        if schema == ROLE_PROJECTION_SCHEMA:
            fields = {
                "schema", "request_id", "backup_name", "sprint_id", "task_ids",
                "role_source", "authorization",
            }
            exact_keys(value, fields, "task process migration request")
            if not isinstance(value["request_id"], str) or not value["request_id"].strip():
                raise DomainError("Task process migration request_id must be a nonempty string")
            if not isinstance(value["authorization"], str) or not value["authorization"].strip():
                raise DomainError("Task process migration authorization must be a nonempty string")
            if not isinstance(value["sprint_id"], str) or not value["sprint_id"].strip():
                raise DomainError("Task process role migration sprint_id must be a nonempty string")
            task_ids = value["task_ids"]
            if not isinstance(task_ids, list) or not task_ids:
                raise DomainError("Task process role migration task_ids must be a nonempty list")
            if any(not isinstance(task_id, str) or not task_id.strip() for task_id in task_ids):
                raise DomainError("Task process role migration task_ids require nonempty strings")
            if len(task_ids) != len(set(task_ids)):
                raise DomainError("Task process role migration task_ids must be unique")
            if value["role_source"] != "configured_process":
                raise DomainError("Task process role migration role_source must be configured_process")
            backup_name = exact_backup_name(value["backup_name"])
            normalized = {
                "schema": schema,
                "request_id": value["request_id"],
                "backup_name": backup_name,
                "sprint_id": value["sprint_id"],
                "task_ids": list(task_ids),
                "role_source": "configured_process",
                "authorization": value["authorization"],
            }
            return cls(
                schema=schema,
                request_id=value["request_id"],
                backup_name=backup_name,
                task_ids=tuple(task_ids),
                authorization=value["authorization"],
                sprint_id=value["sprint_id"],
                role_source="configured_process",
                digest=digest(normalized),
            )

        fields = {"schema", "request_id", "backup_name", "task_ids", "authorization"}
        exact_keys(value, fields, "task process migration request")
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
            if schema == "task-process-migration-4":
                raise DomainError(
                    "Task process migration accepts exactly the current planning Task"
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
            schema=schema,
            request_id=value["request_id"],
            backup_name=backup_name,
            task_ids=authorized_task_ids,
            authorization=value["authorization"],
            sprint_id=None,
            role_source=None,
            digest=digest(normalized),
        )



@dataclass(frozen=True)
class ProcessUpdate:
    task_id: str
    process: dict
    contract: dict | None
    worktree_required_added: bool
    old_process_digest: str
    new_process_digest: str
    old_contract_digest: str | None
    new_contract_digest: str | None
    stage_roles: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class MigrationPlan:
    updates: tuple[ProcessUpdate, ...]


class ProcessSnapshotMigration:
    @staticmethod
    def _without_stage_roles(process: dict) -> dict:
        candidate = deepcopy(process)
        stages = candidate.get("stages")
        if not isinstance(stages, list):
            raise DomainError("Task process role migration requires explicit stages")
        for stage in stages:
            if not isinstance(stage, dict):
                raise DomainError("Task process role migration requires stage objects")
            stage.pop("role", None)
        return candidate

    @staticmethod
    def _plan_role_projection(
        request: MigrationRequest, rows: list[dict], processes: dict[str, dict]
    ) -> MigrationPlan:
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
            if row.get("status") != "newborn":
                raise DomainError(f"Task {task_id} role projection requires newborn status")
            if row.get("claimed_by") is not None:
                raise DomainError(f"Task {task_id} role projection requires an unclaimed Task")
            metadata = row.get("metadata")
            if not isinstance(metadata, dict):
                raise DomainError(f"Task {task_id} metadata is incompatible")
            if metadata.get("sprint_id") != request.sprint_id:
                raise DomainError(f"Task {task_id} does not belong to the requested Sprint")
            newborn = metadata.get("newborn")
            if (not isinstance(newborn, dict) or set(newborn) != {"draft", "ready"}
                    or newborn.get("ready") is not False):
                raise DomainError(f"Task {task_id} role projection requires a not ready newborn Task")
            draft = newborn.get("draft")
            process = metadata.get("process")
            if not isinstance(draft, dict) or not isinstance(process, dict):
                raise DomainError(f"Task {task_id} role projection requires a frozen draft process")
            goal_type = draft.get("goal_type")
            if not isinstance(goal_type, str) or not goal_type or process.get("goal_type") != goal_type:
                raise DomainError(f"Task {task_id} goal_type is incompatible")
            configured = processes.get(goal_type)
            if not isinstance(configured, dict) or configured.get("goal_type") != goal_type:
                raise DomainError(f"Task {task_id} goal_type has no matching configured process")
            legacy_stages = process.get("stages")
            configured_stages = configured.get("stages")
            if not isinstance(legacy_stages, list) or not isinstance(configured_stages, list):
                raise DomainError(f"Task {task_id} process stages are incompatible")
            if any(not isinstance(stage, dict) or "role" in stage for stage in legacy_stages):
                raise DomainError(f"Task {task_id} process already has role metadata")
            stage_roles = []
            for stage in configured_stages:
                if not isinstance(stage, dict):
                    raise DomainError(f"Task {task_id} configured stage is incompatible")
                stage_id = stage.get("id")
                role = stage.get("role")
                if not isinstance(stage_id, str) or not stage_id:
                    raise DomainError(f"Task {task_id} configured stage id is incompatible")
                if not isinstance(role, str) or not role.strip():
                    raise DomainError(f"Task {task_id} configured process requires explicit stage roles")
                stage_roles.append((stage_id, role))
            if ProcessSnapshotMigration._without_stage_roles(process) != ProcessSnapshotMigration._without_stage_roles(configured):
                raise DomainError(
                    f"Task {task_id} frozen process differs from configured process beyond role fields"
                )
            candidate = deepcopy(process)
            for stage, (_, role) in zip(candidate["stages"], stage_roles, strict=True):
                stage["role"] = role
            RouteDefinition.from_process(candidate)
            updates.append(ProcessUpdate(
                task_id=task_id,
                process=candidate,
                contract=None,
                worktree_required_added=False,
                old_process_digest=digest(process),
                new_process_digest=digest(candidate),
                old_contract_digest=None,
                new_contract_digest=None,
                stage_roles=tuple(stage_roles),
            ))
        return MigrationPlan(tuple(updates))

    @staticmethod
    def _stage_contracts(metadata: dict, process: dict) -> list[dict]:
        try:
            contract = metadata["contract"]
            requirements = [
                *process["content_contract"]["requirements"],
                *contract["content_contract"]["requirements"],
            ]
            values = [
                {
                    "stage_id": stage["id"],
                    "allowed_paths": list(stage["allowed_paths"]),
                    "entry_requirements": [
                        item["id"] for item in requirements
                        if stage["id"] in item["stages"] and item["phase"] == "pre"
                    ],
                    "exit_requirements": [
                        item["id"] for item in requirements
                        if stage["id"] in item["stages"] and item["phase"] == "post"
                    ],
                }
                for stage in process["stages"]
            ]
        except (KeyError, TypeError) as exc:
            raise DomainError("Task stage contract source is incompatible") from exc
        route = RouteDefinition.from_process(process)
        policy = candidate_content_policy_from_metadata(
            {**metadata, "process": process},
            {
                "goal": process["content_contract"],
                "task": contract["content_contract"],
            },
        )
        return TaskStageContracts.parse(values, route, policy).to_list()

    @staticmethod
    def plan(request: MigrationRequest, rows: list[dict], processes: dict[str, dict]) -> MigrationPlan:
        if request.schema == ROLE_PROJECTION_SCHEMA:
            return ProcessSnapshotMigration._plan_role_projection(request, rows, processes)
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
            candidate = deepcopy(process)
            worktree_required_added = "worktree_required" not in candidate
            if worktree_required_added:
                candidate["worktree_required"] = worktree_required
            elif (
                request.schema not in STAGE_CONTRACT_RECOVERY_SCHEMAS
                or type(candidate["worktree_required"]) is not bool
                or candidate["worktree_required"] != worktree_required
            ):
                raise DomainError(
                    f"Task {task_id} already has a conflicting worktree_required value"
                )
            migrated_contract = None
            old_contract_digest = None
            new_contract_digest = None
            if request.schema in STAGE_CONTRACT_RECOVERY_SCHEMAS:
                if "stage_contracts" in contract:
                    raise DomainError(
                        f"Task {task_id} already has stage_contracts; exact recovery is not applicable"
                    )
                migrated_contract = deepcopy(contract)
                migrated_contract["stage_contracts"] = ProcessSnapshotMigration._stage_contracts(
                    metadata, candidate
                )
                old_contract_digest = digest(contract)
                new_contract_digest = digest(migrated_contract)
            updates.append(
                ProcessUpdate(
                    task_id,
                    candidate,
                    migrated_contract,
                    worktree_required_added,
                    digest(process),
                    digest(candidate),
                    old_contract_digest,
                    new_contract_digest,
                )
            )
        return MigrationPlan(tuple(updates))
