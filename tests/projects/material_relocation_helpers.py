"""Disposable public-owner arrangement; expected bytes belong to the tests."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

from batch.helpers import request, result
from conftest import WorkPoise, bind_task_requirements, seed_fixture_requirements, write_json
from poise.application.work import WorkTools
from poise.common import PoiseError, load_config
from projects.test_update import (
    installed_project, project_revision, relocation, update_request, update_tools,
)
from verification.retention_helpers import consumer, file_item, source_file, start


STDOUT = b'retained-bytes=00ff61636365707465642d776865656c800a\n'
STDERR = b'relocation companion stderr\n'
PROOF = b'wheel verified with accepted bytes\n'
INPUT = b'\x00\xffaccepted-wheel\x80\n'


def required(call, *args, **kwargs):
    try:
        return call(*args, **kwargs)
    except PoiseError as exc:
        raise AssertionError(f'Required material relocation refused: {exc}') from exc


def release(tools, task_id='T1'):
    current = tools.invoke(request('show', {'queries': [{'id': 'task', 'kind': 'task'}]}))
    task = current['results'][0]['value']
    return tools.invoke(request('release', {
        'request_id': 'release-relocation-' + str(task['version']),
        'task_id': task_id, 'expected_version': task['version'],
        'reason': 'Release the disposable material owner for relocation.',
    }))


def prepare(project, tmp_path, *, released=True, native_hooks=False):
    # All configuration is test arrangement, before project setup or Task creation.
    project = deepcopy(project)
    project['cfg']['automatic_checks'] = []
    project['cfg']['paths']['worktrees'] = str(project['app'] / 'task-worktrees')
    project['cfg']['paths']['requirements_database'] = str(tmp_path / 'requirements.sqlite')
    project['cfg']['paths']['requirements_lock'] = str(tmp_path / 'requirements.lock')
    if native_hooks:
        project['cfg']['batch']['message_source'] = {'id': 'codex-hook-main', 'mode': 'runtime_event'}
        project['cfg']['accounting']['time_mode'] = 'reported'
    stage = json.loads((Path(__file__).parents[1] / 'verification/fixtures/retention_stage.json').read_text())
    project['process'].update(route={'entry': 'implementation'}, stages=[stage])
    write_json(project['root'] / 'config/processes/development.json', project['process'])
    settings, config_path, _ = installed_project(project)
    root, cfg, processes = load_config(config_path)
    project.update(root=root, config_path=config_path, cfg=cfg,
                   process=processes['development'],
                   requirements_registry=seed_fixture_requirements(root, cfg))
    marker = tmp_path / 'consumer-marker.txt'
    method = consumer(marker)
    method['argv'][-1] += "\nimport sys\nprint('relocation companion stderr', file=sys.stderr)\n"
    method['stderr_contains'] = ['relocation companion stderr']
    tools, context = start(project, marker, method)
    worktree = Path(context['worktree'])
    (worktree / 'src/double.py').write_text('def double(n):\n    return n * 2\n')
    source = source_file(tmp_path)
    payload = result(context, 'Retain exact material for relocation.')
    done = tools.invoke(request('verify', {'result': payload, 'artifacts': [file_item(source)]}))
    assert done['status'] == 'verified'
    receipt = deepcopy(done['checks'][0])
    assert Path(receipt['stdout']).read_bytes() == STDOUT
    assert Path(receipt['stderr']).read_bytes() == STDERR
    if released:
        release(tools)
    move = relocation(config_path, tmp_path / 'relocated-state')
    packet = update_request(config_path, project_revision(config_path),
                            state_relocation=move)
    return {
        'project': project, 'settings': settings, 'config_path': config_path,
        'tools': tools, 'context': context, 'done': done, 'receipt': receipt,
        'method': method, 'payload': payload, 'marker': marker, 'move': move,
        'packet': packet, 'source': Path(move['expected_source']),
        'destination': Path(move['destination']),
    }


def relocate(case):
    outcome = required(update_tools(case['settings']).apply, case['packet'])
    assert outcome['state_relocation']['status'] == 'completed'
    assert not case['source'].exists(), 'Original state must be retired by its owner'
    return fresh(case)


def fresh(case, session='relocation-reader'):
    return WorkTools(WorkPoise(case['config_path'], session))


def placed(case, original):
    # Test oracle: exact destination chosen by this test, never the product resolver.
    return case['destination'] / Path(original).relative_to(case['source'])


def immutable(runtime):
    with runtime.store.transaction() as db:
        return {
            'tasks': [tuple(r) for r in db.execute(
                'SELECT id,status,stage_index,iteration,version,current_submission_id,metadata FROM tasks ORDER BY id')],
            'submissions': [tuple(r) for r in db.execute(
                'SELECT seq,task_id,stage,iteration,digest,data FROM submissions ORDER BY seq')],
            'events': [tuple(r) for r in db.execute(
                'SELECT seq,task_id,version,at,data FROM task_events ORDER BY seq')],
        }


def checksum(content):
    return hashlib.sha256(content).hexdigest()


def empty_contract(case, identifier, sprint=None):
    contract = deepcopy(case['tools'].runtime.task_queries.record('T1')['contract'])
    contract.update(id=identifier, sprint_id=sprint, methods=[], method_inputs=[],
                    checks={'implementation': []})
    return bind_task_requirements(contract, case['project']['requirements_registry'])


def other_owner(case, identifier='OTHER', scope='task', *, released=True):
    tools = fresh(case, 'foreign-writer')
    context = tools.invoke(request('bootstrap', {
        'task': empty_contract(case, identifier), 'decision': None,
        'feedback': None, 'rework_stage': None,
    }))
    published = tools.invoke(request('artifacts', {'items': [{
        'scope': scope, 'path': 'foreign.bin',
        'source': {'kind': 'text', 'text': 'foreign owner material\n'},
    }]}))
    records = tools.runtime.validate_artifact_paths(
        published['artifact_paths'], tools.runtime.current_task())
    assert len(records) == 1
    record = records[0]
    if released:
        release(tools, identifier)
    return tools, context, record
