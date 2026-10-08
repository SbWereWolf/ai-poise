"""Coordinate explicit result delivery before retiring owned working material."""
from ..modules.task_delivery.domain import DeliveryAgreement, DeliveryState, identity, text
from ..modules.artifact_factory.domain import exact
from ..modules.foundation.errors import PoiseError


class TaskDeliveryCommands:
    def __init__(self, unit_of_work, effects, actor):
        self.uow, self.effects, self.actor = unit_of_work, effects, actor

    def state(self, task_id):
        with self.uow() as uow:
            return uow.task_delivery.read(task_id)

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
        with self.effects.locked():
            with self.uow() as uow:
                replay = uow.task_delivery.agreement_request(args['task_id'], args['request_id'], identity(args))
            if replay is not None:
                return replay
            record = self.effects.admission(args['task_id'], self.actor)
            version = record['revision'] if record['status'] == 'newborn' else record['version']
            if version != args['expected_version']:
                raise PoiseError('Delivery Task version changed')
            old = self.state(args['task_id'])
            if old is not None and (old.data['retirement_started'] or record['status'] in ('completed', 'cancelled')):
                raise PoiseError('Terminal delivery agreement cannot change intent')
            if old is not None and len(record.get('restart_history', [])) <= old.data['epoch']:
                raise PoiseError('Delivery agreement intent is immutable until an authorized Task restart')
            root, root_identity, epoch = self.effects.prepare(record, args)
            state = DeliveryState.agreed(agreement, root, root_identity, epoch)
            if old is not None:
                state = DeliveryState({**state.data, 'revision': old.data['revision'] + 1})
            with self.uow() as uow:
                current=(uow.tasks.load_newborn(args['task_id']) if uow.tasks.is_newborn(args['task_id'])
                         else uow.tasks.load(args['task_id']).state)
                if current.version != args['expected_version'] or current.claimed_by not in (None,self.actor):
                    raise PoiseError('Delivery Task owner/version changed before agreement')
                uow.task_delivery.save(self.actor,state,state.data['revision']-1)
            return state.result()

    def _blocked(self, state, reason):
        return self._save(state.evolve(status='delivery_blocked', reason=reason)).result()

    def settle(self, args):
        exact(args, {'action', 'request_id', 'task_id'}, 'delivery settlement')
        text(args['request_id'], 'settlement request id')
        task_id = text(args['task_id'], 'Task id')
        with self.effects.locked():
            state = self.state(task_id)
            if state is None:
                return {'task': task_id, 'status': 'delivery_blocked', 'reason': 'agreement_required'}
            if state.data['status'] == 'delivery_complete':
                return state.result(replayed=True)
            record = self.effects.admission(task_id, self.actor)
            reason = self.effects.consumer_blocker(record)
            if reason:
                return self._blocked(state, reason)
            if not state.data['retirement_started']:
                reason = self.effects.validate_sources(state)
                if reason:
                    return self._blocked(state, reason)
            self.effects.ensure_resources(record, state)
            record = self.effects.admission(task_id, self.actor)
            reason = self.effects.blocker(record, state)
            if reason:
                return self._blocked(state, reason)
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
                # Refresh every consumer and owner before durable deletion permission.
                reason = self.effects.blocker(self.effects.admission(task_id, self.actor), state)
                if reason:
                    return self._blocked(state, reason)
                artifacts = self.effects.retiring_artifacts(state)
                state = self._save(state.evolve(status='delivery_partial', retirement_started=True,
                                                retired_artifacts=artifacts))
            self.effects.remove_owned_root(state)
            return self._save(state.evolve(status='delivery_complete', reason=None)).result()

    def material_admission(self, task_id, purpose):
        """Require a live material source before a new consumer reserves it.

        The caller holds the shared material scope. This grants no Task claim.
        Reuse of retired input is separately resolved through agreed delivery.
        """
        if purpose not in {'bootstrap', 'handoff', 'draft', 'reuse', 'reuse_source', 'export'}:
            raise PoiseError('Unknown material consumer purpose')
        if self.unavailable(task_id) and purpose != 'reuse_source':
            raise PoiseError('Task working material has been retired')
        if purpose != 'export' and self.effects.preparing_consumer(task_id):
            raise PoiseError('transfer_preparing: finish the original export preparation first')

    def resource_gate(self, task_id):
        """Called inside the shared material scope before resource effects."""
        record = self.effects.admission(task_id, self.actor)
        reason = self.effects.consumer_blocker(record)
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
