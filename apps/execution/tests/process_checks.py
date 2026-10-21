"""Test-owned process packets and real CLI/filesystem/SQLite observations."""
from copy import deepcopy
import io
import json
import os
from pathlib import Path
import subprocess
import sys

from store_checks import assert_same, snapshot, subject

APP = Path(__file__).resolve().parents[1]
ROOT = APP.parents[1]
PROVIDER = APP / 'bin/execution'


def fixture(name):
    return json.loads((APP / 'fixtures/process-contract.json').read_text())[name]


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')


def installation(root, *, process=True):
    config = fixture('config')
    if not process:
        del config['process']
        del config['operator_extension']
    path = root / 'execution.json'
    write_json(path, config)
    template = root / 'state/templates/starter.json'
    write_json(template, fixture('definition'))
    return path


def invoke(config, packet):
    environment = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
    # The actual executable selects env python3, so use this test's selected runtime.
    environment['PATH'] = str(Path(sys.executable).parent) + os.pathsep + environment['PATH']
    proc = subprocess.run([str(PROVIDER), '--config', str(config)],
                          input=json.dumps(packet, ensure_ascii=False) + '\n',
                          capture_output=True, text=True, env=environment, cwd=config.parent)
    assert proc.stderr == '', (proc.returncode, proc.stdout, proc.stderr)
    assert proc.stdout.endswith('\n') and proc.stdout.count('\n') == 1, proc.stdout
    value = json.loads(proc.stdout)
    assert value['schema'] == 'stride-execution/v1', value
    return proc.returncode, value


def create(*, goal='writing', request='create-1', revision=0, template=False, definition=None, changes=None):
    packet = fixture('create')
    value = packet['input']
    value.update(goal_type=goal, request_id=request, expected_revision=revision)
    value['source'] = ({'kind': 'template', 'template': {'id': 'starter'}} if template
                       else {'kind': 'definition', 'definition': fixture('definition') if definition is None else definition})
    value['changes'] = [] if changes is None else changes
    return packet


def edit(first, changes, *, request='edit-1', revision=None):
    return {'schema': 'stride-execution/v1', 'operation': 'process', 'input': {
        'action': 'edit', 'request_id': request,
        'expected_revision': first['revision'] if revision is None else revision,
        'goal_type': first['goal_type'], 'expected_process_revision': first['process_revision'],
        'changes': changes}}


def status(goal='writing'):
    return {'schema': 'stride-execution/v1', 'operation': 'process',
            'input': {'action': 'status', 'goal_type': goal}}


def published(outcome, *, revision, goal='writing', previous=None, changed=True):
    code, result = outcome
    assert code == 0, result
    assert set(result) == {'schema', 'status', 'revision', 'replayed', 'goal_type',
                           'previous_process_revision', 'process_revision', 'changed'}, result
    assert result['status'] == 'published', result
    assert type(result['revision']) is int and result['revision'] == revision, result
    assert_same(result['replayed'], False)
    assert_same(result['changed'], changed)
    assert result['goal_type'] == goal and result['previous_process_revision'] == previous, result
    assert isinstance(result['process_revision'], str) and result['process_revision'], result
    return result


def rejected(outcome, *tokens):
    code, result = outcome
    assert code == 2 and result['status'] == 'rejected', result
    reason = result.get('reason') or result.get('error', {}).get('message', '')
    assert isinstance(reason, str) and reason.strip(), result
    # Generic current capability/config refusal is RED, never proof of semantic validation.
    assert not ('unknown' in reason.lower() and 'operation' in reason.lower()), result
    assert not ('unknown [' in reason and 'process' in reason), result
    assert any(token.casefold() in reason.casefold() for token in tokens), (tokens, result)
    return result


def current(config, goal='writing'):
    return config.parent / 'state/processes' / (goal + '.json')


def document(config, goal='writing'):
    return json.loads(current(config, goal).read_text())


def store(config):
    module = subject()
    return module.Store(module.load_config(config))


def prepare_core(config):
    owner = store(config)
    with owner.locked():
        connection = owner.connect()
        connection.close()
    return owner


def state(config):
    """Observe complete own persisted tables and files without production serialization."""
    root = config.parent / 'state'
    values = {}
    for name in ['execution.sqlite', 'process-editor.sqlite']:
        path = root / name
        values[name] = snapshot(path) if path.is_file() else None
    values['files'] = {str(p.relative_to(root)): p.read_bytes()
                       for directory in ['processes', 'templates']
                       for p in (root / directory).rglob('*') if p.is_file()}
    return values


def setup(process, *, task='0042', revision=None, request='setup-1'):
    contract = fixture('contract')
    contract['task_id'] = task
    contract['process_ref'] = {'goal_type': process['goal_type'], 'revision': process['process_revision']}
    if process['goal_type'] == 'notes':
        contract['stage_expectations'] = [{'stage_id': 'capture', 'values': [], 'files': [], 'methods': []}]
        contract['evidence_plan'] = {'capture': {'subject_methods': {}, 'arguments': [], 'review_arguments': []}}
    return {'schema': 'stride-execution/v1', 'operation': 'apply', 'input': {
        'request_id': request, 'expected_revision': process['revision'] if revision is None else revision,
        'operations': [{'id': 'setup', 'kind': 'setup_execution', 'contract': contract}]}}


def applied(outcome, task='0042'):
    code, value = outcome
    assert code == 0 and value['status'] == 'applied', value
    assert value['results'][0]['task_id'] == task and value['results'][0]['generation'] == 1, value
    return value


def frozen(config, task, expected, contract):
    records = store(config).read_records(task)
    assert [row['kind'] for row in records] == ['contract_snapshot', 'process_snapshot'], records
    contract_record, process_record = records
    assert_same(contract_record['data']['contract'], contract)
    assert process_record['task_id'] == task and process_record['generation'] == 1, process_record
    # The public record value carries the materialized process document; no private class inspected.
    assert_same(process_record['data']['process'], expected)
    assert_same(process_record['data']['process_ref'], contract['process_ref'])
    return records


def in_process(config, packet):
    output = io.StringIO()
    code = subject().execute(config, io.BytesIO((json.dumps(packet) + '\n').encode()), output)
    return code, json.loads(output.getvalue())
