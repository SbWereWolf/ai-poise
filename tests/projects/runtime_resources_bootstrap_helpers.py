"""Real installed producer and independent initial-publication observations."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from poise.common import load_config, PoiseError
from .test_update import installed_project, project_revision, update_tools


@pytest.fixture(scope='module')
def bootstrap_python(tmp_path_factory):
    root = tmp_path_factory.mktemp('bootstrap installed candidate')
    source = root / 'source'
    source.mkdir()
    repo = Path(__file__).resolve().parents[2]
    shutil.copytree(repo / 'src', source / 'src')
    shutil.copyfile(repo / 'pyproject.toml', source / 'pyproject.toml')
    wheels = root / 'wheels'
    subprocess.run([sys.executable, '-B', '-m', 'pip', 'wheel', '--no-build-isolation',
                    '--no-deps', '--wheel-dir', str(wheels), str(source)],
                   check=True, capture_output=True)
    environment = root / 'environment'
    subprocess.run([sys.executable, '-B', '-m', 'venv', str(environment)],
                   check=True, capture_output=True)
    wheel, = wheels.glob('*.whl')
    python = environment / 'bin/python'
    subprocess.run([str(python), '-B', '-m', 'pip', 'install', '--no-index', '--no-deps',
                    str(wheel)], check=True, capture_output=True)
    proof = subprocess.run([str(python), '-I', '-B', '-c',
                            "import importlib.metadata; print(importlib.metadata.version('ai-poise'))"],
                           check=True, capture_output=True, text=True)
    assert proof.stdout == '1.0.2\n' and proof.stderr == ''
    (root / 'wheel.sha256.txt').write_text(hashlib.sha256(wheel.read_bytes()).hexdigest() + '\n')
    return python


def arrange_bootstrap(project, tmp_path, *, native_hooks=False):
    from goal_config.helpers import settings as editor_settings, request as editor_request
    from poise.composition import goal_config_tools
    project = deepcopy(project)
    if native_hooks:
        project['cfg']['batch']['message_source'] = {'id': 'codex-hook-main', 'mode': 'runtime_event'}
        project['cfg']['accounting']['time_mode'] = 'reported'
    project['cfg'].pop('runtime_resources', None)
    project['cfg']['preserved_unknown'] = {'nested': ['original', 17, None]}
    settings, path, _ = installed_project(project)
    root, cfg, _ = load_config(path)
    state = Path(cfg['paths']['state'])
    if not state.is_absolute():
        state = root / state
    state.mkdir(parents=True, exist_ok=True)
    (state / 'operator.bin').write_bytes(b'\x00\xffexisting operator work\x80\n')
    editor, selection = editor_settings(root, {'writing': 'config/processes/writing.json'})
    head = goal_config_tools(editor).apply_batch(
        editor_request('create', 'writing', None, [], selection=selection))
    assert head['status'] == 'applied'
    resources = {'project_setup': str(settings), 'hooks': [],
                 'preserved_unknown_resource': {'future': ['keep', 42]}}
    packet = {
        'schema': 'project-runtime-resources-bootstrap-1',
        'request_id': 'initial-resource-publication', 'config_path': str(path),
        'expected_revision': project_revision(path), 'runtime_resources': resources,
        'receipt_path': 'operations/initial-resource-publication.json',
    }
    return {'settings': settings, 'config_path': path, 'root': root, 'state': state,
            'config': cfg, 'packet': packet, 'resources': resources,
            'editor': editor, 'head': head}


def files(root):
    return {str(path.relative_to(root)): path.read_bytes()
            for path in root.rglob('*') if path.is_file()}


def bootstrap_cli(python, case, packet=None):
    process = subprocess.run([
        str(python), '-I', '-B', '-m', 'poise', 'project-config',
        '--settings', str(case['settings']),
    ], input=json.dumps(case['packet'] if packet is None else packet),
        capture_output=True, text=True, cwd=case['root'])
    assert process.stderr == '', process.stderr
    assert process.stdout.strip(), 'Installed producer returned no structured result'
    return process, json.loads(process.stdout)


def apply_required(case):
    try:
        return update_tools(case['settings']).apply(case['packet'])
    except PoiseError as exc:
        raise AssertionError('The explicit initial-publication operation refused valid input: ' + str(exc)) from exc


def assert_only_publication(case, before):
    after = files(case['root'])
    manifest = str(case['config_path'].relative_to(case['root']))
    assert set(before) <= set(after)
    for name, content in before.items():
        if name != manifest:
            assert after[name] == content, 'Bootstrap changed unrelated material: ' + name
    for name in set(after) - set(before):
        assert name.startswith('operations/'), 'Bootstrap allocated non-publication state: ' + name
    expected = deepcopy(case['config'])
    expected['runtime_resources'] = case['resources']
    assert json.loads(case['config_path'].read_text()) == expected
