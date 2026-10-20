"""Atomic ownership use cases composed inside the existing apply transaction."""
from apps.sequencing.domain.ownership import (
    require_different_target, require_handoff, require_same_intent, require_source, require_target,
)


class OwnershipCommands:
    def __init__(self, repository, availability, allocate_id, now):
        self.repository = repository
        self.availability = availability
        self.allocate_id = allocate_id
        self.now = now

    def execute(self, operation):
        kind = operation['kind']
        if kind in ('claim_task', 'switch_claim'):
            intent = {key: value for key, value in operation.items() if key != 'id'}
            saved = self.repository.inner_intent(operation['claim_request_id'])
            if saved is not None:
                require_same_intent(saved['intent'], intent)
                return {**saved['result'], 'changed': False, 'claim_replayed': True}
        return getattr(self, kind)(operation)

    def _target(self, task_id, claimant, exempt_task_id=None):
        task = self.repository.task(task_id)
        require_target(task['state'], self.repository.active_for_task(task_id),
                       self.availability(task_id, claimant),
                       self.repository.active_for_session(claimant), exempt_task_id)
        return task

    def _create(self, task_id, claimant):
        claim_id = self.allocate_id()
        self.repository.create_claim(claim_id, task_id, claimant)
        return {'changed': True, 'task_id': task_id, 'claim_id': claim_id,
                'claimant': claimant, 'claim_replayed': False}

    def _save_intent(self, operation, result, event):
        self.repository.record(operation['task_id'], operation['claimant'], event, {
            'claim_request_id': operation['claim_request_id'],
            'intent': {key: value for key, value in operation.items() if key != 'id'},
            'result': result}, self.now())

    def _release(self, task, claim, actor, reason, event, extra=None):
        workspace_id = self.repository.current_workspace_id(task['task_id'])
        handoff = {'handoff_id': self.allocate_id(), 'task_id': task['task_id'],
                   'previous_claim_id': claim['claim_id'], **task['reference'],
                   'workspace_id': workspace_id}
        self.repository.release_claim(claim['claim_id'], task['task_id'])
        self.repository.record(task['task_id'], actor, event,
            {'handoff': handoff, 'previous_claim': claim, 'reason': reason, **(extra or {})},
            self.now(), workspace_id)
        return {'changed': True, 'task_id': task['task_id'], 'claim_id': claim['claim_id'],
                'handoff_id': handoff['handoff_id']}

    def claim_task(self, operation):
        self._target(operation['task_id'], operation['claimant'])
        result = self._create(operation['task_id'], operation['claimant'])
        self._save_intent(operation, result, 'claim.created')
        return result

    def switch_claim(self, operation):
        source = self.repository.task(operation['from_task_id'])
        claim = self.repository.active_for_task(source['task_id'])
        require_source(source['task_id'], operation['from_claim_id'], operation['claimant'], claim)
        require_different_target(source['task_id'], operation['task_id'])
        self._target(operation['task_id'], operation['claimant'], source['task_id'])
        released = self._release(source, claim, operation['claimant'], operation['reason'], 'claim.released')
        result = {**self._create(operation['task_id'], operation['claimant']),
                  'previous_task_id': source['task_id'], 'previous_claim_id': claim['claim_id'],
                  'handoff_id': released['handoff_id']}
        self._save_intent(operation, result, 'claim.switched')
        return result

    def release_claim(self, operation):
        task = self.repository.task(operation['task_id'])
        claim = self.repository.active_for_task(task['task_id'])
        require_source(task['task_id'], operation['claim_id'], operation['claimant'], claim)
        return self._release(task, claim, operation['claimant'], operation['reason'], 'claim.released')

    def resume_claim(self, operation):
        task = self.repository.task(operation['task_id'])
        handoff = self.repository.handoff(operation['handoff_id'])
        require_handoff(handoff, task['task_id'], task['reference'],
                        self.repository.handoff_consumed(operation['handoff_id']))
        self._target(task['task_id'], operation['claimant'])
        result = self._create(task['task_id'], operation['claimant'])
        self.repository.record(task['task_id'], operation['claimant'], 'claim.resumed', {
            'consumed_handoff_id': operation['handoff_id'], 'claim': result}, self.now())
        return result

    def recover_claim(self, operation):
        task = self.repository.task(operation['task_id'])
        claim = self.repository.active_for_task(task['task_id'])
        require_source(task['task_id'], operation['claim_id'],
                       None if claim is None else claim['claimant'], claim)
        return self._release(task, claim, operation['operator'], operation['reason'], 'claim.recovered',
                             {'decision': operation['decision'], 'writers_stopped': True})
