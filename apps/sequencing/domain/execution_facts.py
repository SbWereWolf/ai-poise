"""Owned permissions and projections for explicitly received execution facts."""


class FactsError(ValueError):
    pass


def label_fact(operation):
    return {key: operation[key] for key in ('task_id', 'label_id', 'label', 'source_ref')}


def require_same_fact(saved, current, kind):
    if saved != current:
        raise FactsError(kind + '_conflict')


def require_outcome_target(state, active_claim, workspace):
    if state in ('completed', 'cancelled'):
        raise FactsError('outcome_conflict')
    if active_claim is not None:
        raise FactsError('claim_conflict')
    if workspace is not None and workspace['status'] in ('reserved', 'release_pending'):
        raise FactsError('workspace_conflict')


def state_for_outcome(kind):
    return {'positive': 'completed', 'negative': 'available', 'cancelled': 'cancelled'}[kind]


def terminal_for_outcome(fact):
    if fact['kind'] == 'negative':
        return None
    return {'terminal_id': fact['outcome_id'], 'task_id': fact['task_id'],
            'kind': state_for_outcome(fact['kind']), 'result_commit': fact['result_commit'],
            'source_ref': fact['source_ref']}
