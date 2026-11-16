"""Public observation/measurement/telemetry contract; independent literal oracles.

Run with the project's Python3.13, --suite behavior --output-root ABS_UNIQUE.
Missing public entries are RED only after actual isolated environment controls.
Deeper assertions remain NOT REACHED on that baseline and run unchanged at GREEN.
"""
import argparse
from contextlib import closing
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import traceback

from generic_checks import APP, LOG as STATE_CALLS
from reader_support import exact
from session_support import CALLS as SESSION_CALLS
from state_sql import SQL_LOG
from observations_support import (Observations, MissingObservation, DiagnosticError,
    CALLS, CONTROLS, EFFECTS, FIX, literal, event, encoded, envelope, qualify, sql, wait_file,
    history_bound, admitted_or_dropped)

CASES = []


def case(name, requirements, operations, oracle, checks):
    def register(function):
        CASES.append({'id': name, 'requirements': requirements, 'operations': operations,
                      'oracle': oracle, 'downstream_assertions': checks, 'function': function})
        return function
    return register


def problem(body, code, status='rejected'):
    exact(body['status'], status)
    exact(body['error']['code'], code)
    assert body['error']['action'], body


def read_result(result):
    exact(set(result), {'schema', 'scope', 'source', 'as_of_seq', 'events', 'observed_events',
          'usage_events', 'totals', 'next_after', 'has_more', 'availability', 'coverage'})
    exact(result['schema'], 'poise-observations-read/v1')


@case('prepare-independent-repeat', ['Q01','Q08'], ['observations.prepare'],
      'design4 schema1; own store, no Session/Task prerequisite',
      ['own initial schema', 'same installation/version on repeat', 'no Session access'])
def prepare(app):
    app.config['session_store'] = str(app.root / 'no-session' / 'absent.sqlite')
    result = app.ok('observations.prepare')
    expected=literal('prepared');expected['database']=str(app.database)
    exact(result,expected)
    exact(app.ok('observations.prepare'), dict(result, created=False))
    exact(sql(app.database, 'SELECT id,schema_version,installation_id FROM observation_meta'),
          [[1,1,app.config['installation_id']]])
    exact(app.facts(), {'events':[], 'streams':[]})
    assert not (app.root/'no-session').exists()


@case('prepare-incompatible', ['Q01'], ['observations.prepare'],
      'design4 selected incompatible own store reports actual version, no repair',
      ['incompatible version refusal', 'bytes/rows retained'])
def incompatible(app):
    app.seed()
    sql(app.database, 'UPDATE observation_meta SET schema_version=? WHERE id=?', (9,1), write=True)
    before = app.database.read_bytes()
    body = app.require('observations.prepare')
    problem(body, 'observations_unavailable', 'unavailable')
    exact(sql(app.database, 'SELECT schema_version FROM observation_meta WHERE id=?', (1,)), [[9]])
    exact(app.database.read_bytes(), before)


@case('source-zero-unknown-offset', ['Q01'], ['observations.record','observations.read'],
      'Q01 literal source parameters and exact supplied timestamp offset',
      ['source payload preserved', 'zero != unknown', 'actual Session', 'no Task/Session effects'])
def provenance(app):
    app.seed()
    value = event(kind='source', value={'parameters':literal('source_parameters')})
    receipt = app.record(value)
    row = receipt['entries'][0]
    exact(row['session_id'], app.identity)
    exact(row['contribution'], None)
    exact(row['duration_ns'], None)
    result = app.read()
    exact(result['events'][0]['event'], value)
    exact(result['totals'], literal('unknown'))
    exact(result['usage_events'], 0)
    app.unchanged_session()
    exact(sql(app.database, 'SELECT name FROM sqlite_master WHERE type=? AND name NOT LIKE ? ORDER BY name',
              ('table','sqlite_%')), [['observation_events'],['observation_meta'],['observation_streams']])


@case('delta-and-exact-repeat', ['Q01','Q02'], ['observations.record','observations.read'],
      'Q02 independent 100+20=120; repeat preserves first receipt',
      ['120 total', 'same seq and Session on repeat', 'one event per business identity'])
def delta(app):
    app.seed()
    first = event()
    second = event(event_id='event-2', value={'sequence':2,'mode':'delta','counters':literal('counters_20')})
    initial = app.record(first, second)
    exact([row['contribution'] for row in initial['entries']],
          [literal('counters_100'),literal('counters_20')])
    exact(app.read()['totals'], literal('counters_120'))
    replay = app.record(first)
    expected = dict(initial['entries'][0], index=0, replayed=True)
    exact(replay['entries'][0], expected)
    exact(app.read()['totals'], literal('counters_120'))
    exact(len(app.facts()['events']), 2)
    app.unchanged_session()


@case('cumulative-baseline-order', ['Q02'], ['observations.record','observations.read'],
      'Q02 baseline1000->0 then1040->40, input receipt order retained',
      ['sort novel stream sequence', 'zero baseline', '40 incurred total', 'unknown details'])
def cumulative(app):
    app.seed()
    first = event(value={'sequence':10,'mode':'baseline','counters':literal('counters_1000')})
    second = event(event_id='event-2', value={'sequence':11,'mode':'cumulative','counters':literal('counters_1040')})
    result = app.record(second,first)
    exact([r['event_id'] for r in result['entries']], ['event-2','event-1'])
    exact(result['entries'][0]['contribution'], literal('counters_40'))
    zero_unknown = dict(literal('zero'), cached_input_tokens=None, reasoning_tokens=None)
    exact(result['entries'][1]['contribution'], zero_unknown)
    exact(app.read()['totals'], literal('counters_40'))
    exact(app.record(first)['entries'][0]['replayed'], True)


@case('baseline-alone-not-incurred', ['Q02'], ['observations.read'],
      'Q02 baseline alone is not known incurred usage', ['all usage totals unknown, not fabricated zero'])
def baseline_only(app):
    app.seed()
    value = event(value={'sequence':10,'mode':'baseline','counters':literal('counters_1000')})
    app.put(value, dict(literal('zero'), cached_input_tokens=None, reasoning_tokens=None))
    exact(app.read()['totals'], literal('unknown'))


@case('modality-identity', ['Q01','Q02'], ['observations.record','observations.read'],
      'design4 identity includes kind; same provider event may have source and usage facets',
      ['two facets stored', 'one usage contribution', 'each facet replayed once'])
