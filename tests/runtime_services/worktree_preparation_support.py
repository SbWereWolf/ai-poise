"""Isolated arrangements and independent observations for P004 integration."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

from batch.helpers import request
from conftest import WorkPoise, git, write_json
from poise.application.work import WorkTools

FIXTURES = Path(__file__).parent / 'fixtures/worktree_preparation'


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def arrange(project, monkeypatch):
    monkeypatch.syspath_prepend(str(FIXTURES))
    operator = project['root'] / 'operator'
    operator.mkdir()
    source = operator / 'selected-source.txt'
    source.write_text('selected input Ω\n', encoding='utf-8')
    control = write_json(operator / 'control.json', read_json(FIXTURES / 'control.json'))
    events = operator / 'events.jsonl'
    configuration = read_json(FIXTURES / 'configuration.json')
    configuration['parameters'] = {
        'python': sys.executable, 'command': str(FIXTURES / 'command.py'),
        'catalog': str(operator / 'catalog'), 'events': str(events),
        'control': str(control), 'input_source': str(source), 'label': 'literal Ω $VALUE with spaces',
    }
    path = write_json(operator / 'worktree-preparation.json', configuration)
    project['cfg']['worktree_preparation'] = {'configuration': str(path)}
    write_json(project['config_path'], project['cfg'])
    # Environment working data is not Task-authored source or a commit input.
    (project['app'] / '.gitignore').write_text('selected/input.txt\neffect.txt\n', encoding='utf-8')
    git(project['app'], 'add', '.gitignore')
    git(project['app'], 'commit', '-m', 'Declare fixture environment working paths')
    runtime = WorkPoise(project['config_path'], 'P004-EXECUTOR')
    return {'project': project, 'runtime': runtime, 'tools': WorkTools(runtime),
            'configuration': path, 'source': source, 'control': control, 'events': events,
            'base': git(project['app'], 'rev-parse', 'HEAD')}


def boot(case, *, again=False, request_id=None, tools=None):
    body = read_json(FIXTURES / 'bootstrap-input.json')
    body['task'] = {'id': case['project']['task']['id']} if again else deepcopy(case['project']['task'])
    if request_id is not None:
        body['preparation_request_id'] = request_id
    return (tools or case['tools']).invoke(request('bootstrap', body))


def events(case):
    path = case['events']
    return [] if not path.exists() else [json.loads(line) for line in path.read_text().splitlines()]


def operations(case):
    return [row['operation'] for row in events(case)]


def set_decision(case, operation, *, status='not_ready', code=17, malformed=False):
    control = read_json(case['control'])
    control['operations'][operation] = {'status': status, 'exit_code': code, 'malformed': malformed}
    write_json(case['control'], control)


def receipt(reply):
    declaration = reply['preparation']
    path = Path(declaration['receipt_path'])
    assert hashlib.sha256(path.read_bytes()).hexdigest() == declaration['receipt_sha256']
    result = read_json(path)
    assert result['schema'] == 'poise/worktree-preparation/v1'
    assert result['attempt_id'] == declaration['attempt_id']
    return result


def task_row(case):
    with sqlite3.connect(case['runtime'].store.path) as connection:
        return connection.execute(
            'SELECT id,status,stage_index,iteration,claimed_by FROM tasks WHERE id=?',
            (case['project']['task']['id'],)).fetchone()


def registered(case):
    with sqlite3.connect(case['runtime'].store.path) as connection:
        return connection.execute(
            'SELECT a.path,a.digest FROM artifacts a JOIN task_artifacts t '
            'ON a.id=t.artifact_id WHERE t.task_id=? ORDER BY a.path',
            (case['project']['task']['id'],)).fetchall()


def material_bytes(case):
    return {path: Path(path).read_bytes() for path, _ in registered(case)}


def assert_registered_materials(case, report):
    records = dict(registered(case))
    required = [report['configuration']['snapshot_path']]
    for operation in report['operations']:
        for command in operation['commands']:
            for stream in ('stdout', 'stderr'):
                path = command[stream + '_path']
                content = Path(path).read_bytes()
                assert hashlib.sha256(content).hexdigest() == command[stream + '_sha256']
                assert path in records
                assert records[path] == command[stream + '_sha256']
                required.append(path)
    assert all(path in records for path in required)
    snapshot = Path(report['configuration']['snapshot_path']).read_bytes()
    assert hashlib.sha256(snapshot).hexdigest() == report['configuration']['sha256']


def use_environment_handler(case):
    config = read_json(case['configuration'])
    config['handler'] = {'callable': 'apps.bootstrap.environment_result:handle',
                         'options': read_json(FIXTURES / 'plugin-options.json')}
    write_json(case['configuration'], config)
