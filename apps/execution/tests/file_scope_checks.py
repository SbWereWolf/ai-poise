"""Literal T050 contracts exercised through the real standalone CLI."""
from copy import deepcopy
import json
from pathlib import Path

from process_checks import (APP, applied, assert_same, create, installation, invoke,
                            published, rejected, state, write_json)
from store_checks import original_rows


def fixture(name):
    return json.loads((APP / 'fixtures/file-scope-contract.json').read_text())[name]


def bind(value, mapping):
    if isinstance(value, dict):
        return {key: bind(item, mapping) for key, item in value.items()}
    if isinstance(value, list):
        return [bind(item, mapping) for item in value]
    if isinstance(value, str):
        if value in mapping:
            return mapping[value]
        for key in ('@CODE0', '@CODE1', '@MAIN', '@ARTIFACT'):
            if value.startswith(key + '/'):
                return mapping[key] + value[len(key):]
    return value


def arranged(root, *, counted=False, policy=True, context='scope-1', definition=None,
             policy_change=None):
    config = installation(root)
    mapping = {'@CODE0': str(root / 'code0'), '@CODE1': str(root / 'code1'),
               '@MAIN': str(root / 'main'), '@ARTIFACT': str(root / 'artifact')}
    for directory in mapping.values():
        Path(directory).mkdir(parents=True)
    for token, names in [('@CODE0', ['src/app.py', 'src/private/note.txt',
                                    'src/frozen/app.py', 'note.txt']),
                         ('@CODE1', ['docs/guide.txt', 'docs/readonly.txt',
                                    'src/app.py', 'note.txt']),
                         ('@MAIN', ['src/app.py', 'src/locked.txt']),
                         ('@ARTIFACT', ['outputs/note.txt', 'outputs/excluded.txt'])]:
        for name in names:
            path = Path(mapping[token]) / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(token + ':' + name)
    data = json.loads(config.read_text())
    data['gates'] = {'roots': {'runtime': str(root / 'runtime'),
                              'task': mapping['@ARTIFACT'], 'sprint': str(root / 'sprint')}}
    write_json(config, data)
    definition = fixture('definition') if definition is None else deepcopy(definition)
    if counted:
        for stage in definition['stages']:
            stage['artifact_requirements'] = [fixture('count_rule')]
    first = published(invoke(config, create(definition=definition)), revision=1)
    mapping['@PROCESS'] = first['process_revision']
    contract = bind(fixture('contract'), mapping)
    setup = {'schema': 'stride-execution/v1', 'operation': 'apply', 'input': {
        'request_id': 'setup-scope', 'expected_revision': 1,
        'operations': [{'id': 'setup', 'kind': 'setup_execution', 'contract': contract}]}}
    result = applied(invoke(config, setup))
    mapping['@CONTRACT'] = result['results'][0]['contract_revision']
    declaration = bind(fixture('declare'), mapping)
    declaration['input']['context_id'] = context
    if not policy:
        del declaration['input']['file_policy']
    elif policy_change is not None:
        policy_change(declaration['input']['file_policy'])
    return config, mapping, declaration


def declare(config, value):
    code, response = invoke(config, value)
    assert code == 0, response
    expected = {'schema': 'stride-execution/v1', 'status': 'declared', 'revision': 3,
                'replayed': False, 'task_id': '0042', 'generation': 1,
                'context_id': value['input']['context_id'], 'changed': True}
    assert_same(response, expected)
    return response


def ready(root, **kwargs):
    config, mapping, declaration = arranged(root, **kwargs)
    declare(config, declaration)
    return config, mapping


def path(kind='code', position=0, relative='src/app.py'):
    return {'root': {'kind': kind, 'position': position}, 'path': relative}


def access(mapping, *, stage='draft', mode='read', paths=None, revision=3,
           request='access-1', context='scope-1'):
    packet = bind(fixture('check'), mapping)
    packet['input'].update(stage_id=stage, mode=mode, expected_revision=revision,
                           request_id=request, context_id=context)
    if paths is not None:
        packet['input']['paths'] = paths
    return packet


def checked(config, value, expected_decisions, *, role='maker'):
    code, response = invoke(config, value)
    allowed = all(item['allowed'] for item in expected_decisions)
    assert code == (0 if allowed else 2), response
    expected = fixture('checked_response')
    expected.update(revision=value['input']['expected_revision'] + 1,
                    stage_id=value['input']['stage_id'], mode=value['input']['mode'],
                    context_id=value['input']['context_id'], role=role, allowed=allowed,
                    decisions=expected_decisions)
    assert type(response.get('result_id')) is str and response['result_id'], response
    expected['result_id'] = response['result_id']
    assert_same(response, expected)
    return response


def decision(kind, position, relative, allowed, reason=None):
    return {'root': {'kind': kind, 'position': position}, 'path': relative,
            'allowed': allowed, 'reason': reason}


def query(config, packet):
    code, response = invoke(config, packet)
    assert code == 0, response
    assert response['status'] == 'read_only' and response['replayed'] is False
    assert set(response) == {'schema', 'status', 'revision', 'replayed', 'results'}
    return response


def result_query(**updates):
    packet = fixture('query_result')
    packet['input']['queries'][0].update(updates)
    return packet


def gate_packet(mapping, *, stage='draft', files=None, revision=3, candidate='candidate-1'):
    packet = bind(fixture('evaluate'), mapping)
    packet['input'].update(stage_id=stage, expected_revision=revision,
                           candidate_id=candidate, request_id=candidate)
    if files is not None:
        packet['input']['files'] = files
    return packet


def gate(config, value, admissions, *, accepted, counted=False):
    code, response = invoke(config, value)
    assert code == (0 if accepted else 2), response
    assert set(response) == {'schema', 'status', 'revision', 'replayed', 'task_id',
                            'generation', 'context_id', 'candidate_id', 'stage_id',
                            'iteration', 'accepted', 'result_id', 'assessment'}
    assert response['status'] == 'evaluated' and response['replayed'] is False
    for name in ('task_id', 'generation', 'context_id', 'candidate_id', 'stage_id', 'iteration'):
        assert_same(response[name], value['input'][name])
    assert_same(response['revision'], value['input']['expected_revision'] + 1)
    assert_same(response['accepted'], accepted)
    assert (isinstance(response['result_id'], str) and bool(response['result_id'])) if accepted else response['result_id'] is None
    expected = fixture('assessment')
    expected['pre']['stage'] = expected['post']['stage'] = value['input']['stage_id']
    expected['passed'] = accepted
    expected['file_admission'] = {'passed': all(row['allowed'] for row in admissions),
                                   'files': admissions}
    if counted:
        expected['artifact_checks'] = [fixture('counted_check')]
    assert_same(response['assessment'], expected)
    return response


def saved_gate(config, candidate='candidate-1'):
    packet = {'schema': 'stride-execution/v1', 'operation': 'query', 'input': {'queries': [{
        'id': 'gate', 'kind': 'gate_result', 'task_id': '0042', 'generation': 1,
        'context_id': 'scope-1', 'candidate_id': candidate}]}}
    return query(config, packet)['results'][0]


def database(config):
    return config.parent / 'state/execution.sqlite'


def refusal(config, value, *tokens):
    before = state(config)
    rejected(invoke(config, value), *tokens)
    assert_same(state(config), before)
