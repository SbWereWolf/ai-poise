"""Validate captured attribution facts without consulting another owner's storage."""
from copy import deepcopy

from ..foundation.errors import DomainError


def validate_finding_context(targets, task_id, context):
    if not targets:
        return []
    if (task_id is None or not isinstance(context, dict)
            or context.get('schema') != 'accounting-finding-context-1'
            or context.get('task') != task_id
            or not isinstance(context.get('observations'), list)
            or len(context['observations']) != len(targets)):
        raise DomainError('Finding attribution requires an exact captured Task context')
    for target, observation in zip(targets, context['observations']):
        if not isinstance(observation, dict) or observation.get('target') != target:
            raise DomainError('Finding attribution context does not match the requested targets')
        finding = observation.get('finding')
        if (not isinstance(finding, dict) or set(finding) != {'stage', 'iteration'}):
            raise DomainError('Unknown finding for cost attribution')
        if (observation.get('delivered') is not True
                or (finding['stage'], finding['iteration']) == (target['stage'], target['iteration'])):
            raise DomainError('Quality finding must target a previously delivered iteration')
    return deepcopy(targets)
