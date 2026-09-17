from __future__ import annotations

from copy import deepcopy

from ..foundation.errors import DomainError


class TaskRequirementsGate:
    """Validate live chains and persist the immutable Task-owned snapshot."""

    def __init__(self, registry_loader, snapshots):
        self._registry_loader = registry_loader
        self._snapshots = snapshots

    @classmethod
    def enabled(cls, registry_loader, snapshots=None):
        if not callable(registry_loader):
            raise DomainError("Requirements registry loader must be callable")
        return cls(registry_loader, snapshots)

    def prepare_contract(self, intent):
        candidate, body = self._candidate(intent)
        snapshot, plan = self._validate_snapshot(body, self._registry_loader(), "live registry")
        agreement = deepcopy(body.get("requirements_agreement"))
        self._validate_agreement(agreement, plan)
        return self._prepared(candidate, body, snapshot, agreement)

    def prepare_restarted_contract(self, intent, persisted_snapshot, persisted_agreement):
        """Validate only the immutable context selected by the Task restart/replay owner."""
        candidate, body = self._candidate(intent)
        if (
            body.get("requirements_snapshot") != persisted_snapshot
            or body.get("requirements_agreement") != persisted_agreement
        ):
            raise DomainError("Restarted Task Requirements context differs from persisted state")
        snapshot, plan = self._validate_snapshot(
            body,
            self._snapshot_registry(persisted_snapshot),
            "persisted Task",
        )
        agreement = deepcopy(body.get("requirements_agreement"))
        self._validate_agreement(agreement, plan)
        return self._prepared(candidate, body, snapshot, agreement)

    @staticmethod
    def _candidate(intent):
        if not isinstance(intent, dict):
            raise DomainError("Task requirements intent must be an object")
        candidate = deepcopy(intent)
        body = candidate.get("task") if set(candidate) == {"request_id", "task"} else candidate
        if not isinstance(body, dict):
            raise DomainError("Task requirements contract must be an object")
        return candidate, body

    @staticmethod
    def _prepared(candidate, body, snapshot, agreement):
        del body["requirements_snapshot"]
        del body["requirements_agreement"]
        return candidate, {"snapshot": snapshot, "agreement": agreement}

    @staticmethod
    def _snapshot_registry(snapshot):
        from .domain import RequirementsRegistry
        if not isinstance(snapshot, dict) or set(snapshot) != {
            "requirements",
            "links",
            "task_requirements",
        }:
            raise DomainError("Task requirements snapshot has an invalid shape")
        requirements = snapshot["requirements"]
        if not isinstance(requirements, dict):
            raise DomainError("Task requirements snapshot has invalid requirements")
        return RequirementsRegistry.restore(list(requirements.values()), snapshot["links"])

    @staticmethod
    def _validate_agreement(agreement, plan):
        if (
            not isinstance(agreement, dict)
            or set(agreement) != {"accepted", "chains"}
            or agreement["accepted"] is not True
            or agreement["chains"] != plan["chains"]
        ):
            raise DomainError("Explicit full-text requirements agreement is required")

    def validate(self, contract):
        snapshot, _ = self._validate_snapshot(contract, self._registry_loader(), "live registry")
        return snapshot

    @staticmethod
    def _validate_snapshot(contract, registry, source):
        if not isinstance(contract, dict):
            raise DomainError("Task requirements contract must be an object")
        if "requirements_snapshot" not in contract:
            raise DomainError("Task requirements_snapshot is required")
        snapshot = deepcopy(contract["requirements_snapshot"])
        if not isinstance(snapshot, dict) or set(snapshot) != {
            "requirements",
            "links",
            "task_requirements",
        }:
            raise DomainError("Task requirements snapshot has an invalid shape")
        planned = registry.plan_task(snapshot["task_requirements"])
        if planned["status"] != "ready":
            raise DomainError("Task requirements snapshot contains unresolved gaps")
        if planned["snapshot"] != snapshot:
            raise DomainError(f"Task requirements snapshot differs from the {source}")
        texts = [item["text"] for item in snapshot["task_requirements"]]
        if contract.get("requirements") != texts:
            raise DomainError("Task requirements snapshot does not match Task requirements")
        return deepcopy(snapshot), planned

    def publish(self, task_id, task_requirements, agreement):
        if self._snapshots is None:
            raise DomainError("Task requirements publication is not configured")
        if self._snapshots.exists(task_id):
            raise DomainError("Task requirements snapshot is immutable and already exists")
        plan = self._registry_loader().plan_task(task_requirements)
        if plan["status"] != "ready":
            raise DomainError("Task requirements chain has unresolved gaps")
        self._validate_agreement(agreement, plan)
        self._snapshots.create(task_id, plan["snapshot"], agreement)
        return deepcopy(plan["snapshot"])

    def publish_created(self, uow, task_id, context):
        if context is None:
            raise DomainError("Task publication requires Requirements agreement context")
        stored = uow.tasks.restart_context(task_id)
        if (
            stored.get("requirements_snapshot") is None
            and stored.get("requirements_agreement") is None
        ):
            uow.tasks.publish_requirements_context(
                task_id, context["snapshot"], context["agreement"]
            )
            stored = uow.tasks.restart_context(task_id)
        if (
            stored.get("requirements_snapshot") != context["snapshot"]
            or stored.get("requirements_agreement") != context["agreement"]
        ):
            raise DomainError("Task publication did not persist its Requirements agreement")

    def snapshot(self, task_id):
        if self._snapshots is None:
            raise DomainError("Task requirements snapshot storage is not configured")
        return self._snapshots.read(task_id)
