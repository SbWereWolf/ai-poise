"""A real stdio peer, literal protocol expectations and no execution effects."""
from copy import deepcopy

import pytest

from .support import literal


def test_real_discovery_and_typed_correlation(case):
    peer = case.peer()
    hello = peer.initialize()
    assert hello['serverInfo']['name'] and hello['serverInfo']['version']
    peer.request(7, 'ping', {})
    peer.request('7', 'tools/list', {})
    assert peer.response(7) == {'jsonrpc': '2.0', 'id': 7, 'result': {}}
    tools = peer.response('7')['result']['tools']
    assert {item['name'] for item in tools} == {'run', 'cancel'}
    advertised = {item['name']: item['inputSchema'] for item in tools}
    assert set(advertised['run']['required']) == set(literal('run-arguments.json'))
    assert set(advertised['cancel']['required']) == set(literal('cancel-arguments.json'))
    assert not case.rows()


@pytest.mark.parametrize('kind,code', literal('protocol-errors.json'))
def test_protocol_errors_reach_real_peer_without_child(case, kind, code):
    peer = case.peer()
    if kind != 'before-init':
        if kind == 'missing-initialized':
            peer.request('hello', 'initialize', literal('initialize.json'))
            assert peer.response('hello')['result']['protocolVersion'] == '2025-11-25'
        else:
            peer.initialize()
    method, params = 'tools/list', {}
    if kind == 'repeat-initialize':
        method, params = 'initialize', literal('initialize.json')
    elif kind == 'unknown-method':
        method = 'independent/unknown'
    elif kind == 'unknown-tool':
        method, params = 'tools/call', {'name': 'unknown', 'arguments': {}}
    peer.request('bad', method, params)
    result = peer.response('bad')
    assert result['error']['code'] == code and result['error']['message']
    assert not case.rows()


def test_unsupported_version_is_not_silently_claimed(case):
    peer = case.peer()
    hello = literal('initialize.json')
    hello['protocolVersion'] = 'unsupported-independent-version'
    peer.request('hello', 'initialize', hello)
    actual = peer.response('hello')['result']
    assert actual['protocolVersion'] == '2025-11-25'
    assert not case.rows()


@pytest.mark.parametrize('field,value', [('preview', 'true'), ('verbose', 1), ('selection', None)])
def test_malformed_arguments_are_not_started(case, field, value):
    peer = case.peer(); peer.initialize()
    args = case.arguments()
    args[field] = value
    before = deepcopy(case.scene.functional_state())
    peer.call('bad', 'run', args)
    response = peer.response('bad')
    assert response['error']['code'] == -32602
    assert not case.rows() and case.scene.functional_state() == before


def test_malformed_wire_is_a_protocol_error_not_a_check_result(case):
    peer = case.peer(); peer.initialize()
    peer.input_path.write_bytes(peer.input_path.read_bytes() + b'not-json\n')
    peer.process.stdin.write(b'not-json\n'); peer.process.stdin.flush()
    response = peer.response(None)
    assert response['error']['code'] == -32700
    assert not case.rows()
