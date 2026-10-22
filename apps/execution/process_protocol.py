"""Strict public process packets; supported configuration projection is separate."""
from pathlib import Path
from apps.execution.domain.process.errors import DomainError
from apps.execution.domain.process.validation import configuration_fields, validate_exact_keys


def exact(value, keys, where):
    validate_exact_keys(value, set(keys), where, DomainError)


def text(value, where):
    if not isinstance(value, str) or not value.strip() or '\0' in value:
        raise DomainError(f'{where}: nonempty string required')
    return value


def integer(value, where, minimum, maximum=None):
    if type(value) is not int or value < minimum or (maximum is not None and value > maximum):
        raise DomainError(f'{where}: integer in configured range required')
    return value


def settings(raw, state):
    fields = configuration_fields(raw, {'database','lock','workflows','templates','max_changes','file_mode','json_indent'}, 'process', DomainError)
    def path(value, label):
        value = Path(text(value, label))
        return (value if value.is_absolute() else state / value).resolve()
    result = {name: path(fields[name], 'process.' + name) for name in ['database','lock']}
    for key in ['workflows','templates']:
        if not isinstance(fields[key], dict) or (key == 'workflows' and not fields[key]):
            raise DomainError(f'process.{key}: nonempty object required')
        result[key] = {}
        for name, value in fields[key].items():
            text(name, 'process.' + key + ' id')
            if key == 'templates':
                value = configuration_fields(value, {'path'}, 'process.templates path', DomainError)['path']
            result[key][name] = path(value, 'process.' + key)
    result['max_changes'] = integer(fields['max_changes'], 'process.max_changes', 1)
    result['file_mode'] = integer(fields['file_mode'], 'process.file_mode', 0, 0o777)
    result['json_indent'] = integer(fields['json_indent'], 'process.json_indent', 0)
    if result['database'] == result['lock']:
        raise DomainError('process database and lock must be distinct')
    return result


def packet(value, config):
    if 'process' not in config:
        raise DomainError('Explicit process configuration is required')
    action = value.get('action')
    common = {'action','goal_type'}
    shapes = {
        'status': common,
        'create': common | {'request_id','expected_revision','source','changes'},
        'edit': common | {'request_id','expected_revision','expected_process_revision','changes'},
        'reconcile': common | {'request_id','expected_revision','expected_managed_revision','expected_live_revision','authorization_id','reason'},
    }
    if not isinstance(action, str) or action not in shapes:
        raise DomainError('process action: unknown action')
    exact(value, shapes[action], 'process input')
    text(value['goal_type'], 'goal_type')
    if action == 'status':
        return
    text(value['request_id'], 'request_id')
    integer(value['expected_revision'], 'expected_revision', 0)
    if action in ['create','edit']:
        changes = value['changes']
        if not isinstance(changes, list) or len(changes) > config['process']['max_changes']:
            raise DomainError('changes: required list within max_changes limit')
    if action == 'create':
        source = value['source']
        if not isinstance(source, dict) or source.get('kind') not in ['definition','template']:
            raise DomainError('source.kind: definition or template required')
        kind = source['kind']
        exact(source, {'kind',kind}, 'source')
        if kind == 'definition' and not isinstance(source[kind], dict):
            raise DomainError('source.definition: object required')
        if kind == 'template':
            exact(source[kind], {'id'}, 'source.template')
            text(source[kind]['id'], 'source.template.id')
    if action == 'edit':
        text(value['expected_process_revision'], 'expected_process_revision')
    if action == 'reconcile':
        for key in ['expected_managed_revision','expected_live_revision','authorization_id','reason']:
            text(value[key], key)
