from __future__ import annotations
from copy import deepcopy
from dataclasses import dataclass

from ..modules.foundation.errors import PoiseError, VersionConflict
from ..modules.ownership.domain import Liveness, OwnershipChange
from ..modules.tasks.ports import TaskReadSet, TaskSourcesChanged


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



def _reconciliation_conflict(condition):
    raise PoiseError(
        f'Terminal ownership reconciliation {condition}; repeat the saved integrate request.'
    )


def reconcile_integrated_worktree(unit_of_work, actor, task_id, expected_pending, verify_cleanup):
    """Keep terminal proof detached and recheck its authority before atomic release."""
    facade = unit_of_work()

    def capture():
        with unit_of_work() as uow:
            observed = uow.ownership.preflight(actor, task_id)
        actors = ((actor,) if observed.worktree_owner in (None, actor)
                  else (actor, observed.worktree_owner))
        inputs = facade.capture(TaskReadSet((task_id,), actors=actors, execution_ids=(task_id,)))
        return observed, inputs

    observed, inputs = capture()
    if not inputs.worktree_required(task_id) or observed.worktree_owner is None:
        return
    if observed.task_owner is not None:
        _reconciliation_conflict('cannot release a claimed Task')
    task = inputs.task(task_id)
    if task.state.status != 'completed':
        _reconciliation_conflict('requires an unclaimed completed Task')
    execution, version = inputs.required_execution(task_id)
    if execution['pending'] != expected_pending:
        _reconciliation_conflict('cleanup proof changed')
    owner = observed.worktree_owner
    try:
        verify_cleanup(deepcopy(execution))
    except PoiseError as exc:
        raise PoiseError(
            f'Terminal ownership reconciliation: {exc}; repeat the saved '
            'integrate request after resolving the reported conflict.'
        ) from exc

    observed, current = capture()
    if not current.worktree_required(task_id) or observed.worktree_owner is None:
        return
    if observed.worktree_owner != owner:
        _reconciliation_conflict('owner changed')
    if observed.task_owner is not None:
        _reconciliation_conflict('cannot release a claimed Task')
    current_task = current.task(task_id)
    if current_task.state.status != 'completed':
        _reconciliation_conflict('requires an unclaimed completed Task')
    current_execution, current_version = current.required_execution(task_id)
    if current_execution['pending'] != execution['pending']:
        _reconciliation_conflict('cleanup proof changed')
    if current_execution['last_report'] != execution['last_report']:
        _reconciliation_conflict('accepted result changed')
    if (current_execution['worktree'] != execution['worktree']
            or current_execution['branch'] != execution['branch']):
        _reconciliation_conflict('cleanup identity changed')
    if current_task.state.version != task.state.version:
        _reconciliation_conflict('Task version changed')
    if current_version != version:
        _reconciliation_conflict('execution version changed')
    proof = facade.prepare_ownership_reconciliation({
        'released_owner':owner, 'request_id':expected_pending['intent']['request_id'],
        'accepted_commit':expected_pending['accepted_commit'],
        'published_commit':expected_pending['target_after'], 'cleanup':dict(expected_pending['cleanup'])})
    with unit_of_work() as uow:
        # A binding that became independent or moved away is preserved as a no-op.
        if not uow.ownership.worktree_required(task_id):
            return
        latest = uow.ownership.preflight(actor, task_id)
        if latest.worktree_owner is None:
            return
        if latest.worktree_owner != owner:
            _reconciliation_conflict('owner changed')
        if latest.task_owner is not None:
            _reconciliation_conflict('cannot release a claimed Task')
        try:
            uow.require_inputs(current)
        except TaskSourcesChanged as exc:
            raise PoiseError(
                'Terminal ownership reconciliation basis changed; '
                'repeat the saved integrate request.'
            ) from exc
        if current.ownership(owner).worktree_task_id == task_id:
            uow.ownership.bind_worktree(owner, None)
        uow.ownership.record_prepared_reconciliation(actor, task_id, proof)


def _release_task_in(uow, actor, task_id):
    if uow.tasks.is_newborn(task_id):
        uow.tasks.release_newborn(task_id, actor)
        release_dependent_worktree_in(uow, actor, task_id)
        return
    task = uow.tasks.load(task_id)
    change = task.release_ownership(actor)
    uow.tasks.save(change, task.state.version)
    release_dependent_worktree_in(uow, actor, task_id)