def facets(app):
    app.seed()
    usage = event()
    source = event(kind='source', value={'parameters':literal('source_parameters')})
    result = app.record(usage,source)
    exact(result['count'], 2)
    exact([r['replayed'] for r in app.record(usage,source)['entries']], [True,True])
    exact(app.read()['totals'], literal('counters_100'))
    exact(len(app.facts()['events']),2)


@case('conflict-batch-atomicity', ['Q02'], ['observations.record'],
      'design4 conflicting identity cannot partially advance another stream/event',
      ['whole batch rejected', 'event rows unchanged', 'stream projection unchanged'])
def atomic(app):
    app.seed()
    app.put(event(), literal('counters_100'))
    before = app.facts()
    novel = event(event_id='novel', stream='new-stream')
    conflicting = event(observed_at='2026-11-16T10:00:01+05:00')
    body = app.require('observations.record', {'events':[novel,conflicting]})
    problem(body, 'observation_conflict')
    exact(app.facts(),before)


@case('stream-order-mode-and-time', ['Q02'], ['observations.record'],
      'accepted usage stream progression; exact replay precedes novel sequence validation',
      ['replayed older event allowed', 'novel old sequence rejected', 'mode/time conflicts atomic'])
def stream_order(app):
    app.seed()
    first = event()
    app.record(first)
    for update in ({'sequence':1}, {'mode':'cumulative','sequence':2}, {'sequence':2}):
        value = event(event_id='novel', value=dict(first['value'], **update))
        if update == {'sequence':2}:
            value['observed_at']='2026-11-16T09:59:59+05:00'
        before = app.facts()
        problem(app.require('observations.record', {'events':[value]}), 'observation_conflict')
        exact(app.facts(),before)
    exact(app.record(first)['entries'][0]['replayed'],True)


@case('closed-session-replay-only', ['Q01','Q02'], ['observations.record'],
      'design4 closed real Session may replay original facts but not create new facts',
      ['original Session retained', 'new batch rejected atomically', 'Session remains closed'])
def closed(app):
    app.seed()
    value = event()
    original = app.record(value)['entries'][0]
    sql(app.session.database, 'UPDATE poise_sessions SET status=? WHERE id=?', ('closed',app.identity), write=True)
    repeated = app.record(value)['entries'][0]
    exact(repeated,dict(original,replayed=True))
    before = app.facts()
    novel = event(event_id='new', value=dict(value['value'],sequence=2))
    problem(app.require('observations.record', {'events':[value,novel]}),'session_closed')
    exact(app.facts(),before)
    exact(sql(app.session.database,'SELECT status FROM poise_sessions WHERE id=?',(app.identity,)),[['closed']])


@case('uncertain-commit-public-retry', ['Q02'], ['observations.record','observations.read'],
      'receiver commits once, lost outcome is retried with identical events through public owner',
      ['reported unknown outcome', 'same event retry', 'single contribution'])
def uncertain(app):
    app.seed()
    value = event()
    app.record(value)  # actual public boundary before the internal adapter can be used
    novel = event(event_id='uncertain',value=dict(value['value'],sequence=2))
    body = app.call('observations.record', {'events':[novel]}, mode='uncertain-commit')
    problem(body,'observations_unavailable','unavailable')
    exact(body['error']['action'],'retry:same-events')
    result = app.record(novel)
    exact(result['entries'][0]['replayed'],True)
    exact(app.read()['totals'],literal('counters_200'))


@case('record-budgets-and-meaning', ['Q01','Q02'], ['observations.record'],
      'selected batch/event caps and exact input meaning from design4',
      ['limits refuse without writes', 'invalid counters/source/fields rejected', 'no postcommit read-cap refusal'])
def record_limits(app):
    app.seed()
    app.config['observations']['limits']['max_events']=1
    values = [event(),event(event_id='other',stream='other')]
    problem(app.require('observations.record',{'events':values}),'observation_limit')
    exact(app.facts(),{'events':[],'streams':[]})
    app.config['observations']['limits']['max_event_bytes']=16
    problem(app.require('observations.record',{'events':[event()]}),'observation_limit')
    app.config['observations']['limits']['max_event_bytes']=8192
    for value in (event(source='not-declared'), event(value=dict(event()['value'],counters=literal('unknown'))),
                  dict(event(),session_id='forged-ordinary-field')):
        problem(app.require('observations.record',{'events':[value]}),'invalid_request')
    app.config['observations']['limits']['max_read_response_bytes']=1
    exact(app.record(event())['count'],1)


@case('read-page-and-whole-scope', ['Q01','Q02'], ['observations.read'],
      'literal seeded100+20=120 at one snapshot, page cursor does not limit totals',
      ['event retained', 'scope total120', 'cursor', 'source filter', 'installation needs no Session'])
def pagination(app):
    app.seed()
    app.put(event(),literal('counters_100'))
    app.put(event(event_id='event-2',value={'sequence':2,'mode':'delta','counters':literal('counters_20')}),literal('counters_20'))
    app.config['session_store']=str(app.root/'no-session'/'absent.sqlite')
    first=app.read(limit=1)
    read_result(first)
    exact(first['totals'],literal('counters_120'))
    exact(first['observed_events'],2)
    exact(first['usage_events'],2)
    exact(first['has_more'],True)
    exact(first['next_after'],first['events'][0]['seq'])
    second=app.read(limit=1,after=first['next_after'])
    exact(second['totals'],literal('counters_120'))
    exact(second['has_more'],False)
    exact(second['events'][0]['event']['event_id'],'event-2')
    exact(second['as_of_seq'],first['as_of_seq'])
    exact(app.read(source='poise')['totals'],literal('unknown'))
    assert not (app.root/'no-session').exists()


@case('busy-fresh-read-unknown', ['Q01','Q05'], ['observations.read'],
      'actual exclusive lock; fresh CLI has no last-good cache',
      ['timeout0 returns unavailable', 'unknown fields not zero', 'no fabricated cross-process cache'])
def busy_read(app):
    app.seed()
    with closing(sqlite3.connect(app.database,timeout=0,isolation_level=None)) as db:
        db.execute('PRAGMA journal_mode=DELETE')
        db.execute('BEGIN EXCLUSIVE')
        body=app.require('observations.read',app.read_input())
        problem(body,'observations_unavailable','unavailable')
        result=body['result']; read_result(result)
        exact(result['availability'],'unavailable')
        for field in ('as_of_seq','events','observed_events','usage_events','totals','next_after','has_more'):
            exact(result[field],None)
        db.rollback()


@case('same-instance-cached-read', ['Q05'], ['observations.read'],
      'same service read/key then real exclusive-lock failure; original as_of_seq retained',
      ['real last-good response', 'cached/partial on same instance', 'unavailable envelope retained'])
