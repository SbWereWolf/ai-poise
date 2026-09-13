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


def _release_task_in(uow, actor, task_id, reason=None):
    if uow.tasks.is_newborn(task_id):
        if reason is not None:
            raise PoiseError('Newborn Task handoff requires readiness or explicit cancellation')
        uow.tasks.release_newborn(task_id, actor)
        return
    task = uow.tasks.load(task_id)
    change = task.handoff(actor, reason) if reason is not None else task.release_ownership(actor)
    uow.tasks.save(change, task.state.version)
    snapshot = uow.ownership.snapshot(actor)
    if (uow.ownership.worktree_required(task_id)
            and snapshot.worktree_task_id == task_id):
        uow.ownership.bind_worktree(actor, None)


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

    def _preflight(self, actor, task_id):
        with self.uow() as uow:
            if not uow.tasks.is_newborn(task_id):
                uow.tasks.load(task_id)._require_stage_contracts()
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

    def acquire_task(self, actor, task_id):
        self._preflight(actor, task_id)
        with self.uow() as uow:
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
    def __init__(self, commands, actor):
        self.commands = commands
        self.actor = actor

    def snapshot(self, actor):
        return self.commands.snapshot(actor)

    def acquire_task(self, task_id):
        return self.commands.acquire_task(self.actor, task_id)

    def acquire_worktree(self, task_id):
        return self.commands.acquire_worktree(self.actor, task_id)

    def release_task(self, task_id):
        return self.commands.release_task(self.actor, task_id)
