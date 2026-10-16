"""Editable pre-route phase of the single Task entity."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, replace

from ..foundation.errors import DomainError
from .definition import creation_fields, path_identifier
from .domain import Task, TaskState, TaskStatus
from .stage_criteria import StageCriteriaBook


REQUIREMENTS_CONTEXT_FIELDS = frozenset({"requirements_snapshot", "requirements_agreement"})


NEWBORN_FIELDS = frozenset({
    "id",
    "sprint_id",
    "goal_type",
    "goal",
    "requirements",
    "requirements_snapshot",
    "requirements_agreement",
    "definition_of_done",
    "methods",
    "method_inputs",
    "checks",
    "artifact_requirements",
    "content_contract",
    "evidence_plan",
    "executable_obligations",
    "stage_contracts",
    "decomposition",
    "planning",
})


@dataclass(frozen=True)
class NewbornTask:
    """One Task identity before its type-specific creation contract is ready."""

    task_id: str
    sprint_id: str | None
    claimed_by: str | None
    version: int
    draft: dict
    workflow: dict | None
    ready: bool
    creation_request: dict | None = None
    restart_history: tuple[dict, ...] = ()
    stage_contract_history: tuple[dict, ...] = ()
    criteria_book: StageCriteriaBook | None = None

    @classmethod
    def create(cls, task_id: str, sprint_id: str | None, actor: str | None):
        path_identifier(task_id)
        if sprint_id is not None:
            path_identifier(sprint_id)
        if actor is not None:
            path_identifier(actor)
        return cls(task_id, sprint_id, actor, 0, {}, None, False, None, (), ())

    @classmethod
    def restart(
        cls,
        task: Task,
        contract: dict,
        workflow: dict,
        sprint_id: str | None,
        actor: str,
        reason: str,
        authorization: str,
        history: list[dict],
        creation_request: dict | None,
        stage_contract_history: list[dict],
        *, recovery: dict | None = None,
    ):
        path_identifier(actor)
        if task.state.status not in (
            TaskStatus.AVAILABLE,
            TaskStatus.ACTIVE,
            TaskStatus.VERIFIED,
            TaskStatus.ACCEPTED,
        ):
            raise DomainError("Only unfinished, unintegrated Task work can restart")
        if task.state.claimed_by not in (None, actor):
            raise DomainError("Task is owned by another session; handoff is required")
        if not isinstance(reason, str) or not reason.strip():
            raise DomainError("Task restart reason is required")
        from .planning import revision_authority
        authority = revision_authority(contract, workflow, actor, authorization)
        if not isinstance(contract, dict) or contract.get("id") != task.state.task_id:
            raise DomainError("Task restart requires its exact stored contract")
        if contract.get("sprint_id") != sprint_id:
            raise DomainError("Task restart cannot change Sprint membership")
        draft = {
            key: deepcopy(value)
            for key, value in contract.items()
            if key not in {"id", "sprint_id"}
        }
        audit = {
            "authorization": authorization,
            "from_status": task.state.status.value,
            "from_version": task.state.version,
            "from_stage": task.stage.stage_id,
            "from_iteration": task.state.iteration,
            "reason": reason,
        }
        if authority is not None:
            audit["planning_revision"] = authority
        if recovery is not None:
            if not isinstance(recovery, dict) or not set(recovery) <= {
                    'local_repair', 'abandoned_check_attempt'}:
                raise DomainError('Unsupported Task restart recovery facts')
            audit.update(deepcopy(recovery))
        return cls(
            task.state.task_id,
            sprint_id,
            actor,
            task.state.version + 1,
            draft,
            deepcopy(workflow),
            False,
            deepcopy(creation_request),
            tuple(deepcopy(history)) + (audit,),
            tuple(deepcopy(stage_contract_history)),
            task.criteria_book,
        )

    def restart_draft(self, actor, reason, authorization):
        from .planning import revision_authority
        path_identifier(actor)
        if self.claimed_by not in (None, actor):
            raise DomainError("Newborn Task is owned by another session")
        if not isinstance(reason, str) or not reason.strip():
            raise DomainError("Task restart reason is required")
        # An unfinished draft has not frozen a contract; an explicit user/reviewer
        # decision is recorded without inventing a historical execution contract.
        if isinstance(authorization, dict):
            revising_frozen = self.ready and self.draft.get('planning') is not None
            revising_frozen = revising_frozen or (
                self.restart_history and 'planning_revision' in self.restart_history[-1]
            )
            permitted_keys = ({"role", "decision", "revision_fields"}
                              if revising_frozen else {"role", "decision"})
            if (set(authorization) not in ({"role", "decision"}, permitted_keys)
                    or authorization['role'] not in ('user', 'reviewer')
                    or not isinstance(authorization['decision'], str)
                    or not authorization['decision'].strip()):
                raise DomainError("Task restart authorization requires a decision")
        elif not isinstance(authorization, str) or not authorization.strip():
            raise DomainError("Task restart authorization is required")
        audit = {"authorization": deepcopy(authorization), "from_status": "newborn",
                 "from_version": self.version, "reason": reason}
        if self.ready and self.draft.get('planning') is not None:
            frozen = {'id': self.task_id, 'sprint_id': self.sprint_id, **self.draft}
            audit['planning_revision'] = revision_authority(
                frozen, self.workflow, actor, authorization)
        elif self.restart_history and 'planning_revision' in self.restart_history[-1]:
            previous = self.restart_history[-1]['planning_revision']
            # Escalation reselects authority from the original frozen contract,
            # not from a draft that may already contain proposed changes.
            audit['planning_revision'] = revision_authority(
                previous['before_contract'], previous['before_workflow'], actor, authorization)
        return replace(self, claimed_by=actor, version=self.version + 1, ready=False,
                       restart_history=self.restart_history + (audit,))

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
        workflow = metadata.get("workflow")
        goal_type = draft.get("goal_type")
        if workflow is not None and goal_type != workflow.get("goal_type"):
            raise DomainError("Newborn Task workflow must match explicit goal_type selection")
        creation_request = metadata.get("creation_request")
        if creation_request is not None and (
            not isinstance(creation_request, dict)
            or set(creation_request) != {"request_id", "digest"}
            or not all(isinstance(value, str) and value for value in creation_request.values())
        ):
            raise DomainError("Newborn Task creation request must be immutable")
        restart_history = metadata.get("restart_history", [])
        if not isinstance(restart_history, list) or any(
            not isinstance(item, dict) for item in restart_history
        ):
            raise DomainError("Newborn Task restart history must be a list of records")
        stage_contract_history = metadata.get("stage_contract_history", [])
        if not isinstance(stage_contract_history, list) or any(
            not isinstance(item, dict) for item in stage_contract_history
        ):
            raise DomainError("Newborn Task stage contract history must be a list of records")
        return cls(task_id, metadata["sprint_id"], claimed_by, version,
                   deepcopy(draft), deepcopy(workflow), ready, deepcopy(creation_request),
                   tuple(deepcopy(restart_history)),
                   tuple(deepcopy(stage_contract_history)),
                   StageCriteriaBook.from_dict(metadata['stage_criteria'])
                   if 'stage_criteria' in metadata else None)

    def edit(self, patch: dict, remove: list, workflows: dict, actor: str):
        if self.ready:
            raise DomainError("Ready newborn Task cannot be edited")
        if not isinstance(patch, dict) or not set(patch) <= NEWBORN_FIELDS | {"workflow", "workflow_changes"}:
            raise DomainError("Newborn Task edit patch requires known fields")
        if (
            not isinstance(remove, list)
            or any(not isinstance(field, str) for field in remove)
            or len(remove) != len(set(remove))
            or not set(remove) <= NEWBORN_FIELDS
        ):
            raise DomainError("Newborn Task edit remove requires unique known fields")
        if not patch and not remove:
            raise DomainError("Newborn Task edit requires patch or remove")
        patch = deepcopy(patch)
        removals = frozenset(remove)
        if removals & {'id', 'sprint_id'}:
            raise DomainError('Newborn Task identity and Sprint membership are immutable')
        conflicts = set(patch) & removals
        if conflicts:
            raise DomainError(f'Newborn Task edit patch/remove conflict: {sorted(conflicts)}')
        if 'id' in patch and patch.pop('id') != self.task_id:
            raise DomainError('Newborn Task identity is immutable')
        if 'sprint_id' in patch and patch.pop('sprint_id') != self.sprint_id:
            raise DomainError('Newborn Task Sprint membership is immutable')
        if not patch and not removals:
            raise DomainError('Newborn Task edit must change draft fields')
        from ..goal_config.domain import GoalTypeDefinition
        selected = patch.get("goal_type", self.draft.get("goal_type"))
        if "goal_type" in patch and (not isinstance(selected, str) or not selected.strip()):
            raise DomainError("Explicit goal_type must be a nonempty string")
        if "workflow" in patch and not isinstance(patch["workflow"], dict):
            raise DomainError("Explicit workflow must be a workflow definition")
        if "workflow_changes" in patch and not isinstance(patch["workflow_changes"], list):
            raise DomainError("Explicit workflow_changes must be a list")
        explicit = patch.pop("workflow", None)
        changes = patch.pop("workflow_changes", None)
        if explicit is not None:
            workflow = GoalTypeDefinition.parse(explicit).data
        elif self.workflow is not None and selected == self.draft.get("goal_type"):
            workflow = deepcopy(self.workflow)
        elif selected in workflows:
            workflow = deepcopy(workflows[selected])
        elif selected is not None:
            raise DomainError("Unknown explicit goal_type; supply a task-local workflow")
        else:
            workflow = None
        if changes is not None:
            if workflow is None:
                raise DomainError("Select a workflow before declaring workflow_changes")
            workflow = GoalTypeDefinition.build(selected, workflow, changes).data
        if "planning" in patch:
            from .planning import validate_planning
            validate_planning(patch["planning"])
        changes_goal_type = (
            self.workflow is not None
            and workflow is not None
            and self.workflow.get('goal_type') != workflow.get('goal_type')
        )
        draft = {**deepcopy(self.draft), **patch}
        if removals and workflow is None:
            raise DomainError('Select goal_type before removing draft fields')
        if workflow is not None:
            # Requirements context belongs to Task planning, not to a goal-type route.
            allowed = (creation_fields(workflow) | REQUIREMENTS_CONTEXT_FIELDS | {'planning'}) - {'id', 'sprint_id'}
            required_removals = removals & allowed
            if required_removals and "planning" not in draft:
                raise DomainError(
                    f'Cannot remove fields required by target goal_type: '
                    f'{sorted(required_removals)}'
                )
            absent = removals - set(draft)
            if absent:
                raise DomainError(
                    f'Cannot remove absent newborn draft fields: {sorted(absent)}'
                )
            for field in removals:
                draft.pop(field)
            invalid = set(draft) - allowed
            if changes_goal_type and invalid:
                raise DomainError(
                    f'Target goal_type requires explicit removal of fields: {sorted(invalid)}'
                )
        if workflow is not None and draft.get("goal_type") != workflow.get("goal_type"):
            raise DomainError("Newborn Task goal_type and workflow snapshot disagree")
        if self.claimed_by not in (None, actor):
            raise DomainError("Newborn Task is owned by another session")
        from .planning import require_revision
        require_revision(draft, workflow, self.restart_history)
        history = list(deepcopy(self.restart_history))
        if history and any(item.get('from_status') != 'newborn' for item in history):
            edited = set(patch) | set(remove)
            fields = edited & {'methods', 'checks', 'executable_obligations'}
            if fields:
                history[-1]['registry_edits'] = sorted(
                    set(history[-1].get('registry_edits', [])) | fields
                )
        return replace(self, version=self.version + 1, draft=draft, workflow=workflow,
                       claimed_by=actor, restart_history=tuple(history))

    def materialize_draft(self, draft: dict, workflows: dict, actor: str):
        """Persist a Sprint planning draft even before goal type becomes valid."""
        if not isinstance(draft, dict) or not draft or not set(draft) <= NEWBORN_FIELDS:
            raise DomainError("Sprint newborn requires a nonempty known-field draft")
        if self.claimed_by not in (None, actor):
            raise DomainError("Newborn Task is owned by another session")
        value = {**deepcopy(self.draft), **deepcopy(draft)}
        selected = value.get("goal_type")
        workflow = deepcopy(workflows[selected]) if selected in workflows else None
        return replace(
            self,
            version=self.version + 1,
            draft=value,
            workflow=workflow,
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

    def cancellation(self, actor: str, reason: str) -> TaskState:
        """Cancel only an owned planning-only identity, without inventing a route."""
        path_identifier(actor)
        if self.claimed_by != actor:
            raise DomainError("Newborn Task cancellation requires its owner")
        if not isinstance(reason, str) or not reason.strip():
            raise DomainError("Newborn Task cancellation reason is required")
        if self.sprint_id is not None:
            raise DomainError("Sprint owns cancellation of its newborn members")
        if self.restart_history:
            raise DomainError("Restarted Task requires execution-aware cancellation")
        return TaskState(self.task_id, 0, 1, TaskStatus.CANCELLED, None,
                         self.version + 1, None)

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
        if self.workflow is not None:
            value["workflow"] = deepcopy(self.workflow)
        if self.creation_request is not None:
            value["creation_request"] = deepcopy(self.creation_request)
        if self.restart_history:
            value["restart_history"] = list(deepcopy(self.restart_history))
        if self.stage_contract_history:
            value["stage_contract_history"] = list(deepcopy(self.stage_contract_history))
        if self.criteria_book is not None:
            value['stage_criteria'] = self.criteria_book.data
        return value

    def describe(self) -> dict:
        return {
            "status": "newborn",
            "task": self.task_id,
            "revision": self.version,
            "sprint": self.sprint_id,
            "claimed_by": self.claimed_by,
            "goal_type": self.draft.get("goal_type"),
            "route_entry": None if self.workflow is None else self.workflow["route"]["entry"],
            "ready": self.ready,
            "draft": deepcopy(self.draft),
            **({"workflow": deepcopy(self.workflow)} if "planning" in self.draft else {}),
        }