def cache(app):
    app.seed()
    app.put(event(),literal('counters_100'))
    initial=app.read()
    body=app.call('observations.read',app.read_input(),mode='read-cache')
    problem(body,'observations_unavailable','unavailable')
    exact(body['result'],dict(initial,availability='cached',coverage='partial'))


@case('read-response-budget', ['Q01','Q05'], ['observations.read'],
      'read-only budget fails explicitly, never truncates or rewrites observations',
      ['observation_limit', 'stored rows retained'])
def read_limit(app):
    app.seed();app.put(event(),literal('counters_100'))
    before=app.facts()
    app.config['observations']['limits']['max_read_response_bytes']=1
    problem(app.require('observations.read',app.read_input()),'observation_limit')
    exact(app.facts(),before)


@case('comparable-and-unknown-clocks', ['Q03'], ['observations.record','observations.read'],
      'literal2s=2000000000ns; incomparable/missing/decreasing have no duration',
      ['exact integer', 'domain comparison', 'unknown duration', 'raw source timestamps retained'])
def timing(app):
    app.seed();start=literal('clock')
    endings=[dict(start,monotonic_ns=3000000000),None,
             dict(start,comparison_domain='other',monotonic_ns=3000000000),
             dict(start,monotonic_ns=999999999),dict(start,monotonic_ns=1000000000+10**30)]
    expected=[2000000000,None,None,None,10**30]
    values=[event(kind='timing',event_id='clock-'+str(i),value={'started':start,'finished':end})
            for i,end in enumerate(endings)]
    result=app.record(*values)
    exact([r['duration_ns'] for r in result['entries']],expected)
    views=app.read()['events']
    exact([r['event'] for r in views],values)
    exact([r['duration_ns'] for r in views],expected)


@case('reported-interval-touch-overlap', ['Q03'], ['observations.record'],
      'source offsets retained; two adjacent2s intervals allowed, overlap ambiguous',
      ['2000000000ns', 'touching accepted', 'overlap conflict leaves prior rows'])
def interval(app):
    app.seed()
    first=event(kind='interval',value=literal('source_interval'))
    result=app.record(first)
    exact(result['entries'][0]['duration_ns'],2000000000)
    next_value={'started_at':'2026-11-16T10:00:02+05:00','ended_at':'2026-11-16T10:00:04+05:00'}
    app.record(event(kind='interval',event_id='touch',value=next_value))
    before=app.facts()
    problem(app.require('observations.record',{'events':[event(kind='interval',event_id='overlap',
        value={'started_at':'2026-11-16T10:00:01+05:00','ended_at':'2026-11-16T10:00:03+05:00'})]}),
        'observation_conflict')
    exact(app.facts(),before)


def measurement_expected(vector, tokens=False):
    added,removed=vector['lines']; added_bytes,removed_bytes=vector['bytes']
    result=literal('measurement_result')
    result.update(added_lines=added,removed_lines=removed,added_bytes=added_bytes,
                  removed_bytes=removed_bytes,changed_lines=added+removed,
                  changed_bytes=added_bytes+removed_bytes)
    if tokens:
        result.update(added_tokens=7,removed_tokens=3,changed_tokens=10,coverage='complete',
                      tokenizer={'identity':'literal-counter','available':True})
    return result


@case('measurement-literal-texts', ['Q04'], ['content.measure'],
      'literal empty/equal/UTF8/newline/replacement vectors independently byte-checked',
      ['exact lines/bytes', 'unknown tokens', 'stateless no Session/store', 'ok/partial unavailable instrument'])
def texts(app):
    app.config['session_store']=str(app.root/'no-session'/'absent.sqlite')
    for vector in literal('texts'):
        result=app.ok('content.measure',{'before':vector['before'],'after':vector['after']})
        exact(result,measurement_expected(vector))
    assert not app.database.exists()
    assert not (app.root/'no-session').exists()


@case('measurement-instrument-input', ['Q04'], ['content.measure'],
      'external instrument observes exact ordered added/removed streams; literal counts7+3=10',
      ['exact instrument input', 'complete measurement', 'instrument identity'])
def instrument(app):
    audit=app.instrument(); vector=literal('texts')[3]
    exact(app.ok('content.measure',{'before':vector['before'],'after':vector['after']}),
          measurement_expected(vector,True))
    exact(json.loads(audit.read_bytes()),literal('instrument_input'))


@case('measurement-instrument-failures', ['Q04'], ['content.measure'],
      'instrument exit7/negative count invalid; text metrics stay independently known',
      ['unavailable envelope', 'usable text result', 'null token values'])
def instrument_failure(app):
    vector=literal('texts')[3]
    for mode in ('fail','invalid'):
        audit=app.instrument(mode)
        body=app.require('content.measure',{'before':vector['before'],'after':vector['after']})
        problem(body,'measurement_unavailable','unavailable')
        expected=measurement_expected(vector)
        expected['tokenizer']['identity']='literal-counter'
        exact(body['result'],expected)
        exact(json.loads(audit.read_bytes()),literal('instrument_input'))


@case('measurement-limits', ['Q04'], ['content.measure'],
      'sum before/after UTF8 and serialized result bounds are selected configuration',
      ['content cap', 'result cap', 'no instrument on rejected content'])
def measure_limits(app):
    app.config['measurement']['max_content_bytes']=1
    body=app.require('content.measure',{'before':'ёж','after':''})
    exact(body['status'],'rejected')
    assert body['error']['code']
    app.config['measurement']['max_content_bytes']=4096
    app.config['measurement']['max_response_bytes']=1
    body=app.require('content.measure',{'before':'','after':''})
    exact(body['status'],'rejected');assert body['error']['code']


@case('best-effort-status-replay', ['Q06'], ['telemetry.status','telemetry.replay'],
      'best effort has no durable queue/history', ['null spool', 'explicit no_persistent_queue', 'no created spool'])
def best_effort(app):
    result=app.ok('telemetry.status')
    exact(result['schema'],'poise-telemetry-status/v1')
    exact(result['mode'],'best_effort');exact(result['spool'],None)
    assert result['coverage'] in ('partial','unavailable')
    body=app.require('telemetry.replay')
    problem(body,'telemetry_unavailable','unavailable')
    exact(body['error']['details']['reason'],'no_persistent_queue')
    assert not app.spool.exists()


@case('durable-status-read-only', ['Q06'], ['telemetry.status'],
      'literal saved queue metadata; status has no create/write/delivery side effect',
      ['bounded metadata', 'pending count', 'raw payload absent', 'saved record unchanged'])
