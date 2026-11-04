"""Independent fixture composition and real public-boundary calls.

No production schemas, selectors, serializers or token algorithms are imported
to construct expected values. Tokens/addresses are deliberately opaque.
"""
from copy import deepcopy
import hashlib
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys


FIXTURES = Path(__file__).parent / 'fixtures/portable_delivery'
REPOSITORY = Path(__file__).resolve().parents[2]


def literal(name):
    return json.loads((FIXTURES / name).read_text(encoding='utf-8'))


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()


def request():
    return literal('request.json')


def execute(packet):
    # Deliberately lazy: a missing future module is never collection-based RED.
    return importlib.import_module('poise.instructions').execute(deepcopy(packet))


def cli(packet, scenario=None):
    argv = [sys.executable, '-B', '-m', 'poise', 'skills']
    if scenario is not None:
        argv += ['--catalog', str(scenario.catalog), '--root', str(scenario.checkout)]
    result = subprocess.run(
        argv, input=json.dumps(packet), text=True, capture_output=True,
        cwd=REPOSITORY, env={**os.environ, 'PYTHONPATH': str(REPOSITORY / 'src')},
        check=False,
    )
    print(result.stdout, end='')
    print(result.stderr, end='', file=sys.stderr)
    assert result.stderr == '', result.stderr
    return result.returncode, json.loads(result.stdout)


def successful(packet, entry='api'):
    if entry == 'api':
        value = execute(packet)
    else:
        code, value = cli(packet)
        assert code == (1 if value['status'] == 'needs_inputs' else 0), value
    assert set(value) == {'schema', 'status', 'observation', 'delivery', 'receipt'}
    assert value['schema'] == 'poise-portable-instruction-result-1'
    assert value['observation'] == packet['selection']
    assert len(canonical(value['delivery'])) <= packet['budget']['max_bytes']
    return value


def rejected(packet, error, entry='api'):
    if entry == 'api':
        value = execute(packet)
    else:
        code, value = cli(packet)
        assert code == 2, value
    assert value == literal('errors.json')[error]


def selected_records(indices):
    # The fixture author explicitly enumerates each expected partition in tests.
    return [literal('complete-frame.json')['materials'][i] for i in indices]


def expected_frame(packet, delivered=(0, 1, 2), reused=()):
    expected = literal('complete-frame.json')
    expected['context'] = deepcopy(packet['context'])
    expected['stage'] = deepcopy(packet['stage'])
    expected['materials'] = selected_records(delivered)
    expected['reused'] = [
        {k: v for k, v in m.items() if k != 'content'} for m in selected_records(reused)
    ]
    return expected


def continuation_request(first, packet, retained):
    next_packet = deepcopy(packet)
    next_packet.update(previous=first['receipt'], retained=list(retained))
    next_packet['context']['reason'] = 'continue'
    return next_packet


def replace_body(packet, index, body):
    packet['selection']['materials'][index].update(
        content=body, sha256=hashlib.sha256(body.encode()).hexdigest(),
    )


def long_request():
    packet = request()
    replace_body(packet, 0, literal('long-body.json')['content'])
    packet['budget']['max_bytes'] = 5000
    return packet


def address(packet, index=1, entry='api'):
    # Budget the unchanged small records and withhold the selected long one.
    probe = deepcopy(packet)
    probe['budget']['max_bytes'] = 5000
    value = successful(probe, entry)
    key = literal('keys.json')[index]
    item = next(m for m in value['delivery']['remaining'] if m['key'] == key)
    assert isinstance(item['address'], str) and item['address']
    return item['address']


def read_request(packet, material_address, selection_range=None):
    result = literal('read-request.json')
    for key in ('selection', 'context', 'stage'):
        result[key] = deepcopy(packet[key])
    result.update(address=material_address, range=selection_range)
    return result
