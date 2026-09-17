"""Task0144 incident: legacy0028 never blocks0124's unrelated acceptance."""
from copy import deepcopy
import sqlite3
from pathlib import Path

import pytest

from batch.helpers import configure, request, result
from conftest import WorkPoise, write_json
from poise.application.work import WorkTools
from poise.common import PoiseError
from poise.infrastructure.sqlite.database import Database


def task_rows(path, task_id):
    with sqlite3.connect(path) as db:
        names=[r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        data={'tasks':db.execute('SELECT * FROM tasks WHERE id=?',(task_id,)).fetchall()}
        for name in names:
            columns={r[1] for r in db.execute(f'PRAGMA table_info("{name}")')}
            if 'task_id' in columns and name!='sessions':
                data[name]=db.execute(f'SELECT * FROM "{name}" WHERE task_id=? ORDER BY rowid',(task_id,)).fetchall()
        return data


def prepare(project):
    configure(project)
    stage=deepcopy(project['process']['stages'][0]);stage['transitions']={'complete':None}
    process={**deepcopy(project['process']),'stages':[stage],
             'benefit':{'git_categories':['code','documentation'],'sections':['report']}}
    write_json(project['root']/'config/processes/development.json',process)
    project['cfg']['automatic_checks']=[]
    write_json(project['config_path'],project['cfg'])
    task=deepcopy(project['task'])
    task.update(methods=[],method_inputs=[],checks={stage['id']:[]},
                evidence_plan={stage['id']:{'subject_methods':{},'arguments':[],'review_arguments':[]}},
                stage_contracts=[task['stage_contracts'][0]])
    task['decomposition']['phases']=task['decomposition']['phases'][:1]
    contexts={}
    for tid,actor in [('0028','finished-owner'),('0124','current-owner')]:
        runtime=WorkPoise(project['config_path'],actor);tools=WorkTools(runtime)
        task['id']=tid
        ctx=runtime.bootstrap(deepcopy(task));contexts[tid]=ctx
        verified=tools.invoke(request('verify',{'result':result(ctx,'The durable section is the result.'),'artifacts':[]}))
        assert verified['status']=='verified'
        if tid=='0028':
            assert tools.invoke(request('accept',{}))['status']=='completed'
    return runtime.store.database.path,contexts


@pytest.mark.parametrize('binding_count',[0,1,2])
@pytest.mark.parametrize('terminal',[False,True],ids=['unfinished-conflict','completed-incident'])
def test_unrelated_accept_preserves_legacy_task_and_wip(project,binding_count,terminal):
    path,contexts=prepare(project)
    old_tree=Path(contexts['0028']['worktree'])
    (old_tree/'retained-wip.txt').write_text('Untracked foreign WIP remains intact')
    with sqlite3.connect(path) as db:
        db.execute('DROP INDEX tasks_single_claimant');db.execute('DROP INDEX sessions_single_worktree_owner')
        db.execute("UPDATE sessions SET task_id=NULL WHERE task_id='0028'")
        if not terminal:db.execute("UPDATE tasks SET status='verified' WHERE id='0028'")
        db.executemany("INSERT INTO sessions VALUES(?,'0028')",[(f'old-{n}',) for n in range(binding_count)])
        db.execute('PRAGMA user_version=12')
    before=task_rows(path,'0028')
    files={p.relative_to(old_tree):p.read_bytes() for p in old_tree.rglob('*') if p.is_file()}
    runtime=WorkPoise(project['config_path'],'current-owner');tools=WorkTools(runtime)
    shown=tools.invoke(request('show',{'queries':[{'id':'current','kind':'task'}]}))
    assert shown['status']=='read_only'
    assert tools.invoke(request('accept',{}))['status']=='completed'
    assert task_rows(path,'0028')==before
    assert {p.relative_to(old_tree):p.read_bytes() for p in old_tree.rglob('*') if p.is_file()}==files
    # Re-entering with a new process is harmless, even if a different component is pending.
    other=WorkPoise(project['config_path'],'taskless-reader')
    assert WorkTools(other).invoke(request('show',{'queries':[{'id':'read','kind':'task'}]}))['status']=='read_only'
    with sqlite3.connect(path) as db:
        expected=12 if not terminal and binding_count==2 else 13
        assert db.execute('PRAGMA user_version').fetchone()[0]==expected
        remaining=db.execute("SELECT COUNT(*) FROM sessions WHERE task_id='0028'").fetchone()[0]
        assert remaining==(0 if terminal else binding_count)
        assert db.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
        assert not db.execute('PRAGMA foreign_key_check').fetchall()


def test_startup_failure_after_migration_rolls_back_and_can_retry(project,monkeypatch):
    path,_=prepare(project)
    with sqlite3.connect(path) as db:
        db.execute('DROP INDEX tasks_single_claimant');db.execute('DROP INDEX sessions_single_worktree_owner')
        db.execute("UPDATE sessions SET task_id=NULL WHERE task_id='0028'")
        db.executemany("INSERT INTO sessions VALUES(?,'0028')",[('old-a',),('old-b',)])
        db.execute('PRAGMA user_version=12')
    before=task_rows(path,'0028')
    original=Database._upgrade_v12_ownership
    def interrupted(db):
        original(db)
        raise PoiseError('interrupted before migration commit')
    monkeypatch.setattr(Database,'_upgrade_v12_ownership',staticmethod(interrupted))
    with pytest.raises(PoiseError,match='interrupted'):
        WorkPoise(project['config_path'],'reader')
    assert task_rows(path,'0028')==before
    with sqlite3.connect(path) as db:
        assert db.execute('PRAGMA user_version').fetchone()[0]==12
        assert db.execute("SELECT COUNT(*) FROM sessions WHERE task_id='0028'").fetchone()[0]==2
        assert not db.execute("SELECT name FROM sqlite_master WHERE type='index' AND name IN ('tasks_single_claimant','sessions_single_worktree_owner')").fetchall()
    monkeypatch.setattr(Database,'_upgrade_v12_ownership',staticmethod(original))
    WorkPoise(project['config_path'],'reader')
    assert task_rows(path,'0028')==before
    with sqlite3.connect(path) as db:assert db.execute('PRAGMA user_version').fetchone()[0]==13
