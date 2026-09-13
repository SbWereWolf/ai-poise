from __future__ import annotations

from copy import deepcopy

from ..foundation.errors import DomainError


class TaskRequirementsGate:
    """Validate live chains and persist the immutable Task-owned snapshot."""

    def __init__(self, registry_loader, snapshots, enabled):
        self._registry_loader = registry_loader
        self._snapshots = snapshots
        self._enabled = enabled

    @classmethod
    def enabled(cls, registry_loader, snapshots=None):
        if not callable(registry_loader):
            raise DomainError("Requirements registry loader must be callable")
        return cls(registry_loader, snapshots, True)

    @classmethod
    def disabled(cls):
        return cls(None, None, False)

    def prepare_contract(self, intent):
        if not isinstance(intent, dict):
            raise DomainError("Task requirements intent must be an object")
        candidate = deepcopy(intent)
        if not self._enabled:
            return candidate, None
        body = candidate.get("task") if set(candidate) == {"request_id", "task"} else candidate
        snapshot = self.validate(body)
        del body["requirements_snapshot"]
        return candidate, snapshot

    def validate(self, contract):
        if not self._enabled:
            return None
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
        planned = self._registry_loader().plan_task(snapshot["task_requirements"])
        if planned["status"] != "ready":
            raise DomainError("Task requirements snapshot contains unresolved gaps")
        if planned["snapshot"] != snapshot:
            raise DomainError("Task requirements snapshot differs from the live registry")
        texts = [item["text"] for item in snapshot["task_requirements"]]
        if contract.get("requirements") != texts:
            raise DomainError("Task requirements snapshot does not match Task requirements")
        return deepcopy(snapshot)

    def publish(self, task_id, task_requirements, agreement):
        if not self._enabled or self._snapshots is None:
            raise DomainError("Task requirements publication is not configured")
        if self._snapshots.exists(task_id):
            raise DomainError("Task requirements snapshot is immutable and already exists")
        plan = self._registry_loader().plan_task(task_requirements)
        if plan["status"] != "ready":
            raise DomainError("Task requirements chain has unresolved gaps")
        if (
            not isinstance(agreement, dict)
            or agreement.get("accepted") is not True
            or agreement.get("chains") != plan["chains"]
        ):
            raise DomainError("Explicit full-text requirements agreement is required")
        self._snapshots.create(task_id, plan["snapshot"])
        return deepcopy(plan["snapshot"])

    def snapshot(self, task_id):
        if not self._enabled or self._snapshots is None:
            raise DomainError("Task requirements snapshot storage is not configured")
        return self._snapshots.read(task_id)
