"""Editable pre-route phase of the single Task entity."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, replace

from ..foundation.errors import DomainError
from .definition import path_identifier


NEWBORN_FIELDS = frozenset({
    "id",
    "sprint_id",
    "goal_type",
    "goal",
    "requirements",
    "definition_of_done",
    "methods",
    "method_inputs",
    "checks",
    "artifact_requirements",
    "content_contract",
    "evidence_plan",
    "executable_obligations",
    "stage_contracts",
})


@dataclass(frozen=True)
class NewbornTask:
    """One Task identity before its type-specific creation contract is ready."""

    task_id: str
    sprint_id: str | None
    claimed_by: str | None
    version: int
    draft: dict
    process: dict | None
    ready: bool
    creation_request: dict | None = None

    @classmethod
    def create(cls, task_id: str, sprint_id: str | None, actor: str):
        path_identifier(task_id)
        if sprint_id is not None:
            path_identifier(sprint_id)
        path_identifier(actor)
        return cls(task_id, sprint_id, actor, 0, {}, None, False, None)

    @classmethod
    def restore(cls, task_id: str, claimed_by: str | None, version: int, metadata: dict):
        newborn = metadata.get("newborn")
        if not isinstance(newborn, dict) or set(newborn) != {"draft", "ready"}:
            raise DomainError("Newborn Task requires an exact persisted draft snapshot")
        draft = newborn["draft"]
        if not isinstance(draft, dict) or not set(draft) <= NEWBORN_FIELDS:
            raise DomainError("Newborn Task draft contains unknown fields")
        ready = newborn["ready"]
        if type(ready) is not bool:
            raise DomainError("Newborn Task ready flag must be boolean")
        process = metadata.get("process")
        goal_type = draft.get("goal_type")
        if process is not None and goal_type != process.get("goal_type"):
            raise DomainError("Newborn Task process must match explicit goal_type selection")
        creation_request = metadata.get("creation_request")
        if creation_request is not None and (
            not isinstance(creation_request, dict)
            or set(creation_request) != {"request_id", "digest"}
            or not all(isinstance(value, str) and value for value in creation_request.values())
        ):
            raise DomainError("Newborn Task creation request must be immutable")
        return cls(task_id, metadata["sprint_id"], claimed_by, version,
                   deepcopy(draft), deepcopy(process), ready, deepcopy(creation_request))

    def edit(self, patch: dict, processes: dict, actor: str):
        if self.ready:
            raise DomainError("Ready newborn Task cannot be edited")
        if not isinstance(patch, dict) or not patch or not set(patch) <= NEWBORN_FIELDS:
            raise DomainError("Newborn Task edit requires a nonempty known-field patch")
        patch = deepcopy(patch)
        if 'id' in patch and patch.pop('id') != self.task_id:
            raise DomainError('Newborn Task identity is immutable')
        if 'sprint_id' in patch and patch.pop('sprint_id') != self.sprint_id:
            raise DomainError('Newborn Task Sprint membership is immutable')
        if not patch:
            raise DomainError('Newborn Task edit must change draft fields')
        if "goal_type" in patch:
            selected = patch["goal_type"]
            if not isinstance(selected, str) or selected not in processes:
                raise DomainError("Unknown explicit goal_type")
            process = deepcopy(processes[selected])
        else:
            process = self.process
        draft = {**deepcopy(self.draft), **patch}
        if process is not None and draft.get("goal_type") != process.get("goal_type"):
            raise DomainError("Newborn Task goal_type and process snapshot disagree")
        if self.claimed_by not in (None, actor):
            raise DomainError("Newborn Task is owned by another session")
        return replace(self, version=self.version + 1, draft=draft, process=process,
                       claimed_by=actor)

    def materialize_draft(self, draft: dict, processes: dict, actor: str):
        """Persist a Sprint planning draft even before goal type becomes valid."""
        if not isinstance(draft, dict) or not draft or not set(draft) <= NEWBORN_FIELDS:
            raise DomainError("Sprint newborn requires a nonempty known-field draft")
        if self.claimed_by not in (None, actor):
            raise DomainError("Newborn Task is owned by another session")
        value = {**deepcopy(self.draft), **deepcopy(draft)}
        selected = value.get("goal_type")
        process = deepcopy(processes[selected]) if selected in processes else None
        return replace(
            self,
            version=self.version + 1,
            draft=value,
            process=process,
            claimed_by=actor,
        )

    def mark_ready(self):
        if self.ready:
            return self
        return replace(self, version=self.version + 1, ready=True)

    def detach_from_sprint(self):
        if self.sprint_id is None:
            return self
        return replace(self, sprint_id=None, version=self.version + 1)

    def acquire(self, actor: str):
        path_identifier(actor)
        if self.claimed_by not in (None, actor):
            raise DomainError("Newborn Task is owned by another session")
        if self.claimed_by == actor:
            return self
        return replace(self, claimed_by=actor, version=self.version + 1)

    def release(self, actor: str):
        if self.claimed_by != actor:
            raise DomainError("Only the newborn Task owner can release it")
        return replace(self, claimed_by=None, version=self.version + 1)

    def metadata(self, config_hash: str) -> dict:
        value = {
            "sprint_id": self.sprint_id,
            "goal": self.draft.get("goal", ""),
            "config_hash": config_hash,
            "newborn": {"draft": deepcopy(self.draft), "ready": self.ready},
        }
        if self.process is not None:
            value["process"] = deepcopy(self.process)
        if self.creation_request is not None:
            value["creation_request"] = deepcopy(self.creation_request)
        return value

    def describe(self) -> dict:
        return {
            "status": "newborn",
            "task": self.task_id,
            "revision": self.version,
            "sprint": self.sprint_id,
            "claimed_by": self.claimed_by,
            "goal_type": self.draft.get("goal_type"),
            "route_entry": None if self.process is None else self.process["route"]["entry"],
            "ready": self.ready,
            "draft": deepcopy(self.draft),
        }