def release_task_in(uow, actor, task_id):
    _release_task_in(uow, actor, task_id)


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
        inputs = self.uow().capture(TaskReadSet((task_id,), review_ids=(task_id,)))
        if inputs.state(task_id).status != 'newborn':
            from .tasks import require_reviewer_identity
            task = inputs.task(task_id)
            task._require_stage_contracts()
            require_reviewer_identity(inputs, task, actor, acquiring=True)
        with self.uow() as uow:
            observed = uow.ownership.preflight(actor, task_id)
        self.after_preflight(observed)
        return observed

    def acquire_task(self, actor, task_id, *, force_duplicate_start=False,
                     expected_newborn_revision=None):
        return self._acquire(actor, task_id, True, force_duplicate_start,
                             expected_newborn_revision)

    def acquire_worktree(self, actor, task_id):
        return self._acquire(actor, task_id, False, False, None)

    def _acquire(self, actor, task_id, claim_task, force_duplicate_start, expected_newborn_revision):
        self._preflight(actor, task_id)
        facade = self.uow()
        with self.uow() as uow:
            target = uow.ownership.preflight(actor, task_id)
        foreign = tuple(sorted({owner for owner in (target.task_owner, target.worktree_owner)
                                if owner not in (None, actor)}))
        inputs = facade.capture(TaskReadSet((task_id,), actors=(actor, *foreign),
            review_ids=(task_id,), family_ids=(task_id,) if claim_task else ()))
        state = inputs.state(task_id)
        newborn = state.status == 'newborn'
        if claim_task and expected_newborn_revision is not None:
            if type(expected_newborn_revision) is not int or not newborn:
                raise PoiseError('Newborn Task changed before acquisition')
            if state.version != expected_newborn_revision:
                raise PoiseError('Newborn Task version changed before acquisition')
        if state.claimed_by != target.task_owner:
            raise VersionConflict('Ownership changed before preparation; repeat the request')
        if not newborn:
            from .tasks import require_reviewer_identity
            task = inputs.task(task_id)
            require_reviewer_identity(inputs, task, actor, acquiring=True)
            if claim_task:
                from .duplicate_tasks import require_duplicate_start
                require_duplicate_start(inputs, task_id, actor,
                    force_duplicate_start=force_duplicate_start)
        required = inputs.worktree_required(task_id)
        before = inputs.ownership(actor)
        recovered = set()
        owners = (target.task_owner, target.worktree_owner) if claim_task else (target.worktree_owner,)
        for owner in owners:
            if owner not in (None, actor) and owner not in recovered:
                _conflict(owner, self.liveness)
                recovered.add(owner)
        release = prepare_actor_release(facade, inputs, actor, task_id) if claim_task else None
        changes = []
        newborn_changes = []
        current = inputs.newborn(task_id) if newborn else task
        for owner in sorted(recovered):
            if inputs.ownership(owner).task_id == task_id:
                if newborn:
                    released = current.release(owner)
                    newborn_changes.append((released, current.version, 'ownership_released'))
                    current = released
                else:
                    change = current.release_ownership(owner)
                    changes.append(change)
                    current = change.task
        if claim_task and (current.claimed_by if newborn else current.state.claimed_by) is None:
            if newborn:
                acquired = current.acquire(actor)
                newborn_changes.append((acquired, current.version, 'ownership_acquired'))
            else:
                changes.append(current.acquire_ownership(actor))
        writes = facade.prepare_claim_changes(inputs, tuple(changes)) if changes else ()
        try:
            with self.uow() as uow:
                uow.require_inputs(inputs)
                if uow.ownership.preflight(actor, task_id) != target:
                    raise VersionConflict('Ownership changed during preparation; repeat the request')
                if release is not None:
                    release.save(uow)
                for value, version, event in newborn_changes:
                    uow.tasks.save_newborn_ownership(value, version, event)
                for write in writes:
                    uow.tasks.save_prepared(write)
                for owner in sorted(recovered):
                    snapshot = inputs.ownership(owner)
                    if (snapshot.worktree_task_id == task_id and
                            (snapshot.task_id != task_id or required)):
                        uow.ownership.bind_worktree(owner, None)
                if not claim_task or required:
                    uow.ownership.bind_worktree(actor, task_id)
                elif before.worktree_task_id is None:
                    uow.ownership.bind_worktree(actor, None)
                after = uow.ownership.snapshot(actor)
        except TaskSourcesChanged as conflict:
            if claim_task and not newborn:
                require_duplicate_start(conflict.current, task_id, actor,
                    force_duplicate_start=force_duplicate_start)
            raise
        return OwnershipChange(before, after, tuple(sorted(recovered)))

    @staticmethod
    def _release_replay(raw, actor, intent):
        import json
        receipt = json.loads(raw)
        if receipt['actor'] != actor or receipt['request'] != intent:
            raise PoiseError('Ordinary release caller or request conflict')
        return {**receipt['result'], 'replayed': True}

    def release(self, actor, request):
        from dataclasses import asdict, replace
        from ..modules.ownership.domain import parse_ordinary_release
        intent = parse_ordinary_release(request)
        task_id = intent['task_id']
        facade = self.uow()
        with self.uow() as uow:
            receipt = uow.ownership.ordinary_release_receipt_raw(intent['request_id'])
        if receipt is not None:
            return self._release_replay(receipt, actor, intent)
        inputs = facade.capture(TaskReadSet((task_id,), actors=(actor,), cycle_releases=((actor, task_id),)))
        before = inputs.ownership(actor)
        with self.uow() as uow:
            target = uow.ownership.preflight(actor, task_id)
        if target.task_owner != actor or before.task_id != task_id:
            raise PoiseError('Release requires the named Task owned by the current caller')
        if inputs.state(task_id).version != intent['expected_version']:
            raise PoiseError('Release version conflict; refresh the current Task version')
        required = inputs.worktree_required(task_id)
        if required and target.worktree_owner not in (None, actor):
            raise PoiseError('Release rejected inconsistent foreign dependent worktree ownership')
        release = prepare_actor_release(facade, inputs, actor, None)
        after = replace(before, task_id=None,
            worktree_task_id=None if release.release_worktree else before.worktree_task_id)
        result = {'status':'ownership_released', 'task':task_id,
            'request_id':intent['request_id'], 'replayed':False,
            'before':asdict(before), 'after':asdict(after)}
        encoded = facade.prepare_release_receipt(intent, actor, result)
        cycle = facade.prepare_cycle_release(inputs, actor, task_id)
        with self.uow() as uow:
            receipt = uow.ownership.ordinary_release_receipt_raw(intent['request_id'])
            if receipt is None:
                uow.require_inputs(inputs)
                if uow.ownership.preflight(actor, task_id) != target:
                    raise VersionConflict('Release ownership changed; repeat the request')
                uow.accounting_cycles.release_prepared(cycle)
                release.save(uow)
                uow.ownership.record_prepared_release(task_id, actor, encoded)
        return result if receipt is None else self._release_replay(receipt, actor, intent)

    def release_task(self, actor, task_id):
        facade = self.uow()
        inputs = facade.capture(TaskReadSet((task_id,), actors=(actor,)))
        before = inputs.ownership(actor)
        release = (prepare_actor_release(facade, inputs, actor, None)
                   if before.task_id == task_id else None)
        # Explicit release validates the named Task even when no claim remains.
        if inputs.state(task_id).status == 'newborn':
            inputs.newborn(task_id).release(actor)
        else:
            inputs.task(task_id).release_ownership(actor)
        with self.uow() as uow:
            uow.require_inputs(inputs)
            if release is not None:
                release.save(uow)
            if (inputs.worktree_required(task_id) and before.worktree_task_id == task_id):
                uow.ownership.bind_worktree(actor, None)
            after = uow.ownership.snapshot(actor)
        return OwnershipChange(before, after, ())


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

    def acquire_task(self, task_id, *, force_duplicate_start=False,
                     expected_newborn_revision=None):
        admission = {}
        if expected_newborn_revision is not None:
            admission['expected_newborn_revision'] = expected_newborn_revision
        return self.commands.acquire_task(self.actor, task_id,
            force_duplicate_start=force_duplicate_start, **admission)

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
    require_reuse_ownership(before, target, required, task_id, actor)
    uow.tasks.save(change, task.state.version)
    if required:
        uow.ownership.bind_worktree(actor, task_id)


