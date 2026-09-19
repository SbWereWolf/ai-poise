"""Real SQLite repository and pure Task-family policy; independent test-owned rows."""
import json
from pathlib import Path
import sqlite3

import pytest
from poise.infrastructure.sqlite.database import SCHEMA
from poise.infrastructure.sqlite.tasks import SqliteTaskRepository

ROUTE = json.loads((Path(__file__).parent/'fixtures/duplicate_route.json').read_text())

@pytest.fixture
def db():
    db = sqlite3.connect(':memory:'); db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    for statement in SCHEMA: db.execute(statement)
    yield db
    db.close()


def add(db, tid, parent=None, stage=0, owner=None, status='active'):
    metadata={'process':ROUTE,'sprint_id':f'S-{tid}'}
    if parent is not None: metadata['duplicate_parent_id']=parent
    db.execute('INSERT INTO tasks VALUES(?,?,?,?,?,?,NULL,?)',
               (tid,status,stage,1,owner,7,json.dumps(metadata)))


def gate(db, current, actor, force=False):
    from poise.modules.tasks.duplicates import assess_duplicate_start
    return assess_duplicate_start(SqliteTaskRepository(db).read_duplicate_family(current),current,actor,
                                  force_duplicate_start=force)


def test_parent_children_and_sisters_use_one_identical_batch(db):
    add(db,'P',stage=2,owner='s1');add(db,'C','P',4,'s2');add(db,'B','P',1)
    reader=SqliteTaskRepository(db)
    expected=reader.read_duplicate_family('P')
    assert expected==reader.read_duplicate_family('C')==reader.read_duplicate_family('B')
    assert expected['parent_id']=='P'
    assert [m['task_id'] for m in expected['members']]==['B','C','P']
    assert expected['members'][1]['stage_id']=='short_b'
    assert expected['members'][1]['session_id']=='s2'
    assert expected['members'][1]['version']==7


def test_three_reads_independent_of_child_count_and_no_writes(db):
    add(db,'P')
    for i in range(70):add(db,f'C{i:03}','P')
    trace=[];db.set_trace_callback(trace.append);before=db.total_changes
    result=SqliteTaskRepository(db).read_duplicate_family('P')
    assert len(result['members'])==71
    assert len([s for s in trace if s.startswith('SELECT')])==3
    assert db.total_changes==before and db.in_transaction


@pytest.mark.parametrize('force',[False,True])
def test_only_nearest_tasks_returned_and_force_does_not_bypass_lead(db,force):
    add(db,'P',stage=0,owner='p');add(db,'B','P',0,'b');add(db,'C','P',1,'c')
    add(db,'D','P',2,'d');add(db,'E','P',4,'e')
    r=gate(db,'P','p',force)
    assert (r['allowed'],r['reason'],r['distance'],r['minimum'])==(False,'family_ahead',3,1)
    assert [x['task_id'] for x in r['relatives']]==['D','E']


def test_prospective_current_claim_and_force_are_not_persisted(db):
    add(db,'P',stage=2,owner='one');add(db,'C','P',4)
    assert gate(db,'C','two')['reason']=='duplicate_sessions'
    assert gate(db,'C','two',True)['allowed'] is True
    assert gate(db,'C','two')['allowed'] is False
    assert db.execute("SELECT claimed_by FROM tasks WHERE id='C'").fetchone()[0] is None


def test_first_session_can_start_without_false_initial_collision(db):
    add(db,'P');add(db,'C','P')
    assert gate(db,'C','one')['allowed'] is True
    db.execute("UPDATE tasks SET claimed_by='one' WHERE id='C'")
    assert gate(db,'P','two')['reason']=='duplicate_sessions'


def test_unowned_advanced_relative_still_blocks(db):
    add(db,'P',stage=0);add(db,'C','P',2)
    assert gate(db,'P','one',True)['reason']=='family_ahead'


def test_rework_rereads_current_position_not_historical_stage(db):
    add(db,'P',stage=2,owner='one');add(db,'C','P',1)
    assert gate(db,'P','one')['allowed']
    db.execute("UPDATE tasks SET stage_index=0,iteration=2,version=8 WHERE id='P'")
    assert gate(db,'P','one',True)['reason']=='family_ahead'


@pytest.mark.parametrize('state,expected',[('completed','family_ahead'),('cancelled','eligible')])
def test_terminal_outcomes(db,state,expected):
    add(db,'P',stage=0);add(db,'C','P',2,status=state)
    assert gate(db,'P','one')['reason']==expected


def test_foreign_owner_and_invalid_flag_cannot_be_overridden(db):
    add(db,'P',stage=2,owner='one');add(db,'C','P',4)
    assert gate(db,'P','two',True)['reason']=='foreign_owner'
    with pytest.raises(ValueError,match='boolean'):gate(db,'P','one','true')


def test_missing_parent_is_an_error_not_an_ordinary_task(db):
    from poise.modules.foundation.errors import PoiseError
    add(db,'C','MISSING')
    with pytest.raises(PoiseError,match='Incomplete duplicate family'):
        SqliteTaskRepository(db).read_duplicate_family('C')


def test_ordinary_task_needs_no_family_comparison(db):
    add(db,'P',owner='one')
    assert SqliteTaskRepository(db).read_duplicate_family('P') is None


def test_reader_outside_uow_leaves_connection_without_transaction(db):
    add(db,'P');add(db,'D','P');db.commit()
    before=db.total_changes
    family=SqliteTaskRepository(db).read_duplicate_family('D')
    assert family['parent_id']=='P'
    assert db.total_changes==before and not db.in_transaction


def test_only_negative_termination_never_permits_start(db):
    process={'route':{'entry':'check'},'stages':[{'id':'check','handler':'check',
        'transitions':{'satisfied':'check','not_satisfied':None,'inconclusive':'check'},
        'rework_targets':['check'],'read_only':True,'allowed_paths':[]}]}
    add(db,'P');add(db,'D','P')
    db.execute("UPDATE tasks SET metadata=json_set(metadata,'$.process',json(?)) WHERE id='D'",(json.dumps(process),))
    decision=gate(db,'P','one',True)
    assert decision['allowed'] is False
    assert decision['reason']=='invalid_duplicate_distance'
    assert decision['invalid_task_ids']==['D']


def test_newborn_has_no_executable_distance_and_does_not_block_ready_family(db):
    add(db,'P',owner='one');add(db,'D','P',status='newborn')
    db.execute("UPDATE tasks SET metadata=json_set(metadata,'$.process',null) WHERE id='D'")
    decision=gate(db,'P','one')
    assert decision['allowed'] is True and decision['relatives']==[]


def test_distances_use_route_edges_not_stage_order(db):
    from copy import deepcopy
    from poise.modules.tasks.duplicates import positive_terminal_distances
    reversed_process=deepcopy(ROUTE)
    reversed_process['stages'].reverse()
    assert positive_terminal_distances(reversed_process)==positive_terminal_distances(ROUTE)
