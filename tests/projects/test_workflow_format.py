"""Exact current data rename with independent complete fixtures and SQL oracles."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sqlite3

import pytest

from poise.modules.foundation.errors import PoiseError


def fixture():
    return json.loads((Path(__file__).parent / 'fixtures/workflow-current-format.json').read_text())


def arranged():
    source = fixture()
    db = sqlite3.connect(':memory:', isolation_level=None)
    db.execute('CREATE TABLE tasks(id TEXT PRIMARY KEY,status TEXT,stage_index INTEGER,iteration INTEGER,claimed_by TEXT,version INTEGER,metadata TEXT)')
    db.execute('CREATE TABLE sprints(id TEXT PRIMARY KEY,project TEXT,state TEXT,revision INTEGER,data TEXT)')
    db.execute('CREATE TABLE journal(seq INTEGER PRIMARY KEY,data TEXT)')
    db.execute('CREATE TABLE sprint_members(sprint_id TEXT,task_id TEXT)')
    db.execute('CREATE TABLE sprint_dependencies(sprint_id TEXT,predecessor TEXT,successor TEXT,kind TEXT)')
    for tid, entry in source['tasks'].items():
        columns = entry['columns']
        db.execute('INSERT INTO tasks(id,status,stage_index,iteration,claimed_by,version,metadata) VALUES(?,?,?,?,?,?,?)',
                   (tid, columns['status'], columns['stage_index'], columns['iteration'], columns['claimed_by'], columns['version'], json.dumps(entry['before'])))
    db.execute('INSERT INTO sprints(id,project,state,revision,data) VALUES(?,?,?,?,?)',
               ('S1', 'fixture-project', 'published', 7, json.dumps(source['sprint']['before'])))
    db.execute('INSERT INTO journal(seq,data) VALUES(?,?)', (1, json.dumps(source['history'])))
    db.executemany('INSERT INTO sprint_members(sprint_id,task_id) VALUES(?,?)', [('S1','T1'),('S1','T2'),('S1','T3')])
    db.executemany('INSERT INTO sprint_dependencies(sprint_id,predecessor,successor,kind) VALUES(?,?,?,?)',
                   [('S1','T1','T2','completion'),('S1','T2','T3','result')])
    return db


def current(db):
    return {
        'tasks': db.execute('SELECT id,status,stage_index,iteration,claimed_by,version,metadata FROM tasks ORDER BY id').fetchall(),
        'sprints': db.execute('SELECT id,project,state,revision,data FROM sprints ORDER BY id').fetchall(),
        'journal': db.execute('SELECT seq,data FROM journal ORDER BY seq').fetchall(),
        'members': db.execute('SELECT sprint_id,task_id FROM sprint_members ORDER BY task_id').fetchall(),
        'dependencies': db.execute('SELECT sprint_id,predecessor,successor,kind FROM sprint_dependencies ORDER BY predecessor').fetchall(),
    }


def expected_rows(db):
    sha = lambda text: hashlib.sha256(text.encode()).hexdigest()
    return {
        'tasks': {tid: {'version': version, 'sha256': sha(data)} for tid, version, data in db.execute('SELECT id,version,metadata FROM tasks')},
        'sprints': {sid: {'revision': revision, 'sha256': sha(data)} for sid, revision, data in db.execute('SELECT id,revision,data FROM sprints')},
    }


def convert(db, expected):
    from poise.infrastructure.workflow_format import rename_current_workflow_rows
    return rename_current_workflow_rows(db, expected)


def assert_complete_conversion(db, before):
    expected = fixture()
    after = current(db)
    for old, new in zip(before['tasks'], after['tasks'], strict=True):
        assert new[:6] == old[:6]
        assert json.loads(new[6]) == expected['tasks'][new[0]]['after']
    assert after['sprints'][0][:4] == before['sprints'][0][:4]
    assert json.loads(after['sprints'][0][4]) == expected['sprint']['after']
    assert after['journal'] == before['journal']
    assert after['members'] == [('S1','T1'),('S1','T2'),('S1','T3')]
    assert after['dependencies'] == [('S1','T1','T2','completion'),('S1','T2','T3','result')]
    return after


def test_current_rows_keep_complete_distinct_graphs_claims_and_sprint_relations():
    db = arranged(); before = current(db)
    result = convert(db, expected_rows(db))
    assert result == {'tasks':['T1','T2','T3'], 'sprints':['S1']}
    assert_complete_conversion(db, before)


def test_current_key_collision_refuses_before_any_row_write():
    db = arranged(); value = deepcopy(fixture()['tasks']['T2']['before']); value['workflow'] = {'stages': []}
    db.execute('UPDATE tasks SET metadata=? WHERE id=?', (json.dumps(value), 'T2'))
    before = current(db)
    with pytest.raises(PoiseError, match='collision'):
        convert(db, expected_rows(db))
    assert current(db) == before


def test_same_version_metadata_drift_preserves_later_change():
    db = arranged(); expected = expected_rows(db)
    value = deepcopy(fixture()['tasks']['T2']['before']); value['goal'] = 'Later current task edit.'
    db.execute('UPDATE tasks SET metadata=? WHERE id=?', (json.dumps(value), 'T2'))
    before = current(db)
    with pytest.raises(PoiseError, match='changed|drift'):
        convert(db, expected)
    assert current(db) == before


@pytest.mark.parametrize('change', ['insert_task','delete_task','insert_sprint','delete_sprint'])
def test_changed_complete_row_set_refuses_without_writes(change):
    db = arranged(); expected = expected_rows(db)
    if change == 'insert_task':
        db.execute('INSERT INTO tasks(id,status,stage_index,iteration,claimed_by,version,metadata) VALUES(?,?,?,?,?,?,?)',
                   ('T4','available',0,1,None,1,json.dumps(fixture()['tasks']['T1']['before'])))
    elif change == 'delete_task':
        db.execute('DELETE FROM tasks WHERE id=?', ('T2',))
    elif change == 'insert_sprint':
        db.execute('INSERT INTO sprints(id,project,state,revision,data) VALUES(?,?,?,?,?)',
                   ('S2','fixture-project','draft',1,json.dumps(fixture()['sprint']['before'])))
    else:
        db.execute('DELETE FROM sprints WHERE id=?', ('S1',))
    before = current(db)
    with pytest.raises(PoiseError, match='changed|drift'):
        convert(db, expected)
    assert current(db) == before


def test_same_revision_sprint_drift_preserves_later_change():
    db = arranged(); expected = expected_rows(db)
    value = deepcopy(fixture()['sprint']['before']); value['aggregate']['plan']['goal'] = 'Later current Sprint edit.'
    db.execute('UPDATE sprints SET data=? WHERE id=?', (json.dumps(value),'S1'))
    before = current(db)
    with pytest.raises(PoiseError, match='changed|drift'):
        convert(db, expected)
    assert current(db) == before


def test_exact_repeat_is_noop_after_complete_first_conversion():
    db = arranged(); before = current(db)
    first = convert(db, expected_rows(db))
    assert first == {'tasks':['T1','T2','T3'], 'sprints':['S1']}
    converted = assert_complete_conversion(db, before)
    assert convert(db, expected_rows(db)) == {'tasks':[], 'sprints':[]}
    assert current(db) == converted


def test_actual_repository_restores_own_task_after_explicit_legacy_conversion(project):
    from conftest import DeterministicClock
    from poise.runtime import Poise
    runtime = Poise(project['config_path'], 'workflow-current-format', DeterministicClock())
    runtime.bootstrap(json.loads(project['task_path'].read_text()))
    own = deepcopy(project['workflow'])
    with sqlite3.connect(runtime.store.path, isolation_level=None) as db:
        raw = db.execute('SELECT metadata FROM tasks WHERE id=?', ('T1',)).fetchone()[0]
        legacy = json.loads(raw)
        legacy.pop('workflow', None)
        legacy['process'] = deepcopy(own)
        legacy['registry_process'] = deepcopy(own)
        db.execute('UPDATE tasks SET metadata=? WHERE id=?', (json.dumps(legacy),'T1'))
        before = db.execute('SELECT status,stage_index,iteration,claimed_by,version,metadata FROM tasks WHERE id=?', ('T1',)).fetchone()
        expected = deepcopy(legacy)
        del expected['process']; del expected['registry_process']
        expected['workflow'] = deepcopy(own); expected['registry_workflow'] = deepcopy(own)
        convert(db, expected_rows(db))
        after = db.execute('SELECT status,stage_index,iteration,claimed_by,version,metadata FROM tasks WHERE id=?', ('T1',)).fetchone()
    assert after[:5] == before[:5]
    assert json.loads(after[5]) == expected
    with runtime.store.unit_of_work() as uow:
        restored = uow.tasks.load('T1')
    assert restored.state.status.value == before[0]
    assert restored.state.stage_index == before[1]
    assert restored.state.iteration == before[2]
    assert restored.state.claimed_by == before[3]
    assert restored.state.version == before[4]
    assert tuple(stage.stage_id for stage in restored.stages) == tuple(stage['id'] for stage in own['stages'])
    assert tuple(node.stage_id for node in restored.route.nodes) == tuple(stage['id'] for stage in own['stages'])


def test_newborn_without_own_graph_is_preserved_without_fabrication():
    db = arranged()
    newborn = {'id':'N1','goal':'Current unplanned task','newborn':{'draft':{'goal':'Current unplanned task'}},'unrelated':{'process':'opaque'}}
    db.execute('INSERT INTO tasks(id,status,stage_index,iteration,claimed_by,version,metadata) VALUES(?,?,?,?,?,?,?)',
               ('N1','newborn',0,0,None,2,json.dumps(newborn)))
    before = db.execute('SELECT id,status,stage_index,iteration,claimed_by,version,metadata FROM tasks WHERE id=?',('N1',)).fetchone()
    result = convert(db, expected_rows(db))
    assert result == {'tasks':['T1','T2','T3'],'sprints':['S1']}
    assert db.execute('SELECT id,status,stage_index,iteration,claimed_by,version,metadata FROM tasks WHERE id=?',('N1',)).fetchone() == before


def test_opaque_authorization_text_is_preserved_during_successful_conversion():
    db = arranged()
    value = deepcopy(fixture()['tasks']['T1']['before'])
    opaque = 'Historical authorization: revision_fields=[process]; preserve exact text.'
    value['restart_history'][0]['authorization'] = opaque
    value['restart_history'][0]['planning_revision']['authorization'] = opaque
    db.execute('UPDATE tasks SET metadata=? WHERE id=?', (json.dumps(value), 'T1'))
    expected = deepcopy(fixture()['tasks']['T1']['after'])
    expected['restart_history'][0]['authorization'] = opaque
    expected['restart_history'][0]['planning_revision']['authorization'] = opaque
    assert convert(db, expected_rows(db)) == {'tasks':['T1','T2','T3'], 'sprints':['S1']}
    observed = json.loads(db.execute('SELECT metadata FROM tasks WHERE id=?',('T1',)).fetchone()[0])
    assert observed == expected
