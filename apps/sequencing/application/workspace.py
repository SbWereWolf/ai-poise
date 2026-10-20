"""Permanent Task workspace preparation with short storage transactions."""
from apps.sequencing.domain.ownership import require_source
from apps.sequencing.domain.workspace import require_context, require_prepare_state

PENDING_SCHEMA = 'stride-sequencing-workspace-pending/v1'


class WorkspaceCommands:
    def __init__(self, transaction, git, resolve_roots, allocate_id, now, schema):
        self.transaction = transaction
        self.git = git
        self.resolve_roots = resolve_roots
        self.allocate_id = allocate_id
        self.now = now
        self.schema = schema

    @staticmethod
    def _authorize(repository, inputs):
        owner = repository.ownership
        owner.task(inputs['task_id'])
        require_source(inputs['task_id'], inputs['claim_id'], inputs['claimant'],
                       owner.active_for_task(inputs['task_id']))

    def _response(self, repository, inputs):
        return {'schema': self.schema, 'status': 'workspace_ready',
                'revision': repository.bump_revision(),
                'workspace': repository.value(inputs['task_id']), 'replayed': False}

    def _existing(self, repository, packet, digest):
        inputs = packet['input']
        prior = repository.request(inputs['request_id'], digest)
        if prior is not None and prior.get('schema') != PENDING_SCHEMA:
            return {**prior, 'replayed': True}, None
        self._authorize(repository, inputs)
        saved = repository.saved(inputs['task_id'])
        if saved is None:
            return None, None
        owns_pending = prior is not None and prior['workspace_id'] == saved['workspace_id']
        require_prepare_state(saved['status'], owns_pending)
        path = next(root['path'] for root in saved['roots']
                    if root['kind'] == 'code' and root['position'] == 0)
        require_context(saved, inputs['read_only'], self.resolve_roots(path, inputs['roots']))
        if saved['status'] == 'ready':
            response = self._response(repository, inputs)
            repository.save_request(inputs['request_id'], digest, response, finish=owns_pending)
            return response, None
        return None, saved

    def prepare(self, packet, digest):
        inputs = packet['input']
        with self.transaction() as repository:
            response, saved = self._existing(repository, packet, digest)
        if response is not None:
            return response
        if saved is None:
            initial = self.git.initial(inputs['task_id'], inputs['read_only'])
            with self.transaction() as repository:
                response, saved = self._existing(repository, packet, digest)
                if response is not None:
                    return response
                if saved is None:
                    saved = {key: initial[key] for key in ('repository_id', 'base_commit', 'branch')}
                    saved.update(workspace_id=self.allocate_id(), task_id=inputs['task_id'],
                                 status='ready' if inputs['read_only'] else 'reserved',
                                 read_only=inputs['read_only'],
                                 roots=self.resolve_roots(initial['path'], inputs['roots']))
                    repository.insert_workspace(saved)
                    repository.record(saved, inputs['claimant'], 'workspace.' + saved['status'],
                                      {'request_id': inputs['request_id'], 'intent': packet}, self.now())
                    if inputs['read_only']:
                        response = self._response(repository, inputs)
                        repository.save_request(inputs['request_id'], digest, response)
                        return response
                    repository.save_request(inputs['request_id'], digest, {
                        'schema': PENDING_SCHEMA, 'workspace_id': saved['workspace_id'], 'packet': packet})
                    repository.bump_revision()
        self.git.ensure(saved)
        with self.transaction() as repository:
            response, saved = self._existing(repository, packet, digest)
            if response is not None:
                return response
            repository.mark_ready(saved['workspace_id'])
            repository.record(saved, inputs['claimant'], 'workspace.ready',
                              {'request_id': inputs['request_id']}, self.now())
            response = self._response(repository, inputs)
            repository.save_request(inputs['request_id'], digest, response, finish=True)
            return response
