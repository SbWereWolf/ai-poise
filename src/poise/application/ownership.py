from __future__ import annotations

from ..modules.foundation.errors import PoiseError
from ..modules.ownership.domain import Liveness, OwnershipChange


def _conflict(owner, liveness):
    state = liveness(owner)
    if state == Liveness.DEAD:
        return True
    if state not in (Liveness.LIVE, Liveness.UNCERTAIN):
        raise PoiseError(f"Unknown liveness result for owner {owner}")
    raise PoiseError(f"Owner {owner} has uncertain live status; ownership was not stolen")


def release_dependent_worktree_in(uow, actor, task_id):
    snapshot = uow.ownership.snapshot(actor)
    if (uow.ownership.worktree_required(task_id)
            and snapshot.worktree_task_id == task_id):
        uow.ownership.bind_worktree(actor, None)


def _release_task_in(uow, actor, task_id, reason=None):
    if uow.tasks.is_newborn(task_id):
        if reason is not None and (not isinstance(reason, str) or not reason.strip()):
            raise PoiseError('Newborn Task handoff reason required')
        uow.tasks.release_newborn(task_id, actor)
        release_dependent_worktree_in(uow, actor, task_id)
        return
    task = uow.tasks.load(task_id)
    change = task.handoff(actor, reason) if reason is not None else task.release_ownership(actor)
    uow.tasks.save(change, task.state.version)
    release_dependent_worktree_in(uow, actor, task_id)


def release_task_in(uow, actor, task_id, reason=None):
    _release_task_in(uow, actor, task_id, reason)