def queue_status(app):
    app.durable();path,value=app.spool_entry();before=path.read_bytes()
    result=app.ok('telemetry.status')
    exact(result['schema'],'poise-telemetry-status/v1')
    exact(result['mode'],'durable')
    exact(result['spool']['pending'],1)
    exact(result['spool']['failed_attempts'],0)
    exact(result['spool']['bytes'],len(before))
    exact(set(result['spool']['records'][0]),{'event_id','attempts','next_attempt_at','last_error'})
    exact(result['spool']['records'][0]['event_id'],value['envelope']['id'])
    exact(path.read_bytes(),before)
    assert not app.database.exists()


@case('saved-replay-one-contribution', ['Q02','Q06'], ['telemetry.replay','observations.read'],
      'literal saved identity/source; receiver commit precedes deletion; repeated replay idempotent',
      ['one receiver event', 'saved entry retired', 'no Session dependency', 'second replay delivers zero'])
def replay(app):
    app.seed();app.durable();path,value=app.spool_entry()
    app.config['session_store']=str(app.root/'no-session'/'absent.sqlite')
    result=app.ok('telemetry.replay')
    exact(result['schema'],'poise-telemetry-replay/v1')
    exact(result['attempted'],1);exact(result['delivered'],1);exact(result['pending'],0)
    assert not path.exists()
    exact(app.ok('telemetry.replay')['delivered'],0)
    rows=app.facts()['events'];exact(len(rows),1)
    exact(json.loads(rows[0][7]),value['envelope']['event'])
    exact(rows[0][5],None)
    assert not (app.root/'no-session').exists()


@case('replay-failure-attempt-limit-corrupt', ['Q06'], ['telemetry.replay','telemetry.status'],
      'bounded attempt is persisted before failing receiver; exhausted/corrupt stay visible',
      ['failed record remains', 'attempt increments once', 'exhausted not retried', 'invalid entry reported'])
def replay_failure(app):
    app.durable();path,_=app.spool_entry()
    body=app.require('telemetry.replay')
    assert body['status'] in ('ok','unavailable'),body
    exact(body['result']['attempted'],1)
    exact(body['result']['failed'],1)
    exact(json.loads(path.read_bytes())['attempts'],1)
    path.unlink();app.spool_entry(attempts=2)
    body=app.require('telemetry.replay');exact(body['result']['attempted'],0)
    exact(app.ok('telemetry.status')['spool']['exhausted'],1)
    path.unlink();app.spool_entry(bad=True)
    body=app.require('telemetry.replay');exact(body['result']['corrupt'],1)
    exact(path.read_bytes(),b'{')


@case('replay-commit-before-delete-restart', ['Q02','Q06'], ['telemetry.replay'],
      'stop actual receiver adapter after commit but before deletion, then replay same saved event',
      ['attempt persisted before sink', 'committed event visible', 'saved record retained until commit return',
       'ordinary restart replay does not duplicate'])
def replay_restart(app):
    app.seed();app.durable();path,value=app.spool_entry()
    # Qualify real entry without consuming the selected saved event.
    empty=path.read_bytes();path.unlink();app.ok('telemetry.replay');path.write_bytes(empty)
    launched=app.launch('telemetry.replay',{},mode='receiver-commit-gate')
    child,record,prefix=launched
    try:
        wait_file(prefix.with_suffix('.entered'))
        exact(json.loads(path.read_bytes())['attempts'],1)
        exact(len(app.facts()['events']),1)
        child.kill()
        out,err=child.communicate(timeout=3)
        record.update(stdout_hex=out.hex(),stderr_hex=err.hex(),exit_code=child.returncode)
        assert child.returncode<0
    finally:
        prefix.with_suffix('.release').touch()
    saved=json.loads(path.read_bytes());saved['next_attempt_at']=0
    path.write_bytes(encoded(saved))
    exact(app.ok('telemetry.replay')['delivered'],1)
    exact(len(app.facts()['events']),1)
    assert not path.exists()


@case('invoke-preserves-primary', ['Q05','Q07'], ['telemetry.invoke'],
      'exact independently observed five-field primary and original CLI exit',
      ['same complete response', 'raw bytes retained', 'admission not delivery acknowledgement'])
def invoke(app):
    app.seed()
    direct=app.call('output.minimal',app.output_input())
    cap=len(bytes.fromhex(CALLS[-1]['stdout_hex']))
    app.config['output']['limits']['max_response_bytes']=cap
    exact(app.call('output.minimal',app.output_input()),direct)
    body=app.invoke()
    assert len(bytes.fromhex(CALLS[-1]['stdout_hex']))>cap
    exact(body['status'],direct['status']);exact(body['error'],direct['error'])
    result=body['result'];exact(result['schema'],'poise-observed-invoke/v1')
    exact(result['primary'],direct)
    exact(set(result['telemetry']),{'state','event_id','coverage','stats','error'})
    exact(set(result['telemetry']['stats']),{'submitted','processed','duplicates','failed','dropped','pending'})
    exact(app.raw.read_bytes(),bytes.fromhex('4142ff0a0043'))
    app.unchanged_session()


@case('invoke-secondary-invalid-settings', ['Q05','Q07'], ['telemetry.invoke'],
      'selected primary intent survives absent/invalid optional families',
      ['primary still delivered', 'secondary unavailable only', 'direct operation stays independent'])
def optional_settings(app):
    direct=app.call('output.minimal',app.output_input())
    for key in ('observations','telemetry'):
        saved=app.config.pop(key)
        body=app.invoke()
        exact(body['status'],'ok');exact(body['result']['primary'],direct)
        exact(body['result']['telemetry']['state'],'unavailable')
        app.config[key]=saved
    app.config['telemetry']['max_pending']=0
    body=app.invoke();exact(body['result']['primary'],direct)
    exact(body['result']['telemetry']['state'],'unavailable')


@case('invoke-primary-error-and-recursion', ['Q05'], ['telemetry.invoke'],
      'outer error/exit equals selected primary; administrative/recursive operations rejected before effects',
      ['same rejected primary', 'no recursive telemetry', 'no prepare side effect'])
def primary_error(app):
    direct=app.call('output.minimal',{'sources':[]})
    exact(direct['status'],'rejected')
    body=app.invoke('output.minimal',{'sources':[]})
    exact(body['status'],direct['status']);exact(body['error'],direct['error'])
    exact(body['result']['primary'],direct)
    for name in ('telemetry.invoke','telemetry.status','telemetry.replay','observations.prepare'):
        problem(app.require('telemetry.invoke',{'operation':name,'input':{}}),'invalid_request')
    assert not app.database.exists()


