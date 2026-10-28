"""Explicit check transport contracts, independent of processes and storage."""
from copy import deepcopy
from pathlib import PurePath

from .contracts import exact, require, text, number


class CheckTransportPolicy:
    @staticmethod
    def parse(value):
        exact(value, ('schema', 'protocol_versions', 'tools', 'limits', 'cancellation', 'invocation'),
              'transport_policy', ('operator_extension',))
        require(value['schema'] == 'poise-checks-mcp-policy-1', 'transport_policy.schema', 'Неподдерживаемая схема.')
        versions = value['protocol_versions']
        require(isinstance(versions, list) and bool(versions), 'protocol_versions', 'Укажите поддержанные версии.')
        for version in versions:
            text(version, 'protocol_versions')
        require(len(set(versions)) == len(versions), 'protocol_versions', 'Версии повторяются.')
        exact(value['tools'], ('run', 'cancel'), 'tools')
        for name in value['tools'].values():
            text(name, 'tools')
        require(len(set(value['tools'].values())) == 2, 'tools', 'Имена инструментов должны различаться.')
        exact(value['limits'], ('max_message_bytes', 'max_workers'), 'limits')
        for key, item in value['limits'].items():
            number(item, 'limits.' + key, integer=True)
        exact(value['cancellation'], ('grace_seconds', 'cleanup_seconds', 'poll_seconds'), 'cancellation')
        for key, item in value['cancellation'].items():
            number(item, 'cancellation.' + key)
        exact(value['invocation'], ('directory', 'record_file', 'lock_file', 'cancel_file'), 'invocation')
        names = list(value['invocation'].values())
        for name in names:
            text(name, 'invocation')
            require(name not in ('.', '..') and PurePath(name).name == name,
                    'invocation', 'Укажите отдельные имена внутри хранилища проверок.')
        require(len(set(names)) == len(names), 'invocation', 'Имена должны различаться.')
        return deepcopy(value)


def arguments(value, *, cancel=False):
    required = ('binding', 'invocation_id', 'preview', 'verbose', 'reason') if cancel else (
        'binding', 'selection', 'options', 'preview', 'verbose')
    exact(value, required, 'arguments', () if cancel else ('_invocation',))
    for key in ('preview', 'verbose'):
        require(type(value[key]) is bool, key, 'Требуется boolean.')
    if value['binding'] is not None:
        exact(value['binding'], ('config', 'checkout', 'policy'), 'binding')
        for key, path in value['binding'].items():
            text(path, 'binding.' + key)
            require(PurePath(path).is_absolute(), 'binding.' + key, 'Требуется абсолютный путь.')
    if cancel:
        if value['invocation_id'] is not None:
            text(value['invocation_id'], 'invocation_id')
        text(value['reason'], 'reason')
        require(len(value['reason']) <= 1000, 'reason', 'Причина превышает 1000 символов.')
    else:
        require(isinstance(value['selection'], dict), 'selection', 'Требуется объект выбора.')
        require(isinstance(value['options'], dict), 'options', 'Требуется объект параметров.')
        if '_invocation' in value:
            text(value['_invocation'], '_invocation')
    return deepcopy(value)


def check_request(value):
    return {'schema': 'poise-project-checks-request-1',
            'operation': 'preview' if value['preview'] else 'run',
            'items': [{'selection': deepcopy(value['selection']), 'options': deepcopy(value['options'])}]}


def envelope(body, error=False):
    import json
    return {'content': [{'type': 'text', 'text': json.dumps(body, ensure_ascii=False, allow_nan=False)}],
            'structuredContent': body, 'isError': bool(error)}


def run_envelope(result, identity):
    body = {'schema': 'poise-mcp-check-result-1',
            'operation': 'preview' if result['operation'] == 'preview' else 'run',
            'invocation': {'id': identity, 'state': 'not_started' if identity is None else 'terminal',
                           'project': result['project'], 'checkout': result['checkout'],
                           'response_path': result['response_path']}, 'result': result}
    return envelope(body, result['status'] in ('invalid', 'unavailable', 'unconfirmed'))


def tool_definitions(policy):
    binding = {'anyOf': [{'type': 'null'}, {'type': 'object', 'properties': {
        k: {'type': 'string'} for k in ('config', 'checkout', 'policy')},
        'required': ['config', 'checkout', 'policy'], 'additionalProperties': False}]}
    common = {'binding': binding, 'preview': {'type': 'boolean'}, 'verbose': {'type': 'boolean'}}
    run = {**common, 'selection': {'type': 'object'}, 'options': {'type': 'object'},
           '_invocation': {'type': 'string'}}
    cancel = {**common, 'invocation_id': {'type': ['string', 'null']},
              'reason': {'type': 'string', 'minLength': 1, 'maxLength': 1000}}
    return [{'name': policy['tools'][kind], 'description': description,
             'inputSchema': {'type': 'object', 'properties': fields,
                             'required': [k for k in fields if k != '_invocation'],
                             'additionalProperties': False}}
            for kind, fields, description in (
                ('run', run, 'Предварительно проверить или выполнить настроенную проверку и дождаться результата.'),
                ('cancel', cancel, 'Отменить собственную проверку и получить фактическое состояние очистки.'))]
