"""Exact gate transport/configuration shapes; acceptance stays in the domain."""
from pathlib import Path

from apps.execution.domain.file_policy import FilePolicy, relative_path
from apps.execution.domain.process.errors import DomainError
from apps.execution.domain.process.validation import configuration_fields


def _text(value, where):
    if type(value) is not str or not value.strip() or '\x00' in value:
        raise DomainError(f'{where}: provide a nonempty text identity')
    return value


def _integer(value, where, minimum):
    if type(value) is not int or value < minimum:
        raise DomainError(f'{where}: provide an integer >= {minimum}')


def _exact(value, fields, where):
    if type(value) is not dict or set(value) != fields:
        raise DomainError(f'{where}: requires exact fields {sorted(fields)}')


def _task(value, config):
    if config['task_pattern'].fullmatch(_text(value, 'Gate task_id')) is None:
        raise DomainError('Gate task_id violates configured identifier policy')


def settings(raw, state):
    value = configuration_fields(raw, {'roots'}, 'Execution config gates', DomainError)
    roots = configuration_fields(value['roots'], {'runtime', 'task', 'sprint'},
                                 'Execution config gates.roots', DomainError)
    result = {}
    for scope in ('runtime', 'task', 'sprint'):
        path = Path(_text(roots[scope], f'Execution config gates.roots.{scope}'))
        result[scope] = (path if path.is_absolute() else state / path).resolve()
    return result


def packet(value, config):
    if 'gates' not in config:
        raise DomainError('Configure gates.roots runtime/task/sprint before a gate command')
    action = value.get('action')
    if type(action) is not str or action not in ('declare', 'evaluate'):
        raise DomainError('Gate action: provide declare or evaluate')
    common = {'action', 'request_id', 'expected_revision', 'task_id', 'generation', 'context_id'}
    fields = ({'contract_revision', 'requirement_ids', 'task_content', 'stage_contracts'}
              if action == 'declare' else {'candidate_id', 'stage_id', 'iteration', 'sections', 'trace', 'files'})
    if action == 'declare' and 'file_policy' in value:
        fields = fields | {'file_policy'}
    _exact(value, common | fields, f'Gate {action} input')
    for name in ('request_id', 'context_id'):
        _text(value[name], f'Gate {name}')
    _task(value['task_id'], config)
    _integer(value['expected_revision'], 'Gate expected_revision', 0)
    _integer(value['generation'], 'Gate generation', 1)
    if action == 'declare':
        _text(value['contract_revision'], 'Gate contract_revision')
        ids = value['requirement_ids']
        if (type(ids) is not list or any(type(i) is not str or not i.strip() for i in ids)
                or len(ids) != len(set(ids))):
            raise DomainError('Gate requirement_ids: provide a unique explicit text list')
        if type(value['task_content']) is not dict or type(value['stage_contracts']) is not list:
            raise DomainError('Gate task_content/stage_contracts: provide an object and explicit list')
        return
    _text(value['candidate_id'], 'Gate candidate_id')
    _text(value['stage_id'], 'Gate stage_id')
    _integer(value['iteration'], 'Gate iteration', 1)
    sections, trace, files = value['sections'], value['trace'], value['files']
    if type(sections) is not dict or any(type(v) is not str for v in sections.values()):
        raise DomainError('Gate sections: provide an object of text section values')
    if type(trace) is not dict:
        raise DomainError('Gate trace: provide an object of route updates')
    for points in trace.values():
        if type(points) is not dict:
            raise DomainError('Gate trace route: provide an object of point updates')
        for point in points.values():
            if point is not None and type(point) is not str and not (
                    type(point) is dict and all(type(v) is str for v in point.values())):
                raise DomainError('Gate trace point: provide text, a record of text fields or null')
    if type(files) is not list:
        raise DomainError('Gate files: provide an explicit absolute-path list')
    for path in files:
        if not Path(_text(path, 'Gate file path')).is_absolute():
            raise DomainError('Gate file path: provide an absolute result path')


def query_item(value, config):
    kind = value.get('kind')
    if kind == 'gate_context':
        fields = {'id', 'kind', 'task_id', 'generation', 'context_id'}
    elif kind == 'file_result':
        fields = {'id', 'kind', 'task_id', 'generation', 'context_id', 'request_id'}
    elif kind == 'gate_result':
        fields = {'id', 'kind', 'task_id', 'generation', 'context_id', 'candidate_id'}
    else:
        raise DomainError('Select a supported gate query kind')
    _exact(value, fields, f'{kind} query')
    for field in ('id', 'context_id'):
        _text(value[field], f'Gate query {field}')
    _task(value['task_id'], config)
    _integer(value['generation'], 'Gate query generation', 1)
    if kind == 'gate_result':
        _text(value['candidate_id'], 'Gate query candidate_id')

    if kind == 'file_result':
        _text(value['request_id'], 'File result request_id')


def files_packet(value, config):
    fields = {'action', 'request_id', 'expected_revision', 'task_id', 'generation',
              'context_id', 'stage_id', 'mode', 'paths'}
    _exact(value, fields, 'Files check input')
    if value['action'] != 'check':
        raise DomainError('Files action: provide check')
    for field in ('request_id', 'context_id', 'stage_id'):
        _text(value[field], 'Files ' + field)
    _task(value['task_id'], config)
    _integer(value['expected_revision'], 'Files expected_revision', 0)
    _integer(value['generation'], 'Files generation', 1)
    if value['mode'] not in ('read', 'write'):
        raise DomainError('Files mode: provide read or write')
    if type(value['paths']) is not list:
        raise DomainError('Files paths: provide an explicit list')
    for item in value['paths']:
        _exact(item, {'root', 'path'}, 'Files selected path')
        _exact(item['root'], {'kind', 'position'}, 'Files selected root')
        FilePolicy.root_key(item['root'])
        relative_path(item['path'])