def gate_case(app, mode):
    app.seed()
    if mode=='spool-gate':app.durable()
    direct=app.call('output.minimal',app.output_input())
    app.qualify_invoke()  # missing entry precedes future-module imports
    launched=app.launch('telemetry.invoke',{'operation':'output.minimal','input':app.output_input()},mode=mode)
    _,_,prefix=launched
    try:
        wait_file(prefix.with_suffix('.entered'))
        assert not prefix.with_suffix('.release').exists()
        result=app.collect(launched)
        exact(result['result']['primary'],direct)
        assert not prefix.with_suffix('.release').exists()
        EFFECTS.append({'mode':mode,'primary_returned_while_dependency_gate_closed':True})
    finally:
        prefix.with_suffix('.release').touch()
        wait_file(prefix.with_suffix('.done'))


for name in ('clock-gate','store-gate','spool-gate'):
    def gate_check(app, mode=name):
        gate_case(app,mode)
    case('primary-independent-'+name,['Q05'],['telemetry.invoke'],
         'causal closed external dependency gate; no numeric latency target',
         ['real dependency entered','primary returned while closed','release and clean fixture worker'])(gate_check)


@case('primary-optional-construction-no-io', ['Q05','Q07'], ['telemetry.invoke'],
      'actual parent optional path/SQLite/boot-file audit; unmodified output is separate proof',
      ['no optional parent I/O', 'independent output without Session read', 'primary intact'])
def no_io(app):
    app.seed();app.durable();app.qualify_invoke()
    direct=app.call('output.minimal',app.output_input())
    app.config['session_store']=str(app.root/'no-session'/'absent.sqlite')
    body=app.call('telemetry.invoke',{'operation':'output.minimal','input':app.output_input()},mode='no-optional-io')
    exact(body['result']['primary'],direct)
    exact([a for a in CALLS[-1]['audit'] if a['kind']=='forbidden_optional_io'],[])
    assert not (app.root/'no-session').exists()


for name in ('pipe-block','pipe-short'):
    def pipe_check(app, mode=name):
        app.seed();app.qualify_invoke()
        direct=app.call('output.minimal',app.output_input())
        body=app.call('telemetry.invoke',{'operation':'output.minimal','input':app.output_input()},mode=mode)
        exact(body['result']['primary'],direct)
        writes=[a for a in CALLS[-1]['audit'] if a['kind']=='final_write']
        exact(len(writes),1)
        exact(writes[0]['blocking'],False)
        exact(body['result']['telemetry']['state'],'dropped')
    case('nonblocking-final-'+name,['Q05','Q06'],['telemetry.invoke'],
         'one nonblocking final metadata write; incomplete/would-block drops observation',
         ['actual final write observed','nonblocking flag','single attempt','primary unchanged','dropped capture'])(pipe_check)


for durable in (False,True):
    def bounds(app, durable=durable):
        app.seed()
        if durable:app.durable()
        app.qualify_invoke()
        launched=app.launch('telemetry.invoke',{'operation':'output.minimal','input':app.output_input()},mode='bounds')
        _,_,prefix=launched
        try:
            body=app.collect(launched)
            exact(body['status'],'ok')
            observed=[a for a in CALLS[-1]['audit'] if a['kind']=='bounds']
            exact(len(observed),1)
            state=observed[0]
            assert 0<state['admitted']<=2,state
            assert 0<=state['summary']['pending']<=2,state
            assert state['summary']['dropped']>=10,state
            for key,value in state['collections'].items():
                if 'recent' in key or 'seen' in key: assert value<=4,state
                elif 'pending' in key or 'receipt' in key: assert value<=2,state
        finally:
            prefix.with_suffix('.release').touch()
    case('bounded-capture-'+('durable' if durable else 'best-effort'),['Q06'],['telemetry.invoke'],
         'max_pending2, twelve blocked captures, recent IDs<=2*max_pending',
         ['saturation observed','pending/inflight/capture limits','bounded identity/receipt containers','drops rather than waits'])(bounds)


@case('replay-retains-original-session', ['Q01','Q02'], ['observations.record'],
      'same persisted business event keeps original provenance after actual caller binding changes',
      ['new actual Session differs', 'repeat returns original seq/session/contribution'])
def replay_session(app):
    app.seed();value=event();first=app.record(value)['entries'][0]
    app.session.generated('second-caller.json')
    _,_,new=app.session.call('session.ensure')
    assert new['result']['session']['id']!=app.identity
    repeated=app.record(value)['entries'][0]
    exact(repeated,dict(first,replayed=True))


@case('known-zero-vs-mixed-unknown-totals', ['Q01','Q02'], ['observations.record','observations.read'],
      'literal known0 is a measured value; one unknown contributing detail makes that total unknown',
      ['known zero preserved', 'unknown detail not coerced', 'source/session scope separated'])
def zero_unknown(app):
    app.seed()
    zero=event(value={'sequence':1,'mode':'delta','counters':literal('zero')})
    app.record(zero)
    exact(app.read()['totals'],literal('zero'))
    unknown=event(event_id='unknown-detail',value={'sequence':2,'mode':'delta','counters':literal('counters_20')})
    app.record(unknown)
    exact(app.read()['totals'],literal('counters_20'))
    session=app.read(scope={'kind':'session'})
    exact(session['totals'],literal('counters_20'))
    exact(session['scope'],{'kind':'session'})


@case('observations-selected-configuration', ['Q01'], ['observations.record'],
      'only selected settings interpreted; malformed selected schema cannot express operation',
      ['invalid selected config rejected', 'unknown options tolerated without rewrite'])
def selected_configuration(app):
    app.seed();original=app.config['observations']['schema']
    app.config['observations']['schema']='unsupported-fixture'
    problem(app.require('observations.record',{'events':[event()]}),'invalid_config')
    app.config['observations']['schema']=original
    app.config['future-unselected']={'unknown':True}
    exact(app.record(event())['count'],1)


@case('invoke-child-exit7-stays-primary-data', ['Q05','Q07'], ['telemetry.invoke'],
      'existing command contract records child exit7 while its CLI delivery remains successful',
      ['qualified real child exit7', 'wrapper CLI0', 'same retained command receipt/full raw streams'])
