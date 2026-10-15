"""Pure Task-owned planning and authorized contract revision; no template I/O."""
from copy import deepcopy

from ..foundation.errors import DomainError
from ..goal_config.domain import fingerprint

# Protocol vocabulary, not a default grant of authority.
REVISION_FIELDS = frozenset({
    'goal_type', 'goal', 'requirements', 'requirements_snapshot',
    'requirements_agreement', 'definition_of_done', 'methods', 'method_inputs',
    'checks', 'artifact_requirements', 'content_contract', 'evidence_plan',
    'executable_obligations', 'stage_contracts', 'decomposition', 'workflow', 'planning',
})


def validate_planning(value):
    if (not isinstance(value, dict)
            or set(value) != {'schema', 'template', 'restart_revision_policy'}
            or value['schema'] != 'task-planning-1'):
        raise DomainError('Task planning requires schema, template and restart_revision_policy')
    template = value['template']
    if template is not None and (
        not isinstance(template, dict) or 'id' not in template
        or set(template) - {'id', 'version', 'digest'}
        or not isinstance(template['id'], str) or not template['id'].strip()
        or any(not isinstance(template[key], str) for key in ('version', 'digest') if key in template)
    ):
        raise DomainError('Task starter requires id; optional version/digest are historical audit text')
    policy = value['restart_revision_policy']
    if not isinstance(policy, dict) or set(policy) != {'reviewer', 'user'}:
        raise DomainError('Task restart revision policy requires explicit reviewer/user fields')
    for fields in policy.values():
        if (not isinstance(fields, list)
                or any(not isinstance(f, str) or f not in REVISION_FIELDS for f in fields)
                or len(fields) != len(set(fields))):
            raise DomainError('Task restart revision policy contains unknown/repeated fields')
    return deepcopy(value)


def revision_authority(contract, workflow, actor, authorization):
    """Save the exact restart grant against the frozen contract."""
    planning = contract.get('planning')
    if planning is None:
        if not isinstance(authorization, str) or not authorization.strip():
            raise DomainError('Task restart authorization is required')
        return None
    validate_planning(planning)
    if (not isinstance(authorization, dict)
            or set(authorization) not in ({'role', 'decision'},
                                          {'role', 'decision', 'revision_fields'})
            or authorization['role'] not in ('reviewer', 'user')
            or not isinstance(authorization['decision'], str)
            or not authorization['decision'].strip()):
        raise DomainError('Task restart authorization requires reviewer/user role and decision')
    if 'revision_fields' in authorization:
        fields = authorization['revision_fields']
        if (authorization['role'] != 'reviewer'
                or not isinstance(fields, list) or not fields
                or any(not isinstance(field, str) or field not in REVISION_FIELDS
                       for field in fields)
                or len(fields) != len(set(fields))):
            raise DomainError('Task restart revision_fields require unique Task contract fields agreed by reviewer')
    else:
        fields = planning['restart_revision_policy'][authorization['role']]
    return {
        'actor': actor, 'authorization': deepcopy(authorization),
        'allowed_changes': deepcopy(fields),
        'before_contract': deepcopy(contract), 'before_workflow': deepcopy(workflow),
        'before_digest': fingerprint({'contract': contract, 'workflow': workflow}),
    }


def revision_changes(contract, workflow, authority):
    before = authority['before_contract']
    changed = {
        field for field in set(before) | set(contract)
        if field not in {'id', 'sprint_id'} and (
            (field in before) != (field in contract)
            or before.get(field) != contract.get(field)
        )
    }
    if workflow != authority['before_workflow']:
        changed.add('workflow')
    return sorted(changed)


def require_revision(draft, workflow, history):
    if not history or 'planning_revision' not in history[-1]:
        return None
    authority = history[-1]['planning_revision']
    changed = revision_changes(draft, workflow, authority)
    denied = set(changed) - set(authority['allowed_changes'])
    if denied:
        source = ('reviewer authorization' if 'revision_fields' in authority['authorization']
                  else 'revision policy')
        raise DomainError(f'Task restart {source} forbids changes: {sorted(denied)}')
    return {
        'changed_fields': changed,
        'contract_digest': fingerprint({'contract': draft, 'workflow': workflow}),
    }
