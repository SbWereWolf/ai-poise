"""Process-owned cleanup intent, progress and explicit external-owner observations."""
from copy import deepcopy

from apps.execution.domain.process.validation import configuration_fields
from apps.execution.domain.records import RecordsError


class CleanupPolicy:
    @staticmethod
    def roles(configuration, lifecycle):
        roles = configuration_fields(configuration, {'roles'}, 'Cleanup policy', RecordsError)['roles']
        if (type(roles) is not list or not roles
                or any(type(role) is not str or not role.strip() for role in roles)):
            raise RecordsError('cleanup.roles: provide a nonempty list of role IDs')
        if len(set(roles)) != len(roles):
            raise RecordsError('cleanup.roles: select unique roles')
        known = set(lifecycle['actors'].values()) if lifecycle is not None else set()
        if set(roles) - known:
            raise RecordsError('cleanup.roles: select roles from lifecycle.actors')
        return roles

    @staticmethod
    def resources(resources, workspace_id):
        identifiers = [item['resource_id'] for item in resources]
        if len(set(identifiers)) != len(identifiers):
            raise RecordsError('Cleanup resources require unique resource_id values')
        for kind in ('worktree', 'branch'):
            selected = [item for item in resources if item['kind'] == kind]
            if len(selected) > 1:
                raise RecordsError('Cleanup resources permit one worktree and one branch pair')
            if selected and selected[0]['reference']['workspace_id'] != workspace_id:
                raise RecordsError('Cleanup resource Workspace must match the own execution binding')

    @staticmethod
    def disposition(disposition, resources):
        if disposition is None:
            return
        branch = next((item for item in resources if item['kind'] == 'branch'), None)
        if branch is None:
            if disposition != {'kind': 'discard_authorized', 'expected_commit': None}:
                raise RecordsError('Branchless cleanup requires discard_authorized and expected_commit:null')
        elif disposition['expected_commit'] != branch['reference']['commit']:
            raise RecordsError('Cleanup disposition expected_commit must match its branch binding')

    @staticmethod
    def semantic_prepare(value):
        return {key: deepcopy(item) for key, item in value.items()
                if key not in ('request_id', 'expected_revision')}


class CleanupRun:
    def __init__(self, value):
        self.value = deepcopy(value)

    @property
    def run(self):
        return self.value['run']

    @classmethod
    def new(cls, value, allocate_id):
        run = {
            'cleanup_id': value['cleanup_id'], 'task_id': value['task_id'],
            'generation': value['expected_generation'], 'outcome_id': value['outcome_id'],
            'disposition': None, 'status': 'disposition_required',
            'resources': deepcopy(value['resources']), 'removed': [], 'history': [],
            'work': None, 'issue': None,
        }
        instance = cls({'input': CleanupPolicy.semantic_prepare(value), 'run': run})
        instance._event('obligation_recorded')
        instance.select_disposition(value, allocate_id)
        return instance

    def _event(self, event, work=None, observation=None):
        self.run['history'].append({
            'event': event, 'step_id': work['step_id'] if work else None,
            'attempt_id': work['attempt_id'] if work else None,
            'resource_id': work['resource']['resource_id'] if work else None,
            'observation': deepcopy(observation),
        })

    def select_disposition(self, value, allocate_id):
        decision = value['commit_disposition']
        CleanupPolicy.disposition(decision, self.value['input']['resources'])
        if self.run['disposition'] is None and decision is not None:
            self.run['disposition'] = deepcopy(decision)
            self._event('disposition_recorded')
        self._schedule(allocate_id)

    def _disposition_observed(self):
        kind = self.run['disposition']['kind']
        if kind == 'discard_authorized':
            return True
        return any(event['observation'] is not None
                   and event['observation']['status'] == 'succeeded'
                   and event['observation']['result']['effect'] == kind
                   for event in self.run['history'])

    def _schedule(self, allocate_id):
        run = self.run
        run['issue'] = None
        if not run['resources']:
            run.update(status='cleanup_complete', work=None)
            return
        if run['disposition'] is None:
            run.update(status='disposition_required', work=None,
                issue={'reason': 'commit_disposition_required',
                       'recovery': 'Select an explicit disposition and continue this cleanup.'})
            return
        if not self._disposition_observed():
            resource = next(item for item in run['resources'] if item['kind'] == 'branch')
            operation = 'preserve' if run['disposition']['kind'] == 'preserved' else 'integration_status'
        else:
            resource = run['resources'][0]
            if resource['kind'] == 'branch':
                resource = next((item for item in run['resources'] if item['kind'] == 'worktree'), resource)
            operation = 'remove'
        run.update(status='cleanup_pending', work={
            'step_id': allocate_id(), 'attempt_id': allocate_id(),
            'owner': resource['owner'], 'operation': operation, 'resource': deepcopy(resource),
            'disposition': deepcopy(run['disposition']), 'mode': 'execute',
        })

    def resume(self, value, allocate_id):
        decision = value['commit_disposition']
        if self.run['disposition'] is None:
            if self.run['resources'] and decision is None:
                raise RecordsError('Cleanup continue requires an explicit commit disposition')
            self.select_disposition(value, allocate_id)
            return
        if decision != self.run['disposition']:
            raise RecordsError('Cleanup decision is immutable; continue the selected disposition')
        if self.run['status'] == 'cleanup_complete':
            return
        work = self.run['work']
        if self.run['status'] == 'cleanup_blocked':
            self._event('retry_requested', work)
            work['attempt_id'] = allocate_id()
            work['mode'] = 'execute'
            self.run.update(status='cleanup_pending', issue=None)
        else:
            work['mode'] = 'reconcile'

    def _success(self, work, observation):
        result = observation['result']
        operation = work['operation']
        expected = {'remove': 'removed', 'preserve': 'preserved', 'integration_status': 'integrated'}[operation]
        if result['effect'] != expected:
            raise RecordsError('Cleanup observation effect must match the current owner operation')
        if operation == 'integration_status':
            disposition = self.run['disposition']
            if (result['source_commit'], result['target_commit'], result['integration_request_id']) != (
                    disposition['expected_commit'], disposition['target_commit'], disposition['integration_request_id']):
                raise RecordsError('Cleanup integration observation must match source, target and integration_request_id')
        if operation == 'remove':
            self.run['resources'].remove(work['resource'])
            self.run['removed'].append(deepcopy(work['resource']))

    def observe(self, step_id, attempt_id, observation, allocate_id):
        work = self.run['work']
        if (self.run['status'] != 'cleanup_pending' or work is None
                or (step_id, attempt_id) != (work['step_id'], work['attempt_id'])):
            raise RecordsError('Cleanup observation must select the current pending step and attempt')
        status = observation['status']
        if status == 'not_started' and work['mode'] != 'reconcile':
            raise RecordsError('Cleanup not_started is admitted only for the current reconcile attempt')
        if status == 'succeeded':
            self._success(work, observation)
        self._event('owner_observed', work, observation)
        if status == 'succeeded':
            self._schedule(allocate_id)
        elif status == 'not_started':
            work['mode'] = 'execute'
            self.run['issue'] = None
        else:
            self.run['issue'] = deepcopy(observation['result'])
            self.run['status'] = 'cleanup_blocked' if status == 'failed' else 'cleanup_pending'
            if status == 'unknown':
                work['mode'] = 'reconcile'

    def result(self):
        return deepcopy(self.run)