def child_exit(app):
    from command_support import load, retained_snapshot
    app.config['commands']=load()['commands']
    (app.root/'command-state').mkdir();(app.root/'command-output').mkdir()
    request=load()['request']
    request.update(command_id='child-exit-seven',argv=[sys.executable,'-I','-S','-B',
        str(FIX/'exit7.py')],cwd=str(app.root))
    direct=app.call('command.request',request)
    if direct['result']['state']!='TERMINAL':
        direct=app.call('command.completion',{'command_id':'child-exit-seven'})
    exact(direct['status'],'ok')
    exact(direct['result']['termination'],{'kind':'exit','exit_code':7})
    snapshot=retained_snapshot(direct)
    exact(snapshot['streams'],{'stdout':b'OUT\n','stderr':b'ERR\n'})
    # completion is read-only; replay bit and operation belong to the original owner.
    expected=app.call('command.completion',{'command_id':'child-exit-seven'})
    body=app.invoke('command.completion',{'command_id':'child-exit-seven'})
    exact(body['status'],'ok');exact(body['result']['primary'],expected)
    exact(retained_snapshot(body['result']['primary'])['streams'],snapshot['streams'])


@case('bounded-recent-identity-history', ['Q06'], ['telemetry.invoke'],
      'twelve actually completed captures retain at most2*max_pending recent duplicate identities',
      ['actual detached completion', 'bounded recent-ID containers after turnover'])
def recent(app):
    app.seed();app.qualify_invoke()
    app.call('telemetry.invoke',{'operation':'output.minimal','input':app.output_input()},mode='recent-history')
    observations=[a for a in CALLS[-1]['audit'] if a['kind']=='recent_history']
    exact(len(observations),1);state=observations[0]
    exact(state['shared_dispatcher_instances'],1)
    history_bound(state['generated_ids'],state['retained_ids'],2)
    exact(len(state['generated_ids']),12)
    exact(len(set(state['generated_ids'])),12)
    exact(state['summary']['pending'],0)
    assert state['summary']['processed']>=12,state



@case('durable-capacity-and-replay-batch', ['Q05','Q06'], ['telemetry.invoke','telemetry.status','telemetry.replay'],
      'two saved-entry/20480byte capacity and one-attempt replay batch under actual receiver lock',
      ['primary independent of receiver lock', 'bounded actual queue', 'batch_items1', 'remaining record preserved'])
def durable_bounds(app):
    app.seed();app.durable();app.qualify_invoke()
    with closing(sqlite3.connect(app.database,timeout=0,isolation_level=None)) as receiver:
        receiver.execute('BEGIN IMMEDIATE')
        for index in range(4):
            launched=app.launch('telemetry.invoke',{'operation':'output.minimal','input':app.output_input()},mode='spool-audit')
            body=app.collect(launched);exact(body['status'],'ok')
            wait_file(launched[2].with_suffix('.done'))
        files=list(app.spool.glob('*.json'))
        exact(len(files),2)
        assert sum(path.stat().st_size for path in files)<=20480
        receiver.rollback()
    # Arrange a fresh eligible replay phase with unchanged captured source events.
    before=[]
    for path in files:
        value=json.loads(path.read_bytes());before.append(value['envelope'])
        value['next_attempt_at']=0;value['attempts']=0;path.write_bytes(encoded(value))
    result=app.ok('telemetry.replay')
    exact(result['attempted'],1);exact(result['delivered'],1);exact(result['pending'],1)
    exact(len(list(app.spool.glob('*.json'))),1)
    exact(app.ok('telemetry.replay')['delivered'],1)
    rows=app.facts()['events']
    actual=[json.loads(row[7]) for row in rows]
    for envelope_value in before:assert envelope_value['event'] in actual


@case('unprepared-selected-store', ['Q01'], ['observations.record','observations.read'],
      'explicit selected missing store is unavailable with a remedy, never silently prepared',
      ['ordinary missing-store refusal', 'no automatic store creation'])
def unprepared(app):
    body=app.require('observations.record',{'events':[event()]})
    problem(body,'observations_unavailable','unavailable')
    assert body['error']['action']
    assert not app.database.exists()
    body=app.require('observations.read',app.read_input())
    problem(body,'observations_unavailable','unavailable')
    exact(body['result']['availability'],'unavailable')
    assert not app.database.exists()


@case('large-usage-sequence-and-counter-reset', ['Q02'], ['observations.record','observations.read'],
      'large ordered sequence is decimal state, cumulative reset remains an explicit conflict',
      ['large exact sequence retained', '40 contribution', 'decreasing counter rejected atomically'])
def large_sequence(app):
    app.seed();sequence=10**30
    baseline=event(value={'sequence':sequence,'mode':'baseline','counters':literal('counters_1000')})
    later=event(event_id='later',value={'sequence':sequence+1,'mode':'cumulative','counters':literal('counters_1040')})
    app.record(baseline,later)
    exact(app.read()['totals'],literal('counters_40'))
    exact(sql(app.database,'SELECT last_sequence FROM observation_streams WHERE source=? AND stream=?',
              ('vendor','stream-1')),[[str(sequence+1)]])
    before=app.facts()
    reset=event(event_id='reset',value={'sequence':sequence+2,'mode':'cumulative','counters':literal('counters_1000')})
    problem(app.require('observations.record',{'events':[reset]}),'observation_conflict')
    exact(app.facts(),before)


@case('cached-read-key-isolation', ['Q05'], ['observations.read'],
      'a different source key cannot reuse the same-instance last-good response',
      ['first successful read exists', 'different query has unknown values on busy store'])
def cache_key(app):
    app.seed();app.put(event(),literal('counters_100'));app.read()
    body=app.call('observations.read',app.read_input(),mode='read-other-key')
    problem(body,'observations_unavailable','unavailable')
    exact(body['result']['availability'],'unavailable')
    exact(body['result']['source'],'poise')
    for field in ('events','totals','as_of_seq','observed_events','usage_events','next_after','has_more'):
        exact(body['result'][field],None)


