"""Independent SQL arrangements for the public addressed Work read boundary.

Subject: Work show composition, packet admission and real SQLite read owners.
No production projection or serializer generates expected values.
"""
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from conftest import DeterministicClock, WorkPoise
from poise.application.work import WorkTools
from poise.common import PoiseError
from poise.runtime import Poise


FIXTURES = Path(__file__).parent / 'fixtures' / 'task-addressed-read'


def fixture(name):
    return json.loads((FIXTURES / name).read_text(encoding='utf-8'))


def packet(needle='READ-ALPHA', query_id='selected'):
    result = fixture('request.json')
    result['input']['queries'][0].update(task_id=needle, id=query_id)
    return result


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def state_path(project):
    return project['root'] / project['cfg']['paths']['state'] / project['cfg']['paths']['database']


def metadata(identifier, goal, *, reader=False):
    name = ('reader-metadata.json' if reader else
            'beta-metadata.json' if identifier == 'READ-BETA' else 'active-metadata.json')
    value = fixture(name)
    value['contract'].update(id=identifier, goal=goal)
    value['goal'] = goal
    return value


def insert_task(db, identifier, goal, *, state='active', actor=None, reader=False):
    value = metadata(identifier, goal, reader=reader)
    stage = 'reader_step' if reader else 'beta_step' if identifier == 'READ-BETA' else 'work'
    if state == 'newborn':
        value = fixture('newborn-metadata.json')
        value['goal'] = value['newborn']['draft']['goal'] = goal
    db.execute('INSERT INTO tasks(id,status,stage_index,iteration,claimed_by,version,'
               'current_submission_id,metadata) VALUES(?,?,0,2,?,17,NULL,?)',
               (identifier, state, actor, encode(value)))
    if actor is not None:
        db.execute('INSERT INTO sessions(id,task_id) VALUES(?,?)', (actor, identifier))
    db.execute('INSERT INTO task_events(task_id,version,at,data) VALUES(?,17,?,?)',
               (identifier, '2026-11-03T00:00:00+00:00',
                encode({'event': 'fixture_saved', 'stage': stage, 'iteration': 2})))
    if state == 'newborn':
        return
    route = fixture('route.json')
    if stage != 'work':
        route = json.loads(encode(route).replace('"work"', '"' + stage + '"'))
    if reader or state in ('completed', 'cancelled'):
        route['feedback'] = {'findings': [], 'resolutions': [], 'decisions': []}
    db.execute('INSERT INTO task_workflows(task_id,data) VALUES(?,?)',
               (identifier, encode(route)))
    content = fixture('content.json')
    if stage != 'work':
        content = json.loads(encode(content).replace('"work"', '"' + stage + '"'))
    db.execute('INSERT INTO content_contracts(task_id,version,data) VALUES(?,0,?)',
               (identifier, encode(content)))
    db.execute('INSERT INTO task_proofs(task_id,data) VALUES(?,?)',
               (identifier, encode({'book': {'batches': [], 'arguments': [], 'decisions': []},
                                    'input': None, 'assessment': None})))
    seq = db.execute('INSERT INTO submissions(task_id,stage,iteration,digest,data) '
                     'VALUES(?,?,2,?,?)', (identifier, stage, 'c' * 64, '{}')).lastrowid
    db.execute('UPDATE tasks SET current_submission_id=? WHERE id=?', (seq, identifier))
    db.execute('INSERT INTO section_layers(submission_id,task_id,section_id,content,content_state) '
               'VALUES(?,?,?, ?,?)', (seq, identifier, 'notes',
                                     'Материал читателя.' if reader else 'Материал A.', 'populated'))
    execution = fixture('execution.json')
    execution['branch'] = f'tasks/{identifier}'
    if state in ('completed', 'cancelled'):
        execution['worktree'] = None
        execution['last_report'] = {'commit': 'a' * 40}
    db.execute('INSERT INTO task_execution(task_id,data,version) VALUES(?,?,0)',
               (identifier, encode(execution)))


def initialize(project):
    # Existing infrastructure initializes a disposable schema; subject behavior
    # is arranged by direct SQL afterwards, never by bootstrapping the target.
    runtime = WorkPoise(project['config_path'], 'fixture-initializer')
    return state_path(project), runtime


def seed_scene(project, *, reader_current=True, extra=True):
    path, initializer = initialize(project)
    with closing(sqlite3.connect(path)) as db:
        db.execute('PRAGMA foreign_keys=ON')
        insert_task(db, 'READ-ALPHA', 'Адресат A.', actor='owner-alpha')
        insert_task(db, 'READ-BETA', 'Адресат B.', actor='owner-beta')
        if extra:
            insert_task(db, 'READ-ALPHA-CHILD', 'Дочерняя запись.', actor='owner-child')
            insert_task(db, "LITERAL-%_[]*?-Q'1", 'Буквальные символы.', actor='owner-literal')
        if reader_current:
            insert_task(db, 'CURRENT-OTHER', 'Текущая задача читателя.',
                        actor='reader', reader=True)
        else:
            db.execute('INSERT INTO sessions(id,task_id) VALUES(?,NULL)', ('reader',))
        db.commit()
    return path, initializer


def read_tools(project, actor='reader'):
    return WorkTools(Poise(project['config_path'], actor, DeterministicClock(), read_only=True))


def addressed(tools, needle='READ-ALPHA'):
    try:
        result = tools.invoke(packet(needle))
    except PoiseError as error:
        pytest.fail(f'ADDRESS_QUERY_REJECTED: {error}', pytrace=False)
    assert result['status'] == 'read_only'
    return result['results'][0]['value']


def snapshot(path):
    """All fixture business rows, including ownership and journals, independently read."""
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as db:
        tables = db.execute("SELECT name FROM sqlite_master WHERE type='table' "
                            "AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall()
        result = {}
        for (name,) in tables:
            quoted = '"' + name.replace('"', '""') + '"'
            columns = [row[1] for row in db.execute(f'PRAGMA table_info({quoted})')]
            selected = ','.join('"' + col.replace('"', '""') + '"' for col in columns)
            result[name] = db.execute(f'SELECT {selected} FROM {quoted} ORDER BY {selected}').fetchall()
        return result


def cli(project, request):
    # Synthetic caller belongs only to this disposable test installation.
    from batch.test_public_read_transactions import environment
    process = subprocess.run([sys.executable, '-B', '-m', 'poise', 'work'],
                             input=encode(request), text=True, capture_output=True,
                             env=environment(project), timeout=20)
    envelope = json.loads(process.stdout) if process.stdout else None
    full = (json.loads(Path(envelope['response_path']).read_text())
            if envelope is not None and 'response_path' in envelope else envelope)
    return process, envelope, full


def seed_retired(db, identifier, root):
    delivery = fixture('retired-delivery.json')
    assert identifier == delivery['task']
    delivery['root'] = str(root)
    db.execute('INSERT INTO journal(at,session_id,task_id,event,data) VALUES(?,?,?,?,?)',
               ('2026-11-03T00:00:00+00:00', 'fixture-owner', identifier,
                'task_delivery.state', encode(delivery)))


def assert_values(actual, expected):
    """Exact named public values; never serialize an internal subject as an oracle."""
    assert {key: actual[key] for key in expected} == expected
