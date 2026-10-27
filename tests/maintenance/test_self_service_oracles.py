"""Test-assertion controls only; never evidence of product or SDK success.

Independent private SQLite/observation inputs exercise the real assertion
functions. A valid control passes before one forbidden observation is changed.
The actual integration tests still execute the real package and Poise CLI.
"""
import base64
from copy import deepcopy
import json
import os
from pathlib import Path
import sqlite3
import subprocess

import pytest

import test_self_service_profile as subject


FIXTURES = Path(__file__).parent / 'fixtures/self_service'


def reply(value, code=0):
    return subprocess.CompletedProcess([], code, json.dumps(value).encode(), b'')


@pytest.fixture
def control(tmp_path, monkeypatch):
    root = tmp_path / 'serviced'
    source = tmp_path / 'source'
    root.mkdir()
    source.mkdir()
    profile = root / 'operator/profile'
    (profile / 'selected-project').mkdir(parents=True)
    state = tmp_path / 'data'
    state.mkdir()
    databases = {}
    for name in ('tasks', 'requirements', 'telemetry'):
        path = state / (name + '.sqlite')
        with sqlite3.connect(path) as db:
            db.executescript((FIXTURES / (name + '.sql')).read_text())
        databases[name] = path
    selected = {'project': 'isolated', 'git': {'repository': str(root), 'base_ref': 'master'},
                'paths': {'state': str(state), 'database': str(databases['tasks']),
                          'requirements_database': str(databases['requirements'])},
                'accounting': {'storage': {'database': str(databases['telemetry'])}}}
    config = profile / 'selected-project/project.json'
    config.write_text(json.dumps(selected))
    sentinel = tmp_path / 'sentinel'
    sentinel.write_text('preserve')
    case = dict(root=root, source=source, state=state, profile=profile,
                python=root / '.venv/bin/python', env={}, sentinel=sentinel)
    stdout = base64.b64encode(json.dumps({'projects': [{'project': 'isolated',
        'config_path': str(config)}], 'errors': []}).encode()).decode()
    child = {'invocation': {'argv': [str(case['python']), '-P', '-s', '-B', '-m',
        'poise', 'project', 'list', '--settings', str(profile / 'setup.json')],
        'cwd': str(root), 'env': {'PYTHONPATH': os.pathsep.join([
            str(root / 'src'), str(root / '.runtime/dependencies'), str(root)])}},
        'stdout': {'encoding': 'base64', 'data': stdout}, 'exit_code': 0}
    run = {'operation': 'run', 'status': 'succeeded', 'requirements': [
        {'result': {'commands': [child]}}]}

    def invoke(case, operation):
        if operation == 'run':
            return reply({'status': 'ready', 'observations': [{'stdout': json.dumps(run)}]})
        return reply({'status': 'ready'})

    def dependency(argv, **kwargs):
        body = (str(root / '.runtime/dependencies/psutil/__init__.py') + '\nTrue\n').encode()
        return subprocess.CompletedProcess(argv, 0, body, b'')

    monkeypatch.setattr(subject, 'invoke', invoke)
    monkeypatch.setattr(subject, 'command', dependency)
    return dict(case=case, selected=selected, config=config, databases=databases, run=run)


def test_valid_observation_control_passes_original_positive_assertions(control):
    subject.test_actual_separate_preparation_checks_dependency_and_launch(control['case'])


def test_positive_oracle_rejects_wrong_private_repository(control, tmp_path):
    selected = deepcopy(control['selected'])
    selected['git']['repository'] = str(tmp_path / 'not-selected')
    control['config'].write_text(json.dumps(selected))
    with pytest.raises(AssertionError):
        subject.test_actual_separate_preparation_checks_dependency_and_launch(control['case'])


def test_positive_oracle_rejects_one_column_task_store(control):
    with sqlite3.connect(control['databases']['tasks']) as db:
        db.executescript('DROP TABLE tasks; CREATE TABLE tasks(id TEXT PRIMARY KEY);')
    with pytest.raises(sqlite3.OperationalError, match='status'):
        subject.test_actual_separate_preparation_checks_dependency_and_launch(control['case'])


@pytest.mark.parametrize('name', ['requirements', 'telemetry'])
def test_positive_oracle_requires_every_supported_store(control, name):
    control['databases'][name].unlink()
    with pytest.raises(AssertionError):
        subject.test_actual_separate_preparation_checks_dependency_and_launch(control['case'])


@pytest.mark.parametrize('name', ['tasks', 'requirements', 'telemetry'])
def test_store_location_oracle_rejects_other_private_data_root(control, tmp_path, name):
    path = tmp_path / (name + '-not-selected.sqlite')
    with sqlite3.connect(path) as db:
        db.executescript((FIXTURES / (name + '.sql')).read_text())
    selected = deepcopy(control['selected'])
    if name == 'telemetry':
        selected['accounting']['storage']['database'] = str(path)
    else:
        key = 'database' if name == 'tasks' else 'requirements_database'
        selected['paths'][key] = str(path)
    with pytest.raises(AssertionError):
        subject.configured_store_paths(control['case'], selected)


def test_one_shot_positive_oracle_rejects_an_extra_preparation_command(control):
    commands = control['run']['requirements'][0]['result']['commands']
    commands.append(deepcopy(commands[0]))
    commands[1]['invocation']['argv'] = ['forbidden-extra-preparation']
    with pytest.raises(AssertionError):
        subject.test_actual_separate_preparation_checks_dependency_and_launch(control['case'])


@pytest.mark.parametrize('side_effect', ['none', 'dependencies', 'runtime'])
def test_unprepared_run_assertions_detect_relevant_install_effects(control, monkeypatch, tmp_path, side_effect):
    case = dict(control['case'], state=tmp_path / 'not-prepared')

    def invoke(case, operation):
        if side_effect != 'none':
            path = (case['root'] / '.runtime/dependencies/forbidden-install'
                    if side_effect == 'dependencies' else
                    case['root'] / '.venv/lib/python3.15/site-packages/forbidden-install')
            path.parent.mkdir(parents=True)
            path.write_text('run installed a dependency')
        return reply({'status': 'error', 'actions': [],
                      'observations': [{'stdout': json.dumps(control['run'])}]}, 4)

    monkeypatch.setattr(subject, 'invoke', invoke)
    if side_effect == 'none':
        subject.test_run_does_not_prepare_missing_resources(case)
    else:
        with pytest.raises(AssertionError):
            subject.test_run_does_not_prepare_missing_resources(case)
