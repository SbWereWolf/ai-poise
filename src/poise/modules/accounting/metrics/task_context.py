"""Optional saved Task association; querying never reconnects an authoritative owner."""
from .domain import ObservationFacts, identity


UNAVAILABLE_FIELDS = ('dependency', 'relation', 'location', 'profile', 'signal', 'recommended_skill', 'updated_at')


def task_observations(observed, scope, source, captured_at):
    facts = ObservationFacts(scope, source, captured_at)
    entity = identity([scope, 'task-association'])
    context = {'session_id': scope['session']}
    if observed['state'] != 'known':
        facts.add('optional-task-policy', entity, 'task_id', None, 'association',
                  kind='observed', state=observed['state'], reason=observed['reason'], context=context)
        return facts.export()
    fields = {**observed, **context, 'captured_at': captured_at,
              'field_availability': {name: {'source_id': 'task-adapter', 'state': 'unknown',
                                          'reason': 'The authoritative current Task does not supply this attribute'}
                                     for name in UNAVAILABLE_FIELDS}}
    fields['field_availability']['policy_hash'] = {'source_id': 'skill-policy', 'state': 'unknown',
                                                 'reason': 'Task association does not supply effective skill policy content'}
    facts.describe('optional-task-policy', entity, fields)
    return facts.export()