class OwnershipCommands:
    def __init__(self, unit_of_work, liveness, after_preflight):
        self.uow = unit_of_work
        self.liveness = liveness
        self.after_preflight = after_preflight

    def snapshot(self, actor):
        with self.uow() as uow:
            return uow.ownership.snapshot(actor)

    def conflicts(self):
        with self.uow() as uow:
            return uow.ownership.conflicts()

    def recover_legacy(self, actor, request):
        from ..modules.ownership.domain import parse_legacy_repair, legacy_releases
        intent = parse_legacy_repair(request)
        with self.uow() as uow:
            receipt = uow.ownership.legacy_receipt(intent['request_id'])
            if receipt is not None:
                if receipt['request'] != intent:
                    raise PoiseError('Ownership recovery request conflict')
                return {**receipt['result'], 'replayed': True}
            components = uow.ownership.conflicts()['conflicts']
            before = next((c for c in components if c['task_ids'] == sorted(intent['task_ids'])), None)
            if before is None or before['expected_snapshot'] != intent['expected_snapshot']:
                raise PoiseError('Ownership snapshot conflict; read the exact current component')
            released = legacy_releases(intent, before)
            for owner in sorted(released):
                if owner != actor:
                    _conflict(owner, self.liveness)
            for task in before['tasks']:
                if task['claimed_by'] is not None and intent['task_claims'][task['id']] is None:
                    uow.tasks.release_legacy_claim(task['id'], task['version'], task['claimed_by'],
                                                   actor, intent['request_id'], intent['reason'])
            return uow.ownership.reconcile_legacy(intent, actor, before)

    def _preflight(self, actor, task_id):
        with self.uow() as uow:
            if not uow.tasks.is_newborn(task_id):
                from .tasks import require_reviewer_identity_in
                task = uow.tasks.load(task_id)
                task._require_stage_contracts()
                require_reviewer_identity_in(uow, task, actor, acquiring=True)
            observed = uow.ownership.preflight(actor, task_id)
        self.after_preflight(observed)
        return observed

    def _recover(self, uow, owner, task_id, recovered):
        if owner is None or owner in recovered:
            return
        if not _conflict(owner, self.liveness):
            return
        snapshot = uow.ownership.snapshot(owner)
        if snapshot.task_id == task_id:
            _release_task_in(uow, owner, task_id)
        elif snapshot.worktree_task_id == task_id:
            uow.ownership.bind_worktree(owner, None)
        recovered.add(owner)

    def acquire_task(self, actor, task_id, *, force_duplicate_start=False):
        self._preflight(actor, task_id)
        with self.uow() as uow:
            if not uow.tasks.is_newborn(task_id):
                from .tasks import require_reviewer_identity_in
                require_reviewer_identity_in(uow, uow.tasks.load(task_id), actor, acquiring=True)
                from .duplicate_tasks import require_duplicate_start_in
                require_duplicate_start_in(uow, task_id, actor,
                    force_duplicate_start=force_duplicate_start)
            before = uow.ownership.snapshot(actor)
            target = uow.ownership.preflight(actor, task_id)
            recovered = set()
            for owner in (target.task_owner, target.worktree_owner):
                if owner not in (None, actor):
                    self._recover(uow, owner, task_id, recovered)
            target = uow.ownership.preflight(actor, task_id)
            if target.task_owner not in (None, actor):
                _conflict(target.task_owner, self.liveness)
            required = uow.ownership.worktree_required(task_id)
            if required and target.worktree_owner not in (None, actor):
                _conflict(target.worktree_owner, self.liveness)
            if before.task_id not in (None, task_id):
                _release_task_in(uow, actor, before.task_id)
            current = uow.ownership.preflight(actor, task_id)
            if current.task_owner is None:
                if uow.tasks.is_newborn(task_id):
                    uow.tasks.acquire_newborn(task_id, actor)
                else:
                    task = uow.tasks.load(task_id)
                    uow.tasks.save(task.acquire_ownership(actor), task.state.version)
            if required:
                uow.ownership.bind_worktree(actor, task_id)
            elif before.worktree_task_id is None:
                uow.ownership.bind_worktree(actor, None)
            after = uow.ownership.snapshot(actor)
            return OwnershipChange(before, after, tuple(sorted(recovered)))

    def acquire_worktree(self, actor, task_id):
        self._preflight(actor, task_id)
        with self.uow() as uow:
            before = uow.ownership.snapshot(actor)
            target = uow.ownership.preflight(actor, task_id)
            recovered = set()
            if target.worktree_owner not in (None, actor):
                self._recover(uow, target.worktree_owner, task_id, recovered)
            target = uow.ownership.preflight(actor, task_id)
            if target.worktree_owner not in (None, actor):
                _conflict(target.worktree_owner, self.liveness)
            uow.ownership.bind_worktree(actor, task_id)
            return OwnershipChange(before, uow.ownership.snapshot(actor), tuple(sorted(recovered)))

    def release_task(self, actor, task_id):
        with self.uow() as uow:
            before = uow.ownership.snapshot(actor)
            _release_task_in(uow, actor, task_id)
            return OwnershipChange(before, uow.ownership.snapshot(actor), ())


class BoundOwnership:
    def conflicts(self):
        return self.commands.conflicts()

    def recover_legacy(self, request):
        return self.commands.recover_legacy(self.actor, request)

    def __init__(self, commands, actor):
        self.commands = commands
        self.actor = actor

    def snapshot(self, actor):
        return self.commands.snapshot(actor)

    def acquire_task(self, task_id, *, force_duplicate_start=False):
        return self.commands.acquire_task(self.actor, task_id,
            force_duplicate_start=force_duplicate_start)

    def acquire_worktree(self, task_id):
        return self.commands.acquire_worktree(self.actor, task_id)

    def release_task(self, task_id):
        return self.commands.release_task(self.actor, task_id)


def reserve_reuse_in(uow, task, change, actor):
    """Acquire only the current verification scope, preserving all foreign ownership."""
    task_id = task.state.task_id
    before = uow.ownership.snapshot(actor)
    target = uow.ownership.preflight(actor, task_id)
    required = uow.ownership.worktree_required(task_id)
    if before.task_id not in (None, task_id):
        raise PoiseError("Reuse caller already owns another Task")
    if target.task_owner not in (None, actor):
        raise PoiseError("Reuse cannot take foreign Task ownership")
    if required and (target.worktree_owner not in (None, actor)
                     or before.worktree_task_id not in (None, task_id)):
        raise PoiseError("Reuse cannot replace independent or foreign worktree ownership")
    uow.tasks.save(change, task.state.version)
    if required:
        uow.ownership.bind_worktree(actor, task_id)
