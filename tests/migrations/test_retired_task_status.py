"""Explicit retirement, not runtime aliases for an obsolete Task outcome."""
from copy import deepcopy
from dataclasses import replace
import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3

import pytest

from batch.helpers import request
from conftest import WorkPoise
from poise.application.work import WorkTools
from poise.common import PoiseError
from poise.modules.tasks.domain import TaskStatus, TERMINAL_TASK_STATUSES
from poise.modules.transfers.domain import validate_saved_work
from sprints.helpers import setup, draft, publish, task

ROOT = Path(__file__).resolve().parents[2]


def migration_tool():
    path = ROOT/'recovery-tools/retire_task_status.py'
    assert path.is_file(), 'Explicit one-way Task status migration entry point is missing'
    spec = importlib.util.spec_from_file_location('retire_task_status_test', path)
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    return tool


@pytest.fixture
def retired(project, tmp_path):
    setup(project)
    runtime = WorkPoise(project['config_path'], 'planner')
    tools = WorkTools(runtime)
    pending = draft(tools, [task(project, 'OLD'),task(project, 'NEW')])
    publish(tools, pending['revision'])
    # Exact legacy persisted input, never produced by the new domain/API.
    with runtime.store.transaction() as db:
        db.execute("UPDATE tasks SET status='superseded' WHERE id='OLD'")
        row = db.execute("SELECT data FROM sprints WHERE id='S'").fetchone()
        value = json.loads(row[0])
        value['aggregate']['decisions'].append({
            'kind':'task_replacement','source':'OLD','replacement':'NEW',
            'reason':'historical source intent','authorization':'historical permission'})
        db.execute('UPDATE sprints SET data=? WHERE id=?',(json.dumps(value),'S'))
        task_versions = {r[0]:r[1] for r in db.execute("SELECT id,version FROM tasks WHERE status='superseded'")}
        sprint_revisions = {r[0]:r[1] for r in db.execute("SELECT id,revision FROM sprints WHERE id='S'")}
    source = runtime.store.database.path
    plan = {'schema':'ai-poise-retired-task-status-1','request_id':'retire-1',
            'source_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),
            'task_versions':task_versions,'sprint_revisions':sprint_revisions,
            'reason':'retire removed Task outcome','authorization':'operator explicitly approved'}
    return runtime, source, tmp_path/'candidate.sqlite', plan


def test_domain_and_saved_work_reject_removed_outcome():
    assert {s.value for s in TERMINAL_TASK_STATUSES} == {'completed','cancelled'}
    with pytest.raises((ValueError,PoiseError)):
        TaskStatus('superseded')
    with pytest.raises(PoiseError, match='Unsupported saved Task state'):
        validate_saved_work([{'id':'OLD','status':'superseded','claimed_by':None}],set())


def test_repository_refuses_unknown_status_instead_of_accepting_legacy(retired):
    runtime, _, _, _ = retired
    with runtime.store.unit_of_work() as unit:
        with pytest.raises(PoiseError, match='Unsupported Task status'):
            unit.tasks.load('OLD')
    with pytest.raises(PoiseError, match='Unsupported Task status'):
        runtime.task_queries.record('OLD')


def test_one_way_candidate_keeps_source_and_immutable_history(retired):
    runtime, source, dest, plan = retired
    before = source.read_bytes()
    receipt = migration_tool().migrate(source, dest, plan)
    assert source.read_bytes() == before
    assert receipt['task_ids'] == ['OLD'] and receipt['sprint_ids'] == ['S']
    assert receipt['source_sha256'] == hashlib.sha256(before).hexdigest()
    assert receipt['candidate_sha256'] == hashlib.sha256(dest.read_bytes()).hexdigest()
    with sqlite3.connect(source) as old, sqlite3.connect(dest) as new:
        assert new.execute("SELECT status,version FROM tasks WHERE id='OLD'").fetchone() == ('cancelled',plan['task_versions']['OLD']+1)
        assert new.execute("SELECT metadata FROM tasks WHERE id='OLD'").fetchone() == old.execute("SELECT metadata FROM tasks WHERE id='OLD'").fetchone()
        for table in ('submissions','evidence','task_results','task_workflows','task_execution','artifacts','task_artifacts','sprint_members','sprint_dependencies','task_methods','content_contracts','task_proofs'):
            columns = ','.join('"'+r[1]+'"' for r in old.execute(f'PRAGMA table_info({table})'))
            assert old.execute(f'SELECT {columns} FROM {table}').fetchall() == new.execute(f'SELECT {columns} FROM {table}').fetchall(),table
        old_history = old.execute('SELECT seq,task_id,version,at,data FROM task_events ORDER BY seq').fetchall()
        assert new.execute('SELECT seq,task_id,version,at,data FROM task_events ORDER BY seq LIMIT ?', (len(old_history),)).fetchall() == old_history
        migrated = json.loads(new.execute("SELECT data FROM sprints WHERE id='S'").fetchone()[0])
        assert all(x['kind'] != 'task_replacement' for x in migrated['aggregate']['decisions'])
        audit = json.loads(new.execute("SELECT data FROM journal WHERE event='migration.retired_task_status'").fetchone()[0])
        assert audit['tasks'][0]['status'] == 'superseded'
        assert 'task_replacement' in audit['sprints'][0]['data']
        assert new.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        assert new.execute('PRAGMA foreign_key_check').fetchall() == []