def require_reuse_ownership(before, target, required, task_id, actor):
    if before.task_id not in (None, task_id):
        raise PoiseError("Reuse caller already owns another Task")
    if target.task_owner not in (None, actor):
        raise PoiseError("Reuse cannot take foreign Task ownership")
    if required and (target.worktree_owner not in (None, actor)
                     or before.worktree_task_id not in (None, task_id)):
        raise PoiseError("Reuse cannot replace independent or foreign worktree ownership")


@dataclass(frozen=True)
class PreparedClaimRelease:
    actor: str
    task_id: str
    write: object | None
    newborn: object | None
    release_worktree: bool
    expected_version: int

    def save(self, uow):
        if self.newborn is not None:
            uow.tasks.save_newborn_ownership(
                self.newborn, self.expected_version, 'ownership_released')
        else:
            uow.tasks.save_prepared(self.write)
        if self.release_worktree:
            uow.ownership.bind_worktree(self.actor, None)


def prepare_actor_release(facade, inputs, actor, target):
    snapshot = inputs.ownership(actor)
    task_id = snapshot.task_id
    if task_id in (None, target):
        return None
    # Ownership selection is captured atomically with this Task's raw state.
    from ..modules.tasks.domain import TaskStatus
    state = inputs.state(task_id)
    if state.status == TaskStatus.NEWBORN:
        newborn = inputs.newborn(task_id).release(actor)
        write = None
    else:
        task = inputs.task(task_id)
        write = facade.prepare_change(inputs, task.release_ownership(actor))
        newborn = None
    return PreparedClaimRelease(actor, task_id, write, newborn,
        inputs.worktree_required(task_id) and snapshot.worktree_task_id == task_id, state.version)
