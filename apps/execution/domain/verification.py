"""Controlled attempts, ordered launch permission and scope-local budgets."""
from copy import deepcopy
from uuid import uuid4

from apps.execution.domain.records import RecordsError


ATTEMPT_KIND = 'verification_attempt'


class VerificationPolicy:
    @staticmethod
    def require_actor(data, actor_id):
        if data['actor_id'] != actor_id:
            raise RecordsError('Continue the verification attempt with its reserved actor')

    @staticmethod
    def current(records):
        return {r.record_id: r for r in records if r.kind == ATTEMPT_KIND}

    @staticmethod
    def scope(records, execution):
        opening = None
        for record in records:
            if (record.kind == 'lifecycle_event' and record.generation == execution['generation']
                    and record.data['action'] in ('enter', 'rework', 'restart')):
                opening = record.record_id
        return {key: execution[key] for key in ('generation', 'current_stage', 'iteration')} | {'opening': opening}

    @classmethod
    def budget(cls, records, execution, maximum):
        scope = cls.scope(records, execution)
        attempts = [r.data['value'] for r in cls.current(records).values() if r.data['scope'] == scope]
        pending = [a['attempt_id'] for a in attempts if a['state'] in ('reserved', 'running')]
        return {'task_id': execution['task_id'], 'generation': execution['generation'],
                'stage_id': execution['current_stage'], 'iteration': execution['iteration'],
                'used': len(attempts), 'maximum': maximum,
                'pending_attempt_id': pending[0] if pending else None}

    @classmethod
    def require_resolved(cls, records, execution):
        if any(r.generation == execution['generation']
               and r.data['value']['state'] in ('reserved', 'running')
               for r in cls.current(records).values()):
            raise RecordsError('Current verification attempt is pending; finish or recover its actual runs, or abandon a quiescent attempt')

    @staticmethod
    def require_quiescent(attempt):
        for run in attempt['runs']:
            if run['started'] and (run['receipt'] is None or not run['termination']
                    or not run['termination']['child_reaped']
                    or not run['termination']['process_group_stopped']):
                raise RecordsError('Attempt has an unresolved run; recover actual termination before abandonment')

    @staticmethod
    def reserve(execution, value, budget, purpose, owner_id):
        if budget['pending_attempt_id'] is not None:
            raise RecordsError('A verification attempt is pending; continue its reserved runs')
        if budget['used'] >= budget['maximum']:
            raise RecordsError('Verification max_attempts exhausted; agree genuine rework or restart before new attempts')
        return {'attempt_id': value['attempt_id'], 'task_id': execution['task_id'],
                'generation': execution['generation'], 'contract_revision': execution['contract_revision'],
                'stage_id': execution['current_stage'], 'iteration': execution['iteration'],
                'context_id': value['context_id'], 'candidate_id': value['candidate_id'],
                'source': deepcopy(value['source']), 'method_ids': list(value['method_ids']),
                'purpose': purpose, 'owner_id': owner_id,
                'budget': {'used': budget['used'] + 1, 'maximum': budget['maximum']},
                'state': 'reserved', 'runs': [
                    {'run_id': str(uuid4()), 'method_id': method, 'started': False,
                     'receipt': None, 'termination': None} for method in value['method_ids']],
                'evidence_id': None}

    @staticmethod
    def validate_run(attempt, run_id):
        if attempt['state'] not in ('reserved', 'running'):
            raise RecordsError('Attempt is already resolved; use its retained result')
        for run in attempt['runs']:
            if run['run_id'] == run_id:
                return run
            if run['receipt'] is None:
                raise RecordsError('Finish the previous reserved run before its tail')
        raise RecordsError('Select an actual run_id from the reserved attempt')

    @staticmethod
    def generic_attempt(data):
        attempt = data['value']
        return {'status': 'completed', 'stage_id': attempt['stage_id'],
                'iteration': attempt['iteration'], 'context_id': attempt['context_id'],
                'candidate_id': attempt['candidate_id'], 'source': deepcopy(attempt['source']),
                'methods': deepcopy(data['methods']), 'execution_key': data['execution_key'],
                'receipts': [deepcopy(r['receipt']) for r in attempt['runs']]}