@pytest.mark.parametrize('case,pattern',[
    ('sha','SHA'),('version','versions'),('revision','revisions'),
    ('authorization','authorization'),('incomplete','versions'),('existing','exists'),
    ('claim','claim'),('trigger','injected'),
])
def test_failed_migration_never_changes_source_or_publishes_candidate(retired,case,pattern):
    runtime, source, dest, plan = retired
    tool = migration_tool()
    if case == 'sha': plan['source_sha256'] = '0'*64
    elif case == 'version': plan['task_versions']['OLD'] += 1
    elif case == 'revision': plan['sprint_revisions']['S'] += 1
    elif case == 'authorization': plan['authorization'] = ''
    elif case == 'incomplete': plan['task_versions'] = {}
    elif case == 'existing': dest.write_bytes(b'foreign file')
    elif case in ('claim','trigger'):
        with runtime.store.transaction() as db:
            if case == 'claim': db.execute("UPDATE tasks SET claimed_by='unresolved-owner' WHERE id='OLD'")
            else: db.execute("CREATE TRIGGER refuse_retirement BEFORE UPDATE ON tasks BEGIN SELECT RAISE(ABORT,'injected migration rollback'); END")
        plan['source_sha256'] = hashlib.sha256(source.read_bytes()).hexdigest()
    before = source.read_bytes()
    with pytest.raises((ValueError,RuntimeError,sqlite3.DatabaseError), match=pattern):
        tool.migrate(source, dest, plan)
    assert source.read_bytes() == before
    assert dest.read_bytes() == b'foreign file' if case == 'existing' else not dest.exists()


def test_current_sprint_has_no_replacement_process_projection(project):
    setup(project); tools = WorkTools(WorkPoise(project['config_path'],'planner'))
    d = draft(tools,[task(project,'A')]); publish(tools,d['revision'])
    assert 'replacements' not in tools.runtime.sprint_tools.query('S','current')


def test_terminal_ledger_does_not_compile_an_obsolete_execution_contract(retired):
    runtime, source, dest, plan = retired
    migration_tool().migrate(source,dest,plan)
    # A historical terminal task may have no current executable registry or
    # active stage contracts. Inspection must not invent these or run gates.
    with sqlite3.connect(dest) as db:
        data = json.loads(db.execute("SELECT metadata FROM tasks WHERE id='OLD'").fetchone()[0])
        data['contract'].pop('stage_contracts',None)
        db.execute('UPDATE tasks SET metadata=? WHERE id=?',(json.dumps(data),'OLD'))
    from poise.infrastructure.sqlite.database import Database
    from poise.infrastructure.sqlite.queries import TaskQueries
    queries = TaskQueries(Database(dest,dest.with_suffix('.lock'),1,.01))
    ledger = queries.terminal_snapshot('OLD')
    assert ledger['status'] == 'cancelled'
    assert ledger['result_template'] is None
    assert ledger['metadata'] == data
    assert ledger['history'][-1]['event'] == 'retired_status_migrated'
    assert ledger['content']['contracts']


def test_runtime_code_has_no_removed_task_status_or_relation_branch():
    for path in (ROOT/'src/poise').rglob('*.py'):
        text = path.read_text()
        assert 'TaskStatus.SUPERSEDED' not in text, path
        assert "'superseded'" not in text and '"superseded"' not in text, path
        assert "'task_replacement'" not in text and '"task_replacement"' not in text, path


def test_repository_snapshot_rejects_retired_state_before_any_insert(retired):
    runtime, source, _, _ = retired
    before = source.read_bytes()
    with runtime.store.unit_of_work() as unit:
        row = dict(unit.tasks.db.execute("SELECT id,status,stage_index,iteration,claimed_by,version,current_submission_id,metadata FROM tasks WHERE id='OLD'").fetchone())
        row['id'] = 'IMPORTED'
        with pytest.raises(PoiseError, match='Unsupported Task status'):
            unit.tasks.restore_snapshot({'tasks': [row]}, {})
    assert source.read_bytes() == before


@pytest.mark.parametrize('case,pattern', [('wal','sidecar'),('symlink','symlink'),('schema','schema'),('binding','binding')])
def test_migration_rejects_unreviewed_storage_or_ownership(retired,case,pattern):
    runtime, source, dest, plan = retired
    if case == 'wal':
        Path(str(source)+'-wal').write_bytes(b'active writer marker')
    elif case == 'symlink':
        linked=source.parent/'linked.sqlite'; linked.symlink_to(source); source=linked
    else:
        with runtime.store.transaction() as db:
            if case == 'schema': db.execute('PRAGMA user_version=99')
            else: db.execute("INSERT OR REPLACE INTO sessions(id,task_id) VALUES(?,'OLD')",(runtime.session,))
        plan['source_sha256']=hashlib.sha256(source.read_bytes()).hexdigest()
    before=source.read_bytes()
    with pytest.raises(ValueError,match=pattern):
        migration_tool().migrate(source,dest,plan)
    assert source.read_bytes()==before and not dest.exists()
    if case=='wal': Path(str(source)+'-wal').unlink()
