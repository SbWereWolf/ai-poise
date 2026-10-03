"""Transparent fixture arrangement; public work APIs are the subject."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys

from conftest import WorkPoise, bind_task_requirements, write_json
from batch.helpers import request, result
from poise.application.work import WorkTools
from poise.common import PoiseError


BYTES = b'\x00\xffaccepted-wheel\x80\n'
PRODUCER = {'task_id': 'PRODUCER', 'commit': 'a' * 40}
FIXTURES = Path(__file__).parent / 'fixtures'


def digest(data=BYTES):
    return hashlib.sha256(data).hexdigest()


def file_item(source, destination='inputs/accepted.whl', expected=None):
    return {'scope': 'task', 'path': destination,
            'source': {'kind': 'file', 'path': str(source),
                       'digest': digest() if expected is None else expected}}


def consumer(marker):
    return {
        'id': 'READ_WHEEL',
        'argv': [sys.executable, '-B', '-c',
                 (FIXTURES / 'retention_consumer.py').read_text()],
        'cwd': '.', 'environment': {'RUN_MARKER': str(marker)},
        'source_under_test': {'kind': 'external',
                              'reason': 'Read an explicitly retained external package.'},
        'verification_plan': {
            'responsibility': 'Verify retained exact package bytes.',
            'change_surface': ['src/**'], 'red_stages': [],
            'green_stages': ['implementation'], 'red_failure': None,
        },
        'expected_exit_code': 0,
        'stdout_contains': ['retained-bytes=00ff61636365707465642d776865656c800a'],
        'stderr_contains': [],
        'artifact_inputs': {
            'manifest_directory': 'artifacts/manifests',
            'files': [{
                'id': 'wheel', 'path': 'artifacts/inputs/accepted.whl',
                'digest': digest(), 'producer': deepcopy(PRODUCER),
                'input_ref': 'accepted-wheel', 'environment': 'ACCEPTED_WHEEL',
            }],
        },
    }


def invoke_required(tools, operation, inputs):
    try:
        return tools.invoke(request(operation, inputs))
    except PoiseError as exc:
        raise AssertionError(f'Required retention contract refused: {exc}') from exc


def start(project, marker=None, method=None):
    cfg = deepcopy(project['cfg'])
    cfg['automatic_checks'] = []
    write_json(project['config_path'], cfg)
    process = deepcopy(project['process'])
    stage = json.loads((FIXTURES / 'retention_stage.json').read_text())
    process.update(route={'entry': 'implementation'}, stages=[stage])
    write_json(project['root'] / 'config/processes/development.json', process)
    task = deepcopy(project['task'])
    task['methods'] = [] if marker is None else [consumer(marker) if method is None else method]
    task['method_inputs'] = [{
        'method_id': 'READ_WHEEL', 'repository_inputs': [], 'future_outputs': [],
        'reference_profile': {'runner': 'python',
                              'parser': 'inline-no-path-arguments', 'version': 1},
    }] if marker is not None else []
    task['checks'] = {'implementation': [] if marker is None else ['READ_WHEEL']}
    task['evidence_plan'] = {'implementation': {
        'subject_methods': {}, 'arguments': [], 'review_arguments': [],
    }}
    task['stage_contracts'] = [{
        'stage_id': 'implementation', 'allowed_paths': ['src/**'],
        'entry_requirements': [], 'exit_requirements': [],
    }]
    task['decomposition']['phases'] = [{
        'stage': 'implementation', 'skills': ['task-domain'], 'areas': [],
    }]
    bind_task_requirements(task, project['requirements_registry'])
    tools = WorkTools(WorkPoise(project['config_path'], 'producer'))
    context = invoke_required(tools, 'bootstrap', {
        'task': task, 'decision': None, 'feedback': None, 'rework_stage': None,
    })
    return tools, context


def source_file(tmp_path):
    source = tmp_path / 'producer-runtime' / 'accepted.whl'
    source.parent.mkdir()
    source.write_bytes(BYTES)
    return source


def verify_input(project, tmp_path):
    marker = tmp_path / 'execution-marker.txt'
    source = source_file(tmp_path)
    tools, context = start(project, marker)
    worktree = Path(context['worktree'])
    (worktree / 'src/double.py').write_text('def double(n):\n    return n * 2\n')
    done = invoke_required(tools, 'verify', {
        'result': result(context, 'Retained package verified.'),
        'artifacts': [file_item(source)],
    })
    assert done['status'] == 'verified'
    assert marker.read_text() == 'run\n'
    return tools, context, done, source, marker


def manifest(done, context):
    paths = done['acceptance_manifests']
    assert len(paths) == 1
    path = Path(paths[0]['path'])
    assert path.is_relative_to(Path(context['task_root']))
    assert hashlib.sha256(path.read_bytes()).hexdigest() == paths[0]['digest']
    assert any(item['path'] == str(path) for item in done['artifacts'])
    return path, json.loads(path.read_text())
