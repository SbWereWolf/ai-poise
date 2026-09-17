"""Packet identity belongs to the same transaction as the verified result."""
from copy import deepcopy
import json
import sqlite3

import pytest

from conftest import WorkPoise, add_test
from poise.application.work import WorkTools
from poise.common import PoiseError
from poise.infrastructure.sqlite.work_packets import SqliteWorkPacketRepository
from .helpers import configure, bootstrap, result, request, text_artifact


TABLES = ('tasks','submissions','task_results','task_execution','task_events',
          'task_workflows','task_proofs','task_proof_layers','evidence','work_packets',
          'artifacts','task_artifacts','sprint_artifacts')


def snapshot(runtime):
    with runtime.store.transaction() as db:
        return {table:[tuple(row) for row in db.execute(f'SELECT * FROM {table} ORDER BY rowid')]
                for table in TABLES} | {'verification_journal':[tuple(row) for row in db.execute(
                    "SELECT * FROM journal WHERE event='stage.verified' ORDER BY seq")]}


@pytest.fixture
def prepared(project):
    configure(project)
    runtime=WorkPoise(project['config_path'],'atomic-executor')
    tools=WorkTools(runtime)
    context=bootstrap(tools,project);add_test(context['worktree'])
    packet=request('verify',{'result':result(context),'artifacts':[text_artifact(path='atomic-proof.md')]})
    return runtime,tools,context,packet


@pytest.mark.parametrize('failure',['packet','packet_after_insert','commit'])
def test_delivery_failure_rolls_back_the_entire_ready_delivery(prepared,monkeypatch,failure):
    runtime,tools,context,packet=prepared
    captured={};original=runtime.runner.verified
    def before_delivery(*args,**kwargs):
        captured['before']=snapshot(runtime)
        return original(*args,**kwargs)
    monkeypatch.setattr(runtime.runner,'verified',before_delivery)
    remember=SqliteWorkPacketRepository.remember
    def fail_packet(self,*args,**kwargs):
        if failure in ('packet_after_insert','commit'):
            remember(self,*args,**kwargs)
        if failure=='commit':
            # A deferred foreign-key failure happens on transaction COMMIT.
            self.db.execute('PRAGMA defer_foreign_keys=ON')
            self.db.execute("INSERT INTO task_artifacts(task_id,artifact_id) VALUES('absent-task','absent-artifact')")
        else: raise PoiseError('injected packet persistence failure')
    monkeypatch.setattr(SqliteWorkPacketRepository,'remember',fail_packet)
    with pytest.raises((PoiseError,sqlite3.DatabaseError)):
        tools.invoke(packet)
    assert 'before' in captured
    assert snapshot(runtime)==captured['before']
    assert runtime.current_task()['status']=='active'
    assert runtime.current_task()['last_report'] is None
    assert runtime.work_resources.packet(runtime.current_task()) is None
    # Exact retry after repairing storage must reuse existing observations.
    monkeypatch.setattr(SqliteWorkPacketRepository,'remember',remember)
    monkeypatch.setattr(runtime,'_execute_checks',lambda *a,**kw:pytest.fail('checks must not rerun'))
    delivered=tools.invoke(packet)
    assert delivered['status']=='verified'


def test_success_packet_and_report_are_visible_atomically(prepared,monkeypatch):
    runtime,tools,context,packet=prepared
    observed=[];remember=SqliteWorkPacketRepository.remember
    def inspect_transaction(self,*args,**kwargs):
        remember(self,*args,**kwargs)
        assert self.db.in_transaction
        task_id=context['task']
        assert self.db.execute('SELECT status FROM tasks WHERE id=?',(task_id,)).fetchone()[0]=='verified'
        assert self.db.execute('SELECT COUNT(*) FROM task_results WHERE task_id=?',(task_id,)).fetchone()[0]==1
        raw=self.db.execute('SELECT data FROM task_execution WHERE task_id=?',(task_id,)).fetchone()[0]
        assert json.loads(raw)['last_report']['status']=='verified'
        with sqlite3.connect(runtime.store.database.path) as outsider:
            observed.append(outsider.execute('SELECT status FROM tasks WHERE id=?',(task_id,)).fetchone()[0])
    monkeypatch.setattr(SqliteWorkPacketRepository,'remember',inspect_transaction)
    done=tools.invoke(packet)
    assert observed==['active']
    assert runtime.work_resources.packet(runtime.current_task())==runtime.packet_digest(packet['input'])
    replay=tools.invoke(deepcopy(packet))
    assert replay['replayed'] is True and replay['checks']==done['checks']


def test_exact_replay_and_different_packet_preserve_delivery(prepared,monkeypatch):
    runtime,tools,context,packet=prepared
    first=tools.invoke(packet);before=snapshot(runtime)
    monkeypatch.setattr(runtime,'_execute_checks',lambda *a,**kw:pytest.fail('replayed checks'))
    monkeypatch.setattr(runtime.work_resources,'factory',lambda:pytest.fail('replayed artifact materialization'))
    assert tools.invoke(packet)['replayed'] is True
    different=deepcopy(packet);different['input']['result']['sections']['report']='Changed claim'
    with pytest.raises(PoiseError,match='Different result|packet'):
        tools.invoke(different)
    assert snapshot(runtime)==before
    assert runtime.current_task()['last_report']['commit']==first['commit']


def test_restart_invalidates_only_mutable_packet_and_preserves_receipts(prepared,monkeypatch):
    runtime,tools,context,packet=prepared
    tools.invoke(packet);before=snapshot(runtime);current=runtime.current_task()
    original=SqliteWorkPacketRepository.invalidate
    def fail_after_delete(self,*args):
        original(self,*args)
        raise PoiseError('injected restart invalidation failure')
    monkeypatch.setattr(SqliteWorkPacketRepository,'invalidate',fail_after_delete)
    restart=request('task',{'action':'restart','request_id':'atomic-restart','task_id':context['task'],
             'expected_version':current['version'],'reason':'Repair invalid execution contract',
             'authorization':'User explicitly authorized restart'})
    with pytest.raises(PoiseError,match='invalidation'):
        tools.invoke(restart)
    assert snapshot(runtime)==before
    monkeypatch.setattr(SqliteWorkPacketRepository,'invalidate',original)
    assert tools.invoke(restart)['status']=='newborn'
    after=snapshot(runtime)
    assert after['work_packets']==[]
    for table in ('submissions','task_results','evidence'):
        assert after[table]==before[table]
    assert all(row in after['task_events'] for row in before['task_events'])


def test_direct_runtime_replay_requires_matching_explicit_identity(prepared):
    runtime,tools,context,packet=prepared
    tools.invoke(packet);before=snapshot(runtime)
    with pytest.raises(PoiseError,match='Different work packet'):
        runtime.verify(packet['input']['result'],packet_digest='f'*64)
    with pytest.raises(PoiseError,match='SHA-256'):
        runtime.verify(packet['input']['result'],packet_digest=None)
    assert snapshot(runtime)==before


def test_verified_replay_does_not_repair_a_missing_historical_packet(prepared):
    runtime,tools,context,packet=prepared
    tools.invoke(packet)
    with runtime.store.transaction() as db:
        db.execute('DELETE FROM work_packets WHERE task_id=?',(context['task'],))
    before=snapshot(runtime)
    with pytest.raises(PoiseError,match='Different result'):
        tools.invoke(packet)
    # Explicit null replay is read-only historical retrieval, never a packet write.
    assert tools.invoke(request('verify',{'result':None,'artifacts':[]}))['replayed'] is True
    assert snapshot(runtime)==before
