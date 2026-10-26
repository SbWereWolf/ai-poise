"""Adapt existing native owners and real source identities to stage handoff."""
from copy import deepcopy

from ..application.check_attempts import is_check_attempt, require_identity
from ..modules.foundation.errors import PoiseError
from ..modules.tasks.ports import TaskReadSet


class RuntimeHandoffEffects:
    def __init__(self, runtime, verification):
        self.runtime, self.verification = runtime, verification

    def digest(self, value):
        return self.runtime.packet_digest(value)

    def require_owner(self, task_id):
        data = self.runtime.current_task()
        if (data is None or data['id'] != task_id
                or data['claimed_by'] != self.runtime.session):
            raise PoiseError('handoff task_id must name the current Task owned by this caller')
        return data

    def prepare_verification(self, args):
        return self.verification.prepare({'result': args['result'], 'artifacts': args['artifacts']})

    def execute_verification(self, prepared):
        return self.verification.execute(prepared)

    def source(self, task_id):
        data = self.runtime.task_queries.record(task_id)
        if data is None:
            raise PoiseError('Handoff source Task is unavailable')
        workspace = self.runtime._verification_workspace(data)
        return {'commit': self.runtime._git(workspace, 'rev-parse', 'HEAD'),
                'tree': self.runtime._tree(workspace)}

    def context(self, task_id):
        inputs = self.runtime.store.unit_of_work().capture(TaskReadSet((task_id,)))
        task = inputs.task(task_id)
        return {'stage': task.stage.stage_id, 'iteration': task.state.iteration,
                'status': task.state.status.value, 'submission_digest': task.state.submission_digest}

    def recovered_verification(self, state):
        data = self.require_owner(state.task_id)
        if state.origin['status'] != 'active' or data['status'] != 'verified':
            return None
        report = data['last_report']
        if report is None or (report['stage'], report['iteration']) != (
                state.origin['stage'], state.origin['iteration']):
            raise PoiseError('Handoff source stage or result changed; use its explicit correction owner')
        inputs = self.runtime.store.unit_of_work().capture(TaskReadSet(
            (state.task_id,), verification_ids=(state.task_id,)))
        if inputs.work_packet(state.task_id, state.origin['stage'], state.origin['iteration']) != state.packet_digest:
            raise PoiseError('Handoff verification work packet conflict')
        self.runtime.retention.validate_bundle(data, report)
        self.runtime._require_report_identity(data)
        self.runtime.validate_verification_artifacts([], data)
        if not self.runtime._delivered_receipts_intact(data):
            raise PoiseError('Current verification receipt missing or changed; restore its original bytes')
        return deepcopy(report)

    def require_unverified_source(self, state):
        data = self.require_owner(state.task_id)
        context = self.context(state.task_id)
        if (context['stage'], context['iteration']) != (
                state.origin['stage'], state.origin['iteration']):
            raise PoiseError('Handoff source stage changed; preserve the old intent and use explicit rework')
        current = self.source(state.task_id)
        if current == state.admitted_source:
            return
        publication = data['publication']
        if (publication is not None and publication.get('commit') == current['commit']
                and publication['tree'] == current['tree'] == state.admitted_source['tree']):
            return
        if (current['tree'] == state.admitted_source['tree']
                and is_check_attempt(data['pending'])):
            scope = self.runtime.validate_stage_scope(data)
            checks = self.runtime._select_checks(data, scope['changed'])
            invocations, execution_key = self.runtime._verification_execution(
                data, current['tree'], checks, self.runtime._verification_workspace(data))
            require_identity(data['pending'], self.runtime._attempt_identity(current['tree'], invocations))
            self.runtime.runner.current_check_attempt(
                state.task_id, self.runtime.session, current['tree'], execution_key,
                [method['id'] for method in checks])
            return
        raise PoiseError('Handoff source changed before verification; preserve WIP and use explicit rework')

    def require_verified_source(self, state):
        data = self.require_owner(state.task_id)
        try:
            self.runtime._require_verification_identity(
                data, state.verified_source['commit'], state.verified_source['tree'])
        except PoiseError as error:
            raise PoiseError(f'Проверка исходников/source: {error}') from error

    def progression_context(self, state):
        self.require_verified_source(state)
        current = self.context(state.task_id)
        if ((current['stage'], current['iteration']) != (
                state.origin['stage'], state.origin['iteration']) or current['status'] != 'verified'):
            raise PoiseError('Handoff verified stage changed before progression')
        return {'before': current, 'next_stage': self.runtime.runner.context(state.task_id)['next_stage']}

    def require_progression_resume(self, state):
        self.require_verified_source(state)
        current = self.context(state.task_id)
        started = state.progression_started
        if current == started['before']:
            return
        data = self.require_owner(state.task_id)
        report = data['last_report']
        if (current['stage'] == started['next_stage'] and current['status'] == 'active'
                and current['submission_digest'] is None and report is not None
                and (report['stage'], report['iteration'], report['commit']) == (
                    state.origin['stage'], state.origin['iteration'], state.verification['commit'])):
            return
        raise PoiseError('Handoff source stage/result changed after progression; use explicit rework')

    def advance(self, state):
        return self.runtime.advance(state.progression_id, state.task_id, state.target_stage)

    def original_release(self, intent):
        return self.runtime.ownership.original_release_receipt(intent)

    def release(self, intent):
        return self.runtime.ownership.release(intent)

    def pending(self, task_id):
        data = self.runtime.task_queries.record(task_id)
        return None if data is None else deepcopy(data['pending'])
