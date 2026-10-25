"""Own terminal decision meaning and the boundary of genuinely new work."""
from copy import deepcopy

from apps.execution.domain.process.workflow import POSITIVE_OUTCOME, RouteDefinition
from apps.execution.domain.records import RecordsError

OUTCOME_RECORD_KIND = 'outcome_fact'


class OutcomePolicy:
    @staticmethod
    def roles(configuration, lifecycle):
        if not isinstance(configuration, dict):
            raise RecordsError('Configure outcomes as an object with explicit roles')
        if set(configuration) != {'roles'}:
            raise RecordsError('outcomes requires exact fields: roles')
        roles = configuration['roles']
        if (not isinstance(roles, list) or not roles
                or any(not isinstance(role, str) or not role.strip() for role in roles)):
            raise RecordsError('outcomes.roles requires a nonempty list of role IDs')
        if len(set(roles)) != len(roles):
            raise RecordsError('outcomes.roles must contain unique role IDs')
        known = set(lifecycle['actors'].values()) if lifecycle is not None else set()
        if set(roles) - known:
            raise RecordsError('outcomes.roles must select roles from lifecycle.actors')
        return tuple(roles)

    @staticmethod
    def semantic_input(value):
        return {key: deepcopy(item) for key, item in value.items()
                if key not in ('request_id', 'expected_revision')}

    @staticmethod
    def work_scope(records, generation):
        opening = None
        for record in records:
            if (record.kind == 'lifecycle_event' and record.generation == generation
                    and record.data['action'] in ('enter', 'rework', 'restart')):
                opening = record.record_id
        return {'generation': generation, 'opening_event_id': opening}

    @staticmethod
    def require_open(records, scope):
        if any(record.kind == OUTCOME_RECORD_KIND and record.data['scope'] == scope
               for record in records):
            raise RecordsError('Current work already has an outcome decision; use its addressed fact or begin genuine new work')

    @staticmethod
    def terminal(records, scope, execution, process):
        start = -1
        if scope['opening_event_id'] is not None:
            start = next(index for index, record in enumerate(records)
                         if record.record_id == scope['opening_event_id'])
        traversals = [record for record in records[start + 1:]
            if record.kind == 'advance_traversal' and record.generation == execution['generation']
            and record.data['stage_id'] == execution['current_stage']
            and record.data['iteration'] == execution['iteration']]
        if not traversals or execution['state'] != 'verified':
            raise RecordsError('Accept requires an admitted terminal route outcome')
        traversal = traversals[-1].data
        visit = next((record.data for record in records if record.kind == 'accepted_visit'
                      and record.record_id == traversal['visit_id']), None)
        if visit is None or visit['next_stage'] is not None:
            raise RecordsError('Accept requires an admitted terminal visit')
        node = RouteDefinition.from_workflow(process).node(execution['current_stage'])
        if node.target(visit['outcome']) is not None:
            raise RecordsError('Current route continues; select an admitted terminal outcome')
        return {'visit': visit, 'traversal': traversal}

    @staticmethod
    def _current_keys(snapshot):
        statement = snapshot.get('statement') if isinstance(snapshot, dict) else None
        entries = statement.get('task_requirements') if isinstance(statement, dict) else None
        if not isinstance(entries, list):
            raise RecordsError('Positive outcome requires the frozen published Task requirement entries')
        return sorted(entry['key'] for entry in entries if entry['status'] == 'current')

    @classmethod
    def fact(cls, execution, snapshot, value, role, process=None, terminal=None):
        if value['intent'] == 'cancel':
            kind, handler, source = 'cancelled', None, None
            provenance = {'visit_id': None, 'source_generation': None,
                'source_actor_ids': [], 'gate_id': None, 'evidence_id': None,
                'current_gate_id': None, 'current_evidence_id': None}
        else:
            visit, traversal = terminal['visit'], terminal['traversal']
            handler = visit['outcome']
            node = RouteDefinition.from_workflow(process).node(execution['current_stage'])
            kind = 'positive' if handler == POSITIVE_OUTCOME[node.handler] else 'negative'
            source = deepcopy(value['source'])
            provenance = {key: deepcopy(visit[key]) for key in ('visit_id', 'source_generation',
                'source_actor_ids', 'gate_id', 'evidence_id')}
            provenance.update(current_gate_id=terminal['current_gate_id'],
                              current_evidence_id=traversal['current_evidence_id'])
        result = value['result_commit']
        if kind == 'positive':
            if result is not None and result != source['commit']:
                raise RecordsError('Positive result_commit must be null or the admitted source commit')
        elif result is not None:
            raise RecordsError('Negative/cancelled result_commit must be null')
        return {'outcome_id': value['outcome_id'], 'task_id': execution['task_id'],
            'generation': execution['generation'], 'contract_revision': execution['contract_revision'],
            'requirements_snapshot_revision': execution['contract']['requirements_snapshot_revision'],
            'kind': kind, 'stage_id': execution['current_stage'], 'iteration': execution['iteration'],
            'handler_outcome': handler, 'source': source, 'provenance': provenance,
            'satisfied_task_requirements': cls._current_keys(snapshot) if kind == 'positive' else [],
            'result_commit': result, 'decision': {'actor_id': value['actor_id'], 'role_id': role,
                'intent': value['intent'], 'reason': value['reason']}}
