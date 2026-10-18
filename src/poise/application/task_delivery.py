"""Coordinate explicit result delivery before retiring owned working material."""
from ..modules.task_delivery.domain import DeliveryAgreement, DeliveryState, MaterialUseIntent, MaterialUseToken, identity, text
from ..modules.artifact_factory.domain import exact
from ..modules.foundation.errors import PoiseError
from uuid import uuid4


class TaskDeliveryCommands:
    def __init__(self, unit_of_work, effects, actor):
        self.uow, self.effects, self.actor = unit_of_work, effects, actor

    def state(self, task_id):
        with self.uow() as uow:
            return uow.task_delivery.read(task_id)

    def require_material_records(self, task_id, records):
        with self.uow() as uow:
            uow.tasks.load(task_id)
            registered = {item['id']: item for item in uow.artifacts.records(task_id)}
            for record in records:
                saved = registered.get(record['id'])
                if (saved is None or record['scope'] not in ('task', 'sprint') or
                        any(saved[key] != record[key] for key in ('owner', 'scope', 'digest')) or
                        (record['scope'] == 'task' and record['owner'] != task_id)):
                    raise PoiseError('Material registry owner/scope does not match selected Task')

    def query(self, task_id):
        state = self.state(task_id)
        return ({'task': task_id, 'status': 'delivery_unagreed', 'retired_artifacts': []}
                if state is None else state.result())

    def unavailable(self, task_id):
        state = self.state(task_id)
        return state is not None and state.data['retirement_started']

    def _save(self, state):
        with self.uow() as uow:
            uow.task_delivery.save(self.actor, state, state.data['revision'] - 1)
        return state

    def agree(self, args):
        agreement = DeliveryAgreement.parse(args)
        with self.uow() as uow:
            replay = uow.task_delivery.agreement_request(args['task_id'], args['request_id'], identity(args))
            if replay is not None:
                return replay
            old = uow.task_delivery.read(args['task_id'])
        record = self.effects.admission(args['task_id'], self.actor)
        version = record['revision'] if record['status'] == 'newborn' else record['version']
        if version != args['expected_version']:
            raise PoiseError('Delivery Task version changed')
        if old is not None and (old.data['retirement_started'] or record['status'] in ('completed', 'cancelled')):
            raise PoiseError('Terminal delivery agreement cannot change intent')
        if old is not None and len(record.get('restart_history', [])) <= old.data['epoch']:
            raise PoiseError('Delivery agreement intent is immutable until an authorized Task restart')
        root, root_identity, epoch = self.effects.prepare(record, args)
        state = DeliveryState.agreed(agreement, root, root_identity, epoch)
        if old is not None:
            state = DeliveryState({**state.data, 'revision': old.data['revision'] + 1})
        with self.uow() as uow:
            replay = uow.task_delivery.agreement_request(args['task_id'], args['request_id'], identity(args))
            if replay is not None:
                return replay
            current = uow.tasks.load_state(args['task_id'])
            if current.status.value != record['status']:
                raise PoiseError('Delivery Task state changed before agreement')
            if current.version != args['expected_version'] or current.claimed_by not in (None, self.actor):
                raise PoiseError('Delivery Task owner/version changed before agreement')
            uow.task_delivery.save(self.actor, state, state.data['revision'] - 1)
        return state.result()

    def _blocked(self, state, reason):
        return self._save(state.evolve(status='delivery_blocked', reason=reason)).result()

    def consumer_blocker_in(self, uow, task_id, *, material_uses=True):
        pending = uow.execution.load(task_id)[0]['pending'] if uow.execution.exists(task_id) else None
        if isinstance(pending, dict) and pending.get('kind') == 'check_attempt':
            return 'checks_pending'
        if (uow.tasks.load_state(task_id).status != 'cancelled'
                and any(d['state'] != 'finalized' for d in uow.artifacts.drafts(task_id).values())):
            return 'artifact_draft_open'
        if uow.tasks.pending_artifact_consumers(task_id):
            return 'artifact_consumer_pending'
        if material_uses and uow.task_delivery.pending_uses(task_id):
            return 'artifact_consumer_pending'
        return None

    def begin_retirement_in(self, uow, state, observed_record):
        task_id = state.data['task']
        current = uow.tasks.load_state(task_id)
        if (current.version != observed_record['version'] or current.claimed_by not in (None, self.actor)
                or current.status not in ('completed', 'cancelled')):
            raise PoiseError('Delivery Task authority/version changed before retirement')
        pending = uow.execution.load(task_id)[0]['pending'] if uow.execution.exists(task_id) else None
        if pending != observed_record.get('pending'):
            raise PoiseError('Delivery execution changed before retirement; retry settlement')
        reason = self.consumer_blocker_in(uow, task_id)
        if reason:
            return None, reason
        # Both values are canonical absolute paths under their existing owners.
        prefix = state.data['root'].rstrip('/') + '/'
        artifacts = [r['id'] for r in uow.artifacts.records(task_id) if r['path'].startswith(prefix)]
        retiring = state.evolve(status='delivery_partial', retirement_started=True, retired_artifacts=artifacts)
        uow.task_delivery.save(self.actor, retiring, state.data['revision'])
        return retiring, None

    def settle(self, args):
        exact(args, {'action', 'request_id', 'task_id'}, 'delivery settlement')
        text(args['request_id'], 'settlement request id')
        task_id = text(args['task_id'], 'Task id')
        state = self.state(task_id)
        if state is None:
            return {'task': task_id, 'status': 'delivery_blocked', 'reason': 'agreement_required'}
        if state.data['status'] == 'delivery_complete':
            return state.result(replayed=True)
        record = self.effects.admission(task_id, self.actor)
        if record['status'] not in ('completed', 'cancelled'):
            return self._blocked(state, 'task_not_terminal')
        if not state.data['retirement_started']:
            reason = self.effects.validate_sources(state)
            if reason:
                return self._blocked(state, reason)
            state = self._save(state.evolve(status='delivery_pending', reason=None))
            confirmed = list(state.data['confirmed'])
            for output in state.data['agreement']['declaration']['outputs']:
                receipt, reason = self.effects.confirm_output(output)
                if reason:
                    return self._blocked(state, reason)
                confirmed = [r for r in confirmed if r['id'] != receipt['id']] + [receipt]
                state = self._save(state.evolve(confirmed=confirmed))
            self.effects.ensure_resources(self.effects.admission(task_id, self.actor), state)
            record = self.effects.admission(task_id, self.actor)
            reason = self.effects.blocker(record, state)
            if reason:
                return self._blocked(state, reason)
            with self.uow() as uow:
                retiring, reason = self.begin_retirement_in(uow, state, record)
            if reason:
                return self._blocked(state, reason)
            state = retiring
        reason = self.effects.blocker(self.effects.admission(task_id, self.actor), state)
        if reason:
            return self._blocked(state, reason)
        for receipt in state.data['confirmed']:
            reason = self.effects.observe_output(receipt)
            if reason:
                return self._blocked(state, reason)
        self.effects.remove_owned_root(state)
        return self._save(state.evolve(status='delivery_complete', reason=None)).result()

    def admit_use_in(self, uow, intent, sources, *, permanent_sources=()):
        attempt = uuid4().hex
        live = []
        for task_id in sorted(set(sources)):
            task = uow.tasks.load_state(task_id)
            state = uow.task_delivery.read(task_id)
            if state is not None and state.data['retirement_started']:
                if task_id in permanent_sources:
                    continue
                raise PoiseError('Task working material has been retired')
            uow.task_delivery.admit_use(self.actor, intent, task_id, attempt, task.version,
                                       None if state is None else state.data['epoch'])
            live.append(task_id)
        return MaterialUseToken(intent, attempt, tuple(live))

    def complete_use_in(self, uow, token, *, accepted):
        uow.task_delivery.complete_use(self.actor, token, accepted=accepted)

    def material_admission(self, task_id, purpose):
        """Require a live material source before a new consumer reserves it.

        This observation grants no Task claim; new released consumers admit in their outcome owner.
        Reuse of retired input is separately resolved through agreed delivery.
        """
        if purpose not in {'bootstrap', 'draft', 'reuse', 'reuse_source'}:
            raise PoiseError('Unknown material consumer purpose')
        if self.unavailable(task_id) and purpose != 'reuse_source':
            raise PoiseError('Task working material has been retired')

    def resource_gate(self, task_id):
        """Keep existing resource consumers; material-only uses guard root retirement."""
        record = self.effects.admission(task_id, self.actor)
        with self.uow() as uow:
            reason = self.consumer_blocker_in(uow, task_id, material_uses=False)
        if reason:
            return reason
        state = self.state(task_id)
        if state is not None and not state.data['retirement_started']:
            return self.effects.validate_sources(state)
        return None

    def apply(self, args):
        if args.get('action') == 'agree':
            return self.agree(args)
        if args.get('action') == 'settle':
            return self.settle(args)
        raise PoiseError('Unknown delivery action')