for durable in (False,True):
    def event_bytes(app, durable=durable):
        app.seed()
        if durable:app.durable()
        app.qualify_invoke()
        primary=app.call('output.minimal',app.output_input())
        app.config['observations']['limits']['max_event_bytes']=65536
        if durable:app.config['telemetry']['spool']['max_bytes']=131072
        # Normal parent metadata budget; only the actual worker clock facts are huge.
        cap=app.config['telemetry']['max_event_bytes']
        huge=dict(literal('automatic_clocks')[0],comparison_domain=literal('oversized_clock_domain'))
        assert len(encoded({'started':huge,'finished':huge}))>cap
        launched=app.launch('telemetry.invoke',{'operation':'output.minimal','input':app.output_input()},
                            mode='completed-event-overflow')
        _,_,prefix=launched
        try:
            wait_file(prefix.with_suffix('.entered'))
            body=app.collect(launched)
            exact(body['result']['primary'],primary)
            assert not prefix.with_suffix('.release').exists()
            admitted_or_dropped(body['result']['telemetry'])
        finally:
            prefix.with_suffix('.release').touch()
        audit=app.await_worker(launched)
        samples=[row for row in audit if row['kind']=='supplied_clock']
        exact(len(samples),2)
        exits=[row for row in audit if row['kind']=='clock_worker_exit']
        exact(len(exits),1);exact(exits[0]['clock_calls'],2)
        # Only now has the full actual-clock event been processed/dropped.
        exact(app.facts()['events'],[])
        assert not list(app.spool.glob('*.json'))
    case('capture-byte-cap-'+('durable' if durable else 'best-effort'),['Q05','Q06'],['telemetry.invoke'],
         'actual worker clock facts exceed full-event cap; admitted pending primary is valid',
         ['primary delivered while worker held','two actual clock samples','worker exit observed',
          'no oversized persisted event after completion'])(event_bytes)


@case('durable-byte-cap-includes-metadata', ['Q06'], ['telemetry.invoke','telemetry.status'],
      'existing near-capacity valid record plus new record/2048-byte allowance exceeds10240',
      ['real queue enqueue attempted','metadata charged','entry count retained','source event preserved'])
def queue_bytes(app):
    app.seed();app.durable()
    app.config['telemetry']['spool'].update(max_entries=16,batch_items=1,max_bytes=10240)
    app.qualify_invoke()
    # A separate phase arranges one literal near-capacity saved record.
    for path in app.spool.glob('*.json'):path.unlink()
    path,saved=app.spool_entry()
    old=saved['envelope']['event']
    old.update(kind='source',value={'parameters':{'padding':literal('capacity_padding')}})
    path.write_bytes(encoded(saved))
    assert len(encoded(old))<=8192
    assert path.stat().st_size<10240
    with closing(sqlite3.connect(app.database,timeout=0,isolation_level=None)) as receiver:
        receiver.execute('BEGIN IMMEDIATE')
        launched=app.launch('telemetry.invoke',{'operation':'output.minimal','input':app.output_input()},mode='spool-audit')
        exact(app.collect(launched)['status'],'ok')
        wait_file(launched[2].with_suffix('.done'))
        exact(list(app.spool.glob('*.json')),[path])
        assert path.stat().st_size<=10240
        exact(json.loads(path.read_bytes())['envelope']['event'],old)
        receiver.rollback()


@case('transaction-projection-fault-rollback', ['Q02'], ['observations.record','observations.read'],
      'trigger/UDF fails only after both novel event and stream rows exist inside the actual transaction',
      ['fault actually reached after both writes','original rows retained','no orphan replay identity',
       'same-event retry contributes once'])
def transaction_fault(app):
    app.seed();app.record(event())
    before=app.facts()
    with closing(sqlite3.connect(app.database)) as db:
        db.executescript((FIX/'projection-fault.sql').read_text())
    novel=event(stream='transaction-stream',event_id='faulted',
                value={'sequence':1,'mode':'delta','counters':literal('counters_20')})
    body=app.call('observations.record',{'events':[novel]},mode='projection-fault')
    problem(body,'observations_unavailable','unavailable')
    faults=[row for row in CALLS[-1]['audit'] if row['kind']=='transaction_fault']
    exact(len(faults),1)
    exact(faults[0]['in_transaction'],True)
    exact([row[0] for row in faults[0]['events']],['faulted'])
    exact(faults[0]['streams'],[['vendor','transaction-stream','1']])
    exact(app.facts(),before)
    sql(app.database,'DROP TRIGGER fixture_event_projection_fault',write=True)
    sql(app.database,'DROP TRIGGER fixture_stream_event_fault',write=True)
    retry=app.record(novel)
    exact(retry['entries'][0]['replayed'],False)
    exact(app.read()['totals'],literal('counters_120'))
    exact(app.record(novel)['entries'][0]['replayed'],True)
    exact(app.read()['totals'],literal('counters_120'))


for scenario in ('known-session','unknown-session','rejected-primary'):
    def automatic_capture(app,scenario=scenario):
        from uuid import UUID
        app.seed();app.qualify_invoke()
        operation='session.status' if scenario=='known-session' else 'output.minimal'
        value={} if scenario=='known-session' else (app.output_input() if scenario=='unknown-session' else {'sources':[]})
        expected_session=app.identity if scenario=='known-session' else None
        if scenario=='unknown-session':
            app.config['session_store']=str(app.root/'no-session'/'absent.sqlite')
        primary=app.call(operation,value)
        expected_status='rejected' if scenario=='rejected-primary' else 'ok'
        exact(primary['status'],expected_status)
        launched=app.launch('telemetry.invoke',{'operation':operation,'input':value},mode='deterministic-capture')
        body=app.collect(launched)
        exact(body['result']['primary'],primary)
        audit=app.await_worker(launched)
        exact([row['sample'] for row in audit if row['kind']=='supplied_clock'],literal('automatic_clocks'))
        rows=sql(app.database,'SELECT event_id,session_id,source_mode,event_json,contribution_json,duration_ns '
                 'FROM observation_events WHERE kind=? AND source=? ORDER BY seq',('operation','poise'))
        exact(len(rows),1)
        identity,session,source_mode,raw,contribution,duration=rows[0]
        UUID(identity)  # Actual generated identity is structurally a UUID, not a guessed value.
        exact(identity,body['result']['telemetry']['event_id'])
        observed=json.loads(raw)
        assert isinstance(observed['stream'],str) and observed['stream']
        expected=literal('automatic_event')
        expected.update(event_id=identity,stream=observed['stream'])
        expected['value'].update(operation=operation,status=expected_status,
                                 exit_code=2 if scenario=='rejected-primary' else 0)
        exact(observed,expected)
        exact(session,expected_session);exact(source_mode,'observed')
        exact(contribution,None);exact(duration,'2000000000')
        if scenario=='unknown-session':assert not (app.root/'no-session').exists()
    case('automatic-event-'+scenario,['Q01','Q03','Q05'],['telemetry.invoke'],
         'independent primary/actual Session facts and two literal clock-port readings, not producer spool equality',
         ['actual generated identity','correct operation/status/CLI exit','known or unknown Session provenance',
          'captured clock facts and exact2second duration','actual worker completion'])(automatic_capture)


