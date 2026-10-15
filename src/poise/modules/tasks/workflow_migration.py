"""Domain contract for the one authorized Task workflow-snapshot migration."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import re

from ...common import digest, exact_keys
from ..backups.domain import exact_backup_name
from ..foundation.errors import DomainError
from ..workflow.domain import RouteDefinition
from .contracts import candidate_content_policy_from_metadata
from .domain import TaskStageContracts


AUTHORIZED_TASK_IDS = ("0077", "0081", "0079", "0078", "0074", "0063")
TASK_0082_AUTHORIZED_IDS = ("0082",)
CURRENT_PLANNING_TASK_IDS = ("ERP-HARNESS-COMPARISON-PLAN",)
AUTHORIZED_TASK_IDS_BY_SCHEMA = {
    "task-workflow-migration-1": AUTHORIZED_TASK_IDS,
    "task-workflow-migration-2": TASK_0082_AUTHORIZED_IDS,
    "task-workflow-migration-3": TASK_0082_AUTHORIZED_IDS,
    "task-workflow-migration-4": CURRENT_PLANNING_TASK_IDS,
}
STAGE_CONTRACT_RECOVERY_SCHEMAS = frozenset({
    "task-workflow-migration-3",
    "task-workflow-migration-4",
})
NONTERMINAL_STATUSES = frozenset({"available", "active", "verified", "accepted"})


@dataclass(frozen=True)
class MigrationRequest:
    schema: str
    request_id: str
    backup_name: str
    task_ids: tuple[str, ...]
    authorization: str
    digest: str

    @classmethod
    def parse(cls, value: object) -> "MigrationRequest":
        if isinstance(value, dict) and value.get("schema") == "task-stage-criteria-migration-1":
            return StageCriteriaMigrationRequest.parse(value)
        fields = {"schema", "request_id", "backup_name", "task_ids", "authorization"}
        exact_keys(value, fields, "task workflow migration request")
        schema = value["schema"]
        if schema not in AUTHORIZED_TASK_IDS_BY_SCHEMA:
            raise DomainError("Unsupported task workflow migration schema")
        if not isinstance(value["request_id"], str) or not value["request_id"].strip():
            raise DomainError("Task workflow migration request_id must be a nonempty string")
        if not isinstance(value["authorization"], str) or not value["authorization"].strip():
            raise DomainError("Task workflow migration authorization must be a nonempty string")
        backup_name = exact_backup_name(value["backup_name"])
        authorized_task_ids = AUTHORIZED_TASK_IDS_BY_SCHEMA[schema]
        if not isinstance(value["task_ids"], list) or tuple(value["task_ids"]) != authorized_task_ids:
            if schema == "task-workflow-migration-1":
                raise DomainError(
                    "Task workflow migration accepts exactly 0077, 0081, 0079, 0078, 0074 and 0063 in that order"
                )
            if schema == "task-workflow-migration-4":
                raise DomainError(
                    "Task workflow migration accepts exactly the current planning Task"
                )
            raise DomainError("Task workflow migration accepts exactly 0082")
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
            digest=digest(normalized),
        )


@dataclass(frozen=True)
class StageCriteriaMigrationRequest:
    """Explicit resource inventory, separate from historical migration schemas."""

    schema: str
    request_id: str
    backup_name: str
    mapping_document: str
    authorization: str
    digest: str

    @property
    def mapping(self):
        import json
        return json.loads(self.mapping_document)

    @classmethod
    def parse(cls, value):
        from ..goal_config.domain import canonical, fingerprint
        from ..workflow.domain import CriteriaAuthority, StagePurpose, text
        from .stage_criteria import StageCriteria
        exact_keys(value, {"schema", "request_id", "backup_name", "mapping", "authorization"},
                   "stage criteria migration request")
        text(value["request_id"], "migration.request_id")
        text(value["authorization"], "migration.authorization")
        backup = exact_backup_name(value["backup_name"])
        if not isinstance(value["mapping"], list) or not value["mapping"]:
            raise DomainError("Migration requires a nonempty mapping")
        identities = set()
        for resource in value["mapping"]:
            if not isinstance(resource, dict):
                raise DomainError("Migration resource must be an object")
            kind = resource.get("kind")
            if kind == "workflow_definition":
                identity_field, revision_field = "goal_type", "expected_revision"
                fields = {"kind", identity_field, revision_field, "stages"}
            elif kind in ("executable_task", "newborn_task"):
                identity_field = "task_id"
                revision_field = "expected_version" if kind == "executable_task" else "expected_revision"
                fields = {"kind", identity_field, revision_field, "expected_workflow_digest", "stages"}
            elif kind == "newborn_draft":
                identity_field, revision_field = "task_id", "expected_revision"
                fields = {"kind", identity_field, revision_field, "expected_metadata_digest", "stages"}
            else:
                raise DomainError("Unknown migration resource kind")
            exact_keys(resource, fields, "migration resource")
            text(resource[identity_field], "migration resource identity")
            if kind == "workflow_definition":
                text(resource[revision_field], "migration managed revision")
            else:
                if type(resource[revision_field]) is not int or resource[revision_field] < 0:
                    if kind == "newborn_draft":
                        raise DomainError("Migration draft revision must be a nonnegative integer")
                    raise DomainError("Migration requires a nonnegative resource version")
                if kind == "newborn_draft":
                    source_digest = resource["expected_metadata_digest"]
                    if (not isinstance(source_digest, str)
                            or re.fullmatch(r"[0-9a-f]{64}", source_digest) is None):
                        raise DomainError("Migration draft metadata digest must be 64 lowercase hexadecimal digits")
                else:
                    text(resource["expected_workflow_digest"], "migration source digest")
            identity = ("workflow" if kind == "workflow_definition" else "task", resource[identity_field])
            if identity in identities:
                raise DomainError("Duplicate migration resource")
            identities.add(identity)
            if (not isinstance(resource["stages"], list)
                    or (not resource["stages"] and kind != "newborn_draft")):
                raise DomainError("Migration requires explicit stages")
            stages = set()
            for stage in resource["stages"]:
                fields = {"stage_id", "purpose", "criteria_authority"}
                if kind not in ("workflow_definition", "newborn_draft"):
                    fields |= {"criteria", "execution"}
                exact_keys(stage, fields, "migration stage")
                text(stage["stage_id"], "migration stage identity")
                if stage["stage_id"] in stages:
                    raise DomainError("Duplicate migration stage")
                stages.add(stage["stage_id"])
                try:
                    StagePurpose(stage["purpose"])
                except (TypeError, ValueError) as exc:
                    raise DomainError("Unknown migration stage purpose") from exc
                CriteriaAuthority.parse(stage["criteria_authority"])
                if kind not in ("workflow_definition", "newborn_draft"):
                    StageCriteria.parse(stage["criteria"])
                    execution = stage["execution"]
                    exact_keys(execution, {"state", "source_revision", "source_refs"}, "migration execution")
                    if execution["state"] not in ("unstarted", "started", "unknown"):
                        raise DomainError("Invalid migration execution provenance")
                    if (execution["source_revision"] is not None and
                            (type(execution["source_revision"]) is not int or execution["source_revision"] < 0)):
                        raise DomainError("Invalid migration source revision")
                    if not isinstance(execution["source_refs"], list) or not execution["source_refs"]:
                        raise DomainError("Migration source_refs requires a list")
                    for ref in execution["source_refs"]:
                        if not isinstance(ref, dict):
                            raise DomainError("Migration source reference requires an object")
                        if ref.get("kind") == "contract":
                            exact_keys(ref, {"kind", "digest"}, "migration contract source")
                            text(ref["digest"], "migration contract digest")
                        elif ref.get("kind") in ("submission", "task_event"):
                            exact_keys(ref, {"kind", "id"}, "migration historical source")
                            if type(ref["id"]) is not int or ref["id"] < 1:
                                raise DomainError("Migration source identity requires a positive integer")
                        else:
                            raise DomainError("Unknown migration source kind")
        try:
            document = canonical(value["mapping"])
            identity = fingerprint(value)
        except (ValueError, UnicodeError, TypeError) as exc:
            raise DomainError("Migration request cannot be encoded") from exc
        return cls(value["schema"], value["request_id"], backup, document,
                   value["authorization"], identity)


@dataclass(frozen=True)
class WorkflowUpdate:
    task_id: str
    workflow: dict
    contract: dict | None
    worktree_required_added: bool
    old_workflow_digest: str
    new_workflow_digest: str
    old_contract_digest: str | None
    new_contract_digest: str | None


@dataclass(frozen=True)
class MigrationPlan:
    updates: tuple[WorkflowUpdate, ...]


class WorkflowSnapshotMigration:
    @staticmethod
    def _stage_contracts(metadata: dict, workflow: dict) -> list[dict]:
        try:
            contract = metadata["contract"]
            requirements = [
                *workflow["content_contract"]["requirements"],
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
                for stage in workflow["stages"]
            ]
        except (KeyError, TypeError) as exc:
            raise DomainError("Task stage contract source is incompatible") from exc
        route = RouteDefinition.from_workflow(workflow)
        policy = candidate_content_policy_from_metadata(
            {**metadata, "workflow": workflow},
            {
                "goal": workflow["content_contract"],
                "task": contract["content_contract"],
            },
        )
        return TaskStageContracts.parse(values, route, policy).to_list()

    @staticmethod
    def plan(request: MigrationRequest, rows: list[dict], workflows: dict[str, dict]) -> MigrationPlan:
        by_id = {row.get("id"): row for row in rows if isinstance(row, dict)}
        if len(by_id) != len(rows):
            raise DomainError("Task workflow migration targets contain duplicate or invalid Task IDs")
        missing = [task_id for task_id in request.task_ids if task_id not in by_id]
        extra = sorted(task_id for task_id in by_id if task_id not in request.task_ids)
        if missing or extra:
            raise DomainError(f"Task workflow migration target mismatch; missing {missing}; extra {extra}")

        updates = []
        for task_id in request.task_ids:
            row = by_id[task_id]
            if row.get("status") not in NONTERMINAL_STATUSES:
                raise DomainError(f"Task {task_id} is terminal or has an incompatible status")
            metadata = row.get("metadata")
            if not isinstance(metadata, dict):
                raise DomainError(f"Task {task_id} metadata is incompatible")
            contract = metadata.get("contract")
            workflow = metadata.get("workflow")
            if not isinstance(contract, dict) or not isinstance(workflow, dict):
                raise DomainError(f"Task {task_id} workflow definition is incompatible")
            goal_type = contract.get("goal_type")
            if not isinstance(goal_type, str) or not goal_type:
                raise DomainError(f"Task {task_id} immutable goal_type is incompatible")
            if workflow.get("goal_type") != goal_type:
                raise DomainError(f"Task {task_id} goal_type does not match its immutable contract")
            configured = workflows.get(goal_type)
            if not isinstance(configured, dict) or configured.get("goal_type") != goal_type:
                raise DomainError(f"Task {task_id} goal_type has no matching configured workflow")
            worktree_required = configured.get("worktree_required")
            if type(worktree_required) is not bool:
                raise DomainError(f"Task {task_id} configured worktree_required is not explicit")
            candidate = deepcopy(workflow)
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
                migrated_contract["stage_contracts"] = WorkflowSnapshotMigration._stage_contracts(
                    metadata, candidate
                )
                old_contract_digest = digest(contract)
                new_contract_digest = digest(migrated_contract)
            updates.append(
                WorkflowUpdate(
                    task_id,
                    candidate,
                    migrated_contract,
                    worktree_required_added,
                    digest(workflow),
                    digest(candidate),
                    old_contract_digest,
                    new_contract_digest,
                )
            )
        return MigrationPlan(tuple(updates))
