from __future__ import annotations

from ..modules.foundation.errors import PoiseError
from ..modules.ownership.domain import Liveness, OwnershipChange


def _conflict(owner, liveness):
    state = liveness(owner)
    if state == Liveness.DEAD:
        return True
    if state not in (Liveness.LIVE, Liveness.UNCERTAIN):
        raise PoiseError(f"Unknown liveness result for owner {owner}")
    raise PoiseError(
        f"Owner {owner} has uncertain live status; ownership was not stolen; "
        "after a confirmed crash use show ownership_recovery and "
        "recover_ownership mode=after_crash with user authorization"
    )


def release_dependent_worktree_in(uow, actor, task_id):
    snapshot = uow.ownership.snapshot(actor)
    if (uow.ownership.worktree_required(task_id)
            and snapshot.worktree_task_id == task_id):
        uow.ownership.bind_worktree(actor, None)



def reconcile_integrated_worktree_in(uow, actor, task_id, expected_pending, verify_cleanup):
    """Release only an obsolete dependent binding, with proof and audit in one UoW.

    Integration supplies its read-only Git/filesystem proof under its publication
    lock. This owner neither changes Task results nor deletes any resource.
    """
    from ..modules.tasks.domain import TaskStatus

    if not uow.ownership.worktree_required(task_id):
        return
    observed = uow.ownership.preflight(actor, task_id)
    if observed.worktree_owner is None:
        return
    if observed.task_owner is not None:
        raise PoiseError('Terminal ownership reconciliation cannot release a claimed Task')
    task = uow.tasks.load(task_id)
    if task.state.status != TaskStatus.COMPLETED:
        raise PoiseError('Terminal ownership reconciliation requires an unclaimed completed Task')
    execution, _ = uow.execution.load(task_id)
    if execution['pending'] != expected_pending:
        raise PoiseError('Cleanup proof changed during ownership reconciliation')
    verify_cleanup(execution)
    owner = observed.worktree_owner
    release_dependent_worktree_in(uow, owner, task_id)
    uow.ownership.record_integrated_worktree_reconciliation(actor, task_id, {
        'released_owner': owner,
        'request_id': expected_pending['intent']['request_id'],
        'accepted_commit': expected_pending['accepted_commit'],
        'published_commit': expected_pending['target_after'],
        'cleanup': dict(expected_pending['cleanup']),
    })


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
                    uow.tasks.release_recovery_claim(task['id'], task['version'], task['claimed_by'],
                                                   actor, intent['request_id'], intent['reason'],
                                                   'legacy_ownership')
            return uow.ownership.reconcile_legacy(intent, actor, before)

    def recovery_snapshot(self, task_ids):
        from ..modules.ownership.domain import recovery_task_ids
        ids = recovery_task_ids(task_ids)
        with self.uow() as uow:
            snapshot = uow.ownership.crash_snapshot(ids)
        owners = {item['actor'] for item in snapshot['revoked']}
        return {**snapshot, 'observed_liveness': {
            owner: self.liveness(owner).value for owner in sorted(owners)},
            'recovery_template': {'operation': 'recover_ownership', 'input': {
                'mode': 'after_crash',
                'request_id': 'crash-' + snapshot['expected_snapshot'],
                'task_ids': ids, 'expected_snapshot': snapshot['expected_snapshot'],
                'reason': '', 'writers_stopped': False,
                'authorization': {'role': 'user', 'decision': ''}}, 'messages': []}}

    def recover_after_crash(self, actor, request):
        from ..modules.ownership.domain import parse_crash_recovery
        intent = parse_crash_recovery(request)
        with self.uow() as uow:
            receipt = uow.ownership.crash_receipt(intent['request_id'])
            if receipt is not None:
                if receipt['request'] != intent:
                    raise PoiseError('Ownership recovery request conflict')
                return {**receipt['result'], 'replayed': True}
            before = uow.ownership.crash_snapshot(sorted(intent['task_ids']))
            if before['expected_snapshot'] != intent['expected_snapshot']:
                raise PoiseError('Ownership snapshot conflict; inspect the current recovery scope')
            if not before['revoked']:
                raise PoiseError('Recovery scope has no abandoned claims')
            if any(t['status'] in ('completed', 'cancelled') for t in before['tasks']):
                raise PoiseError('Crash recovery does not change terminal Task ownership')
            owners = {item['actor'] for item in before['revoked']}
            if actor in owners:
                raise PoiseError('Use normal release for your own claims, not crash recovery')
            observed = {owner: self.liveness(owner) for owner in sorted(owners)}
            if any(state not in (Liveness.LIVE, Liveness.DEAD, Liveness.UNCERTAIN)
                   for state in observed.values()):
                raise PoiseError('Unknown liveness result during crash recovery')
            # User decision + quiescence overrides stale lifecycle observations.
            # It never fabricates SessionEnd, clears pending work or accepts a result.
            for task in before['tasks']:
                if task['claimed_by'] is not None:
                    uow.tasks.release_recovery_claim(
                        task['id'], task['version'], task['claimed_by'], actor,
                        intent['request_id'], intent['reason'], 'after_crash')
            for binding in before['worktree_bindings']:
                uow.ownership.bind_worktree(binding['id'], None)
            result = {'status': 'ownership_recovered', 'request_id': intent['request_id'],
                      'task_ids': sorted(intent['task_ids']), 'revoked': before['revoked'],
                      'replayed': False,
                      'next_action': 'bootstrap the same Task from a new session; retain pending recovery protocols'}
            uow.ownership.record_crash_recovery(intent, actor, before, observed, result)
            return result

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

    def release(self, actor, request):
        from dataclasses import asdict
        from ..modules.ownership.domain import parse_ordinary_release

        intent = parse_ordinary_release(request)
        task_id = intent['task_id']
        with self.uow() as uow:
            receipt = uow.ownership.ordinary_release_receipt(intent['request_id'])
            if receipt is not None:
                if receipt['actor'] != actor or receipt['request'] != intent:
                    raise PoiseError('Ordinary release caller or request conflict')
                return {**receipt['result'], 'replayed': True}
            before = uow.ownership.snapshot(actor)
            target = uow.ownership.preflight(actor, task_id)
            if target.task_owner != actor or before.task_id != task_id:
                raise PoiseError('Release requires the named Task owned by the current caller')
            if uow.tasks.is_newborn(task_id):
                raise PoiseError('Ordinary release requires an executable nonterminal Task')
            task = uow.tasks.load(task_id)
            if task.state.version != intent['expected_version']:
                raise PoiseError('Release version conflict; refresh the current Task version')
            if (uow.ownership.worktree_required(task_id)
                    and target.worktree_owner not in (None, actor)):
                raise PoiseError('Release rejected inconsistent foreign dependent worktree ownership')
            _release_task_in(uow, actor, task_id)
            after = uow.ownership.snapshot(actor)
            result = {'status': 'ownership_released', 'task': task_id,
                      'request_id': intent['request_id'], 'replayed': False,
                      'before': asdict(before), 'after': asdict(after)}
            uow.ownership.record_ordinary_release(intent, actor, result)
            return result

    def release_task(self, actor, task_id):
        with self.uow() as uow:
            before = uow.ownership.snapshot(actor)
            _release_task_in(uow, actor, task_id)
            return OwnershipChange(before, uow.ownership.snapshot(actor), ())


class BoundOwnership:
    def recovery_snapshot(self, task_ids):
        return self.commands.recovery_snapshot(task_ids)

    def recover(self, request):
        if 'mode' in request:
            return self.commands.recover_after_crash(self.actor, request)
        return self.recover_legacy(request)

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

    def release(self, request):
        return self.commands.release(self.actor, request)

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