@case('regression-installation-read-independent-session-path',['Q01'],['observations.read'],
      'T071-IMPL-F01 independent read100 while the unselected Session resolution port refuses access',
      ['qualified own read100','no foreign Session-path dependency','same actual source facts'])
def installation_read_independent(app):
    app.seed();app.record(event())
    expected=app.read()
    exact(expected['totals'],literal('counters_100'))
    actual=app.call('observations.read',app.read_input(),mode='installation-no-session-io')
    exact(actual['status'],'ok');exact(actual['result'],expected)


@case('regression-explicit-prepare-mode-recovery',['Q01'],['observations.prepare'],
      'T071-IMPL-F02 a successful explicit prepare/retry has actual configured0600 mode',
      ['initial success or truthful unavailable','same public retry completes mode','existing data retained'])
def preparation_mode_recovery(app):
    import stat
    initial=app.call('observations.prepare',{},mode='prepare-mode-failure')
    assert initial['status'] in ('ok','unavailable'),initial
    if initial['status']=='ok':exact(stat.S_IMODE(app.database.stat().st_mode),0o600)
    app.ok('observations.prepare')
    exact(stat.S_IMODE(app.database.stat().st_mode),0o600)
    app.record(event());before=app.facts()
    app.ok('observations.prepare')
    exact(app.facts(),before);exact(app.read()['totals'],literal('counters_100'))


@case('regression-exact-nanosecond-interval',['Q03'],['observations.record','observations.read'],
      'T071-IMPL-F03 independent literal interval1ns, original source strings/offsets retained',
      ['duration1 rather than0','same exact duration on read/replay','raw values preserved'])
def nanosecond_interval(app):
    app.seed();value=literal('regression_interval_1ns')
    first=app.record(value)
    exact(first['entries'][0]['duration_ns'],1)
    observed=app.read()['events'][0]
    exact(observed['duration_ns'],1);exact(observed['event'],value)
    repeated=app.record(value)['entries'][0]
    exact(repeated['replayed'],True);exact(repeated['duration_ns'],1)


@case('regression-nanosecond-source-order',['Q02','Q03'],['observations.record','observations.read'],
      'T071-IMPL-F03 actual2ns->1ns decrease conflicts despite increasing sequence; total remains100',
      ['time precision preserved in ordering','atomic refusal','no extra contribution'])
def nanosecond_source_order(app):
    app.seed();first,second=literal('regression_source_order')
    app.record(first);before=app.facts()
    body=app.require('observations.record',{'events':[second]})
    problem(body,'observation_conflict')
    exact(app.facts(),before);exact(app.read()['totals'],literal('counters_100'))


@case('regression-unsupported-subnanosecond-value',['Q03'],['observations.record'],
      'T071-IMPL-F03 a nonintegral0.1ns value cannot become a fabricated integer-zero measurement',
      ['unsupported precision refused explicitly','no stored false zero'])
def subnanosecond_value(app):
    app.seed()
    value=event(kind='interval',value=literal('regression_subnanosecond_interval'))
    body=app.require('observations.record',{'events':[value]})
    problem(body,'invalid_request')
    exact(app.facts(),{'events':[],'streams':[]})


@case('regression-missing-read-public-prepare-remedy',['Q01','Q05'],['observations.read','observations.prepare'],
      'T071-IMPL-F04 actual missing-store read names explicit public preparation, never automatic creation',
      ['prepare:observations action','read leaves DB absent','explicit prepare then read succeeds'])
def missing_read_remedy(app):
    actual=app.require('observations.read',app.read_input())
    problem(actual,'observations_unavailable','unavailable')
    exact(actual['error']['action'],'prepare:observations')
    assert not app.database.exists()
    app.ok('observations.prepare')
    exact(app.read()['availability'],'observed')


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--suite',required=True,choices=['behavior'])
    parser.add_argument('--output-root',required=True)
    args=parser.parse_args()
    output=Path(args.output_root).absolute();output.mkdir(parents=True,exist_ok=False)
    tmp=output/'isolated';tmp.mkdir();tempfile.tempdir=str(tmp)
    report={'suite':args.suite,'argv':sys.argv,'cwd':str(Path.cwd()),'python':sys.executable,
            'version':sys.version,'source_commit':subprocess.check_output(['git','rev-parse','HEAD'],cwd=APP,text=True).strip(),
            'subject':'actual standalone apps/poise CLI; declared probes for optional external adapters only',
            'cases':[],'qualification':None,'errors':[],'calls':CALLS,'controls':CONTROLS,
            'state_setup_calls':STATE_CALLS,'session_setup_calls':SESSION_CALLS,'sql':SQL_LOG,'effects':EFFECTS}
    try:
        report['qualification']=qualify(output)
    except Exception as error:
        report['errors'].append({'qualification':type(error).__name__+': '+str(error),'traceback':traceback.format_exc()})
    if not report['errors']:
        for entry in CASES:
            result={k:v for k,v in entry.items() if k!='function'}
            result['calls_start']=len(CALLS)
            try:
                with Observations() as app:
                    entry['function'](app)
                result['status']='passed'
                result['downstream_reachability']='executed'
            except MissingObservation as error:
                result.update(status='qualified_missing_operation',reason=str(error),
                              downstream_reachability='not_reached_after_absent_public_entry',
                              traceback=traceback.format_exc())
            except AssertionError as error:
                result.update(status='contract_mismatch',reason=str(error),traceback=traceback.format_exc())
            except Exception as error:
                result.update(status='diagnostic_error',reason=type(error).__name__+': '+str(error),traceback=traceback.format_exc())
            result['calls_end']=len(CALLS)
            report['cases'].append(result)
    counts={name:sum(c['status']==name for c in report['cases']) for name in
            ('passed','qualified_missing_operation','contract_mismatch','diagnostic_error')}
    if len(report['cases'])!=len(CASES):report['errors'].append({'unexecuted_cases':len(CASES)-len(report['cases'])})
    report['counts']=counts
    report['status']='error' if report['errors'] or counts['diagnostic_error'] else (
        'failed' if counts['qualified_missing_operation'] or counts['contract_mismatch'] else 'passed')
    (output/'observations-details.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'status':report['status'],'cases':len(CASES),'executed':len(report['cases']),
                      **counts,'errors':len(report['errors'])}))
    return 2 if report['status']=='error' else 1 if report['status']=='failed' else 0


if __name__=='__main__':
    raise SystemExit(main())
