"""Adapt existing explicit duplicate registration to app-owned identities."""
from apps.sequencing.domain.duplicates import DuplicateIdentity
from apps.sequencing.domain.registration import RegistrationError, RequirementsReference


class DuplicateCommands:
    def __init__(self, repository, registration, now):
        self.repository = repository
        self.registration = registration
        self.now = now

    def register(self, operation):
        family = self.repository.family_ids(operation['parent_id'])
        parent = operation['parent_id'] if family is None else family['parent_id']
        kind = 'statement' if 'requirements_statement_revision' in operation else 'snapshot'
        reference = RequirementsReference(kind, operation['requirements_' + kind + '_revision'])
        identity = DuplicateIdentity(parent, reference, operation['sprint_id'])
        task_id = operation['task_id']
        saved = self.repository.registration(task_id)
        if saved is not None:
            identity.require_same(saved['identity'])
            return {**saved['result'], 'changed': False}
        if self.repository.task_exists(task_id):
            raise RegistrationError('identity_conflict')
        destination = self.registration.destination(identity.sprint_id)
        if destination['state'] != 'draft':
            raise RegistrationError('sprint_not_draft')
        self.registration.register_task(task_id, reference)
        revision = self.registration.add_member(task_id, identity.sprint_id)
        result = {'changed': True, 'task_id': task_id, 'parent_id': parent,
                  'sprint_id': identity.sprint_id, 'sprint_revision': revision}
        self.repository.record(identity, result, operation['claimant'], self.now())
        return result
