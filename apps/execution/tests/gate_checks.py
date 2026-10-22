"""Independent literal packets and real CLI/SQLite/file observations for T049."""
from copy import deepcopy
import json
from pathlib import Path
import sqlite3

from process_checks import (APP, applied, assert_same, create, current, installation,
                            invoke, published, rejected, state, write_json)
from store_checks import original_rows, stored_records


def fixture(name):
    return json.loads((APP / 'fixtures/gate-contract.json').read_text())[name]


def bind(value, mapping):
    """Substitute only explicit opaque runtime identities/temporary paths in literals."""
    if isinstance(value, dict):
        return {key: bind(item, mapping) for key, item in value.items()}
    if isinstance(value, list):
        return [bind(item, mapping) for item in value]
    return mapping.get(value, value) if isinstance(value, str) else value


def arranged(root, *, definition=None, gates=True, absolute=False):
    config = installation(root)
    data = json.loads(config.read_text())
    if gates:
        roots = {'runtime': 'files/runtime', 'task': 'files/task', 'sprint': 'files/sprint'}
        if absolute:
            roots = {scope: str(root / 'state' / path) for scope, path in roots.items()}
        data['gates'] = {'roots': roots}
    write_json(config, data)
    first = published(invoke(config, create(definition=fixture('definition') if definition is None else definition)), revision=1)
    contract = bind(fixture('contract'), {'@PROCESS': first['process_revision']})
    packet = fixture('setup_packet')
    packet['input']['operations'][0]['contract'] = contract
    setup = applied(invoke(config, packet))
    path = root / 'state/files/task/outputs/result.txt'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('result\n')
    mapping = {'@PROCESS': first['process_revision'], '@CONTRACT': setup['results'][0]['contract_revision'],
               '@FILE': str(path)}
    return config, mapping


def packet(name, mapping, **updates):
    value = bind(fixture(name), mapping)
    value['input'].update(updates)
    return value


def declared(config, mapping, value=None, *, revision=3, changed=True):
    code, response = invoke(config, packet('declare', mapping) if value is None else value)
    assert code == 0, response
    assert set(response) == set(fixture('response_keys')['declared']), response
    expected = fixture('declared_response')
    expected.update(revision=revision, changed=changed)
    if value is not None:
        expected['context_id'] = value['input']['context_id']
    assert_same(response, expected)
    return response


def ready(root, **kwargs):
    config, mapping = arranged(root, **kwargs)
    declared(config, mapping)
    return config, mapping


def evaluated(config, value, *, accepted, assessment=None):
    code, response = invoke(config, value)
    assert code == (0 if accepted else 2), response
    assert set(response) == set(fixture('response_keys')['evaluated']), response
    assert_same(response['status'], 'evaluated')
    for key in ('task_id', 'generation', 'context_id', 'candidate_id', 'stage_id', 'iteration'):
        assert_same(response[key], value['input'][key])
    assert_same(response['accepted'], accepted)
    assert_same(response['replayed'], False)
    assert type(response['revision']) is int and response['revision'] == value['input']['expected_revision'] + 1
    if accepted:
        assert type(response['result_id']) is str and response['result_id']
    else:
        assert response['result_id'] is None
    if assessment is not None:
        assert_same(response['assessment'], assessment)
    return response


def query(config, *items):
    code, result = invoke(config, {'schema': 'stride-execution/v1', 'operation': 'query',
                                  'input': {'queries': list(items)}})
    assert code == 0, result
    assert set(result) == set(fixture('response_keys')['query']), result
    assert_same(result['status'], 'read_only')
    assert type(result['revision']) is int
    return result


def context_query(context='context-1', *, task='0042', generation=1, identity='context'):
    return {'id': identity, 'kind': 'gate_context', 'task_id': task,
            'generation': generation, 'context_id': context}


def result_query(candidate='candidate-1', *, context='context-1', task='0042', generation=1, identity='result'):
    return {'id': identity, 'kind': 'gate_result', 'task_id': task, 'generation': generation,
            'context_id': context, 'candidate_id': candidate}


def database(config):
    return config.parent / 'state/execution.sqlite'


def rows(config):
    return stored_records(database(config), '0042')


def own_rows(config, kind):
    return [row for row in rows(config) if row['kind'] == kind]


def record(value, kind, data):
    assert set(value) == set(fixture('record_envelope_keys')), value
    assert_same(value['kind'], kind)
    assert_same(value['task_id'], '0042')
    assert_same(value['generation'], 1)
    assert_same(value['revision'], 1)
    assert type(value['id']) is str and value['id']
    assert_same(value['data'], data)


def refusal_unchanged(config, value, *tokens):
    before = state(config)
    rejected(invoke(config, value), *tokens)
    assert_same(state(config), before)


def next_packet(mapping, previous, identity='candidate-2', **changes):
    value = packet('evaluate', mapping, request_id='evaluate-' + identity,
                   candidate_id=identity, expected_revision=previous['revision'])
    value['input'].update(changes)
    return value


def negative_rows(config, candidate='candidate-1'):
    decision = [r for r in own_rows(config, 'gate_decision')
                if r['data']['provenance']['candidate_id'] == candidate]
    assert len(decision) == 1
    assert_same(decision[0]['data']['accepted'], False)
    assert_same(decision[0]['data']['result_id'], None)
    assert not [r for r in own_rows(config, 'stage_result')
                if r['data']['provenance']['candidate_id'] == candidate]
