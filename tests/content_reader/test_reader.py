"""36 independently specified calls; pre-code RED is only absent public CLI."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
import io
import json
import os
import shutil
import sqlite3
import subprocess
import sys

import pytest

from tests.content_reader.helpers import (
    Scenario, FIXTURES, SOURCE, canonical, files, fixture, other_project,
    sha, storage_snapshot, strict_json, text_frames, typed_equal,
)


@pytest.fixture
def s(project, tmp_path):
    return Scenario(project, tmp_path)


def test_complete_small_and_empty_files(s):
    s.ready()
    s.row(s.read(complete=True), text=fixture()['source_texts']['lf'],
          selection='complete', classification='first_read')
    s.write('docs/sample.txt', b'')
    s.row(s.read(complete=True), text='', selection='complete', classification='content_changed')
    assert s.context()['summary']['event_count'] == 2
    assert s.context()['summary']['cache_entries'] == 2


def test_large_range_and_actual_eof(s):
    s.ready()
    s.write('docs/sample.txt', ''.join(f'{n:04d} literal line {n:04d}\n' for n in range(1, 2001)).encode())
    oracle = fixture()['large_file_oracle']
    s.row(s.read(start=100, end=140), text=oracle['expected_content'],
          selection=oracle['expected_range'], classification='first_read')
    assert sha(oracle['expected_content'].encode()) == oracle['expected_hash']
    s.row(s.read(start=2000, end=2010), text='2000 literal line 2000\n',
          selection='lines:2000-2000', classification='new_range')
    before = s.metadata()
    s.error(s.invoke(s.request(start=2001, end=2002)), 'range_after_eof', key='sample')
    assert s.metadata() == before


def test_utf8_and_mode_specific_newlines(s):
    s.ready()
    data = fixture()['source_texts']
    for name, literal in data.items():
        assert sha(literal.encode()) == fixture()['selected_sha256_literals'][name]
    s.write('docs/sample.txt', data['crlf'].encode())
    s.row(s.read(complete=True), text=data['lf'], selection='complete', classification='first_read')
    s.row(s.read(start=1, end=2), text=data['range_crlf_1_2'], selection='lines:1-2', classification='new_range')
    assert len(data['lf'].encode()) == 16 and len(data['crlf'].encode()) == 18


def test_strict_inclusive_range_contract(s):
    s.active()
    for selection in fixture('selection-invalid.json'):
        request = s.request(); request['reads'][0]['selection'] = selection
        s.error(s.invoke(request), 'invalid_selection', key='sample')
    s.ready()
    s.row(s.read(start=1, end=1), text='alpha\n', selection='lines:1-1', classification='first_read')
    # Requested span is limited before clipping to actual EOF.
    s.data['bounded']['max_lines'] = 3; s.save_policy()
    s.error(s.invoke(s.request(start=2, end=8)), 'bounded_limit', key='sample')


def test_complete_and_bounded_limits(s):
    s.ready()
    for mode, bound, value, kwargs, error in [
        ('complete', 'max_bytes', 16, {'complete': True}, 'complete_limit'),
        ('complete', 'max_lines', 3, {'complete': True}, 'complete_limit'),
        ('bounded', 'max_bytes', 10, {}, 'bounded_limit'),
        ('bounded', 'max_lines', 7, {}, 'bounded_limit'),
    ]:
        original = deepcopy(s.data)
        s.data[mode][bound] = value; s.save_policy()
        assert s.invoke(s.request(**kwargs))[0] == 0
        s.data[mode][bound] = value - 1; s.save_policy()
        before = s.metadata()
        s.error(s.invoke(s.request(**kwargs)), error, key='sample')
        assert s.metadata() == before
        s.data = original; s.save_policy()
    s.write('docs/sample.txt', fixture()['source_texts']['crlf'].encode())
    s.data['complete']['max_bytes'] = 17; s.save_policy()
    s.error(s.invoke(s.request(complete=True)), 'complete_limit', key='sample')


def test_full_output_budgets_before_registration(s):
    s.ready()
    used = False
    for fmt in ['json', 'text']:
        for dimension in ['max_bytes', 'max_lines']:
            if used:
                s.event('SessionEnd')
                assert s.invoke({'operation': 'recover'})[0] == 0
                s.event(); s.ready()
            used = True
            # Only paths, native IDs and own policy hash are dynamic inputs.
            candidate = deepcopy(fixture()['first_response'])
            observed = s.context()
            for key in ['project', 'checkout', 'policy_sha256', 'context', 'response_path']:
                candidate[key] = observed[key]
            candidate['items'][0]['classification'] = 'new_context_generation'
            if fmt == 'json':
                encoded = canonical(candidate)
            else:
                encoded = canonical(dict({k: v for k, v in candidate.items() if k != 'items'},
                                        item={k: v for k, v in candidate['items'][0].items() if k != 'content'})) + 'βeta\nlast'
            n = len(encoded.encode()) if dimension == 'max_bytes' else len(encoded.splitlines())
            original = s.data['output'][dimension]
            if n > 1:
                s.data['output'][dimension] = n - 1; s.save_policy()
                before = s.metadata()
                code, out, err = s.raw(s.request(), fmt)
                assert err == ''
                s.error((code, strict_json(out)), 'output_limit')
                assert s.metadata() == before
            s.data['output'][dimension] = n; s.save_policy()
            assert s.raw(s.request(), fmt)[0] == 0
            # JSON has one physical line; zero is an invalid positive policy.
            if n == 1:
                s.data['output'][dimension] = 0; s.save_policy()
                s.error(s.invoke(s.request()), 'invalid_policy', field='policy:output.' + dimension)
            else:
                s.data['output'][dimension] = 1; s.save_policy()
                before = s.metadata()
                code, out, err = s.raw(s.request(), fmt)
                assert err == ''
                s.error((code, strict_json(out)), 'output_limit')
                assert s.metadata() == before
            s.data['output'][dimension] = original; s.save_policy()


def test_first_and_exact_repeat_contract(s):
    s.ready()
    first = s.read()
    s.row(first, text='βeta\nlast', selection='lines:2-3', classification='first_read')
    typed_equal(first['summary'], fixture('context-sequences.json')['first'])
    second = s.read()
    s.row(second, text='βeta\nlast', selection='lines:2-3', classification='exact_repeat', status='unchanged')
    typed_equal(second['summary'], fixture('context-sequences.json')['repeat'])
    assert first['context'] == second['context']


def test_new_range_and_changed_selected_content(s):
    s.ready(); s.read()
    s.row(s.read(start=1, end=1), text='alpha\n', selection='lines:1-1', classification='new_range')
    s.write('docs/sample.txt', 'ALPHA\nβeta\nlast'.encode())
    s.row(s.read(), text='βeta\nlast', selection='lines:2-3', classification='exact_repeat', status='unchanged')
    s.write('docs/sample.txt', 'ALPHA\nγeta\nlast'.encode())
    s.row(s.read(), text='γeta\nlast', selection='lines:2-3', classification='content_changed')


def test_new_generation_and_classification_precedence(s):
    s.ready(); s.read(); s.advance()
    s.row(s.read(), text='βeta\nlast', selection='lines:2-3', classification='new_context_generation', generation=1)
    s.row(s.read(start=1, end=1), text='alpha\n', selection='lines:1-1', classification='new_range', generation=1)
    s.write('docs/sample.txt', 'alpha\nγeta\nlast'.encode())
    s.row(s.read(), text='γeta\nlast', selection='lines:2-3', classification='content_changed', generation=1)


def test_previously_seen_version_is_available(s):
    s.ready(); s.read()
    s.write('docs/sample.txt', 'alpha\nγeta\nlast'.encode()); s.read()
    s.write('docs/sample.txt', fixture()['source_texts']['lf'].encode())
    s.row(s.read(), text='βeta\nlast', selection='lines:2-3', classification='exact_repeat', status='unchanged')
    cache, events = s.metadata()
    assert len(cache) == 2 and len(events) == 3
    s.advance(); s.read()
    assert len(s.metadata()[0]) == 3


def test_project_checkout_and_caller_isolation(s, project, tmp_path):
    s.ready(); a = s.read()
    foreign = other_project(project, tmp_path); foreign.ready(); b = foreign.read()
    assert a['context']['caller_key'] != b['context']['caller_key']
    before = s.metadata(); foreign.advance(); foreign.read(); assert s.metadata() == before
    # A second registered worktree shares the project and native episode, not cache namespace.
    second = tmp_path / 'second-checkout'
    subprocess.run(['git', '-C', str(s.checkout), 'worktree', 'add', '-b', 'reader-copy', str(second)], check=True, capture_output=True)
    original = s.checkout; s.checkout = second
    s.write('docs/sample.txt', fixture()['source_texts']['lf'].encode())
    c = s.read(); assert c['items'][0]['classification'] == 'first_read'
    assert c['context']['checkout_key'] != a['context']['checkout_key']
    s.checkout = original
    assert s.read()['items'][0]['status'] == 'unchanged'
    peer = Scenario(project, tmp_path, identity='second-native')
    peer.prove(); peer.native_identity('second-native'); peer.native_hooks(); peer.event(); peer.ready()
    assert peer.read()['items'][0]['classification'] == 'first_read'
    assert peer.context()['context']['caller_key'] != a['context']['caller_key']


def test_context_generation_event_replay_is_scoped(s, project, tmp_path):
    s.ready(); s.read()
    assert s.context()['context']['generation'] == 0
    first = s.advance('unique-epoch'); assert first['context']['generation'] == 1
    assert s.advance('unique-epoch')['context']['generation'] == 1
    s.error(s.invoke({'operation': 'advance_context', 'request_id': 'unique-epoch', 'reason': 'different'}), 'event_conflict')
    assert s.advance('epoch-2')['context']['generation'] == 2
    assert len(s.metadata()[0]) == 1  # Metadata is retained across generations.
    foreign = other_project(project, tmp_path); foreign.ready()
    assert foreign.advance('unique-epoch')['context']['generation'] == 1
    assert s.context()['context']['generation'] == 2


def test_concurrent_exact_reads(s):
    s.ready()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [f.result(timeout=10) for f in [pool.submit(s.read), pool.submit(s.read)]]
    assert sorted(v['items'][0]['status'] for v in results) == ['content', 'unchanged']
    typed_equal(s.context()['summary'], fixture('context-sequences.json')['repeat'])
    assert len(s.metadata()[0]) == 1 and len(s.metadata()[1]) == 2


def test_batch_preflight_and_per_item_registration_outcomes(s):
    s.ready(); first = s.request()['reads'][0]
    before = s.metadata()
    for bad in [deepcopy(first), dict(first, key='bad', selection={'kind':'lines','start':False,'end':2})]:
        value = s.invoke({'operation': 'read', 'reads': [first, bad]})
        assert value[0] == 2 and value[1]['status'] == 'rejected'
        assert s.metadata() == before
    code, value = s.invoke({'operation':'read','reads':[dict(first,key='z'),dict(first,key='missing',path='docs/absent.txt'),dict(first,key='a')]})
    assert code == 1 and value['status'] == 'partial'
    assert [v['key'] for v in value['items']] == ['z','missing','a']
    assert [v['status'] for v in value['items']] == ['content','failed','unchanged']
    assert value['items'][1]['error']['code'] == 'source_missing'
    assert len(s.metadata()[0]) == 1 and len(s.metadata()[1]) == 2


def test_strict_request_and_policy_shapes(s):
    s.prove()
    for request in [None, [], {}, {'operation':'unknown'}, {'operation':'context','extra':1},
                    {'operation':'reset'}, {'operation':'clear'}, {'operation':'recover','receipt':{}},
                    {'operation':'advance_context','request_id':True,'reason':'manual'}]:
        s.error(s.invoke(request), 'invalid_request')
    for raw in [b'{', b'{"operation":"context","operation":"prepare"}', b'NaN']:
        s.error(s.invoke({}, raw=raw), 'invalid_request')
    original = deepcopy(s.data)
    for path, value in [('registry.schema_version',1),('registry.busy_timeout_ms',True),
                        ('bounded.max_bytes',False),('coordination.wait_seconds',0),
                        ('admission.definition_path',None),('session.temporary_root','')]:
        s.data = deepcopy(original); section, field = path.split('.')
        s.data[section][field] = value; s.save_policy()
        s.error(s.invoke({'operation':'prepare'}), 'invalid_policy', field='policy:'+path)
    s.data = deepcopy(original); s.data['admission']['mode']='caller';s.save_policy()
    # Unknown options are preserved but cannot supply the missing required definition.
    del s.data['admission']['definition_path'];s.save_policy()
    s.error(s.invoke({'operation':'prepare'}), 'invalid_policy', field='policy:admission.definition_path')


def test_source_failure_diagnostics(s, monkeypatch):
    s.ready()
    for path, code in [('docs/missing.txt','source_missing'),('docs','source_not_file'),('../foreign','path_outside_project')]:
        s.error(s.invoke(s.request(path=path)), code, key='sample')
    s.write('docs/sample.txt', b'\xff\xfe');s.error(s.invoke(s.request()),'source_not_utf8',key='sample')
    s.write('docs/sample.txt',fixture()['source_texts']['lf'].encode())
    original = Path.open
    def denied(path,*args,**kwargs):
        if path == s.checkout/'docs/sample.txt':raise PermissionError('fixture denied')
        return original(path,*args,**kwargs)
    before = s.metadata()
    with monkeypatch.context() as patch:
        patch.setattr(Path,'open',denied);code,out,err=s.transport(patch,s.request())
    assert err=='';s.error((code,strict_json(out)),'source_unreadable',key='sample')
    assert s.metadata()==before


def test_prepared_storage_and_schema_preservation(s):
    s.active()
    assert not list(Path(s.data['session']['temporary_root']).glob('**/reads.sqlite'))
    # First read may legitimately prepare its own fresh resource.
    assert s.read()['items'][0]['classification']=='first_read'
    s.ready()
    with sqlite3.connect(s.db) as db:
        assert db.execute('SELECT singleton,schema_version FROM registry_schema').fetchall()==[(1,2)]
        db.execute('UPDATE registry_schema SET schema_version=? WHERE singleton=?',(71,1))
    before=s.db.read_bytes()
    for req in [s.request(),{'operation':'context'},{'operation':'prepare'}]:
        s.error(s.invoke(req),'registry_schema');assert s.db.read_bytes()==before
    s.db.write_bytes(b'corrupt existing own registry');before=s.db.read_bytes()
    s.error(s.invoke({'operation':'prepare'}),'registry_invalid');assert s.db.read_bytes()==before


def test_genuine_caller_admission_profile(s):
    s.prove();s.template()
    for op in ['prepare','context','adopt']:
        code,value=s.invoke({'operation':op});assert code==1
        assert value['error']['code'] in ['admission_uncertain','admission_unavailable']
    assert not list(Path(s.data['session']['temporary_root']).glob('**/reads.sqlite'))
    s.ready();assert s.read()['context']['caller_key']==s.caller_key
    s.error(s.invoke(s.request(),env=dict(s.env,CODEX_SESSION_ID='conflicting')),'invalid_caller')
    invalid={k:v for k,v in s.env.items() if k not in ('CODEX_THREAD_ID','CODEX_SESSION_ID')};invalid['POISE_SESSION']='unsupported'
    s.error(s.invoke(s.request(),env=invalid),'invalid_caller')


def test_native_hook_observation_profile(s):
    s.ready();start=s.episode.copy();s.read();s.event('UserPromptSubmit');s.event('Stop')
    assert s.context()['context']['native_episode']==start
    assert s.read()['items'][0]['status']=='unchanged'
    s.event('SessionEnd');s.event('Stop')
    before=files(s.resource)
    code,value=s.invoke(s.request());assert code==1
    assert value['error']['code'] in ['admission_uncertain','admission_dead']
    assert files(s.resource)==before
    assert set(start)=={'project_id','session_id','binding_id','start_event_id'}


def test_confirmed_end_cleanup_and_next_episode_admission(s):
    s.ready();s.read();old=s.resource;episode=s.episode.copy();s.event('SessionEnd')
    code,result=s.invoke({'operation':'recover'});assert code==0 and result['status']=='cleaned'
    assert not old.exists();assert result['details']['episode']==episode
    s.event();assert s.episode['start_event_id']>episode['start_event_id']
    s.ready();assert s.read()['items'][0]['classification']=='first_read'
    assert s.resource!=old


def test_registry_write_lock_failure(s):
    s.ready();before=s.metadata()
    with sqlite3.connect(s.db,timeout=0) as lock:
        lock.execute('BEGIN IMMEDIATE')
        s.error(s.invoke(s.request()),'registry_write',key='sample')
        lock.rollback()
    assert s.metadata()==before
    assert s.read()['items'][0]['classification']=='first_read'


def test_registry_read_and_connection_failure(s,monkeypatch):
    s.ready();original=sqlite3.connect;before=s.metadata()
    def fail(database,*args,**kwargs):
        if str(s.db) in str(database):raise sqlite3.OperationalError('fixture connection unavailable')
        return original(database,*args,**kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(sqlite3,'connect',fail);code,out,err=s.transport(patch,{'operation':'context'})
    assert err=='';s.error((code,strict_json(out)),'registry_read')
    assert s.metadata()==before


def test_writer_failure_preserves_registered_availability(s,monkeypatch):
    s.ready()
    class Broken(io.StringIO):
        def write(self,value):raise OSError('fixture sink failed')
    code,out,err=s.transport(monkeypatch,s.request(),sink=Broken())
    assert code==1 and out==''
    error=strict_json(err);assert error['error']['code']=='output_write'
    assert error['response_path'] is not None and Path(error['response_path']).is_file()
    s.row(s.read(),text='βeta\nlast',selection='lines:2-3',classification='exact_repeat',status='unchanged')
    s.advance('after-output-failure')
    assert s.read()['items'][0]['status']=='content'


def test_both_complete_representation_consumers(s):
    s.ready()
    for raw in [b'{"x":1,"x":2}',b'{"x":NaN}']:
        with pytest.raises(AssertionError):strict_json(raw)
    for actual,expected in [(True,1),(1.0,1),(0,False),(None,'')]:
        with pytest.raises(AssertionError):typed_equal(actual,expected)
    code,out,err=s.raw(s.request(),'text');assert code==0 and err==''
    frames=text_frames(out);assert len(frames)==1
    assert frames[0][1]=='βeta\nlast'.encode()
    assert set(frames[0][0])==TOP_TEXT_KEYS
    expected = deepcopy(fixture()['first_response']['items'][0]); del expected['content']
    typed_equal(frames[0][0]['item'], expected)
    assert 'content' not in frames[0][0]['item']
    code,out,err=s.raw(s.request(),'text');assert code==0 and err==''
    assert text_frames(out)[0][1]==b''
    s.advance('multiple-frames');request=s.request();request['reads']=[dict(request['reads'][0],key='z'),dict(request['reads'][0],key='a')]
    code,out,err=s.raw(request,'text');assert code==0 and err==''
    frames=text_frames(out);assert [x[0]['item']['key'] for x in frames]==['z','a']
    assert [x[1] for x in frames]==['βeta\nlast'.encode(),b'']


TOP_TEXT_KEYS={'schema','status','operation','project','checkout','policy_sha256','context','item','summary','details','error','response_path'}


def test_complete_machine_output_and_common_views(s):
    s.ready();text=(FIXTURES/'long-body.txt').read_text()
    assert len(text)>99
    s.write('docs/sample.txt',text.encode());s.data['complete']['max_bytes']=16384;s.save_policy()
    code,out,err=s.raw(s.request(complete=True));assert code==0 and err==''
    result=strict_json(out);typed_equal(result['items'][0]['content'],text)
    saved=Path(result['response_path']);before=saved.read_bytes()
    for view in ['brief','expanded','full']:
        r=subprocess.run([sys.executable,'-B','-m','poise','result-view','--response',str(saved),'--view',view,'--output-chars','1200'],capture_output=True,env=s.env,check=False)
        assert r.returncode==0 and r.stderr==b'' and len(r.stdout.decode())<=1200
        v=strict_json(r.stdout);assert v['response_path']==str(saved)
        assert saved.read_bytes()==before
        if view=='full':assert text not in r.stdout.decode()


def test_functional_metadata_and_clock_owner(s,monkeypatch):
    s.ready()
    from tests.accounting.test_clock import FakeClock,observation
    class ObservedClock(FakeClock):
        count=0
        def observe(self):self.count+=1;return super().observe()
    clock=ObservedClock([observation('2026-01-01T00:00:00+00:00',n,'fixture-domain') for n in range(64)])
    code,out,err=s.transport(monkeypatch,s.request(),clock=clock)
    assert code==0 and err=='' and clock.count>0
    s.row(strict_json(out),text='βeta\nlast',selection='lines:2-3',classification='first_read')
    s.read();cache,events=s.metadata()
    assert len(cache)==1 and len(events)==2
    assert [e[6:8] for e in events]==[('content','first_read'),('unchanged','exact_repeat')]
    assert [e[-1] for e in events]==[10,0]
    assert b'\xce\xb2eta' not in s.db.read_bytes()
    assert 'duration' not in canonical(s.context())


def test_configuration_unknown_values_remain_raw(s):
    s.ready();s.data['future_option']={'opaque':['retained',123]};s.save_policy();before=s.policy.read_bytes()
    result=s.read();assert result['policy_sha256']==sha(before) and s.policy.read_bytes()==before
    s.data['bounded']['max_bytes']=False;s.save_policy()
    s.error(s.invoke(s.request()),'invalid_policy',field='policy:bounded.max_bytes')


def test_taskless_reader_preserves_specialized_owners(s):
    s.ready();task_db=s.state/s.base['cfg']['paths']['database']
    with sqlite3.connect(task_db) as db:
        before_tasks=db.execute('SELECT id,status,stage_index,iteration,claimed_by FROM tasks ORDER BY id').fetchall()
        before_sprints=db.execute('SELECT id FROM sprints ORDER BY id').fetchall()
    foreign=s.state/'foreign-specialized-receipt.json';foreign.write_bytes(b'foreign immutable specialized receipt')
    checkout=files(s.checkout)
    assert s.read()['items'][0]['status']=='content';s.advance();s.context()
    with sqlite3.connect(task_db) as db:
        assert db.execute('SELECT id,status,stage_index,iteration,claimed_by FROM tasks ORDER BY id').fetchall()==before_tasks
        assert db.execute('SELECT id FROM sprints ORDER BY id').fetchall()==before_sprints
    assert foreign.read_bytes()==b'foreign immutable specialized receipt' and files(s.checkout)==checkout


def test_template_preparation_is_non_overwriting_and_install_owned(s):
    s.prove();s.template();template=Path(s.data['session']['registry_template']);before=template.read_bytes()
    s.error(s.invoke({'operation':'prepare_template'}),'template_exists');assert template.read_bytes()==before
    with sqlite3.connect(template.as_uri()+'?mode=ro',uri=True) as db:
        assert db.execute('SELECT singleton,schema_version FROM registry_schema').fetchall()==[(1,2)]
        tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert set(fixture('schema2-shape.json')['tables'])<=tables
        assert db.execute('SELECT session_id,epoch FROM content_read_cache').fetchall()==[]
        assert db.execute('SELECT session_id,epoch FROM content_read_events').fetchall()==[]
    s.active();s.ready();assert s.db.read_bytes()==before
    template.write_bytes(b'invalid template marker');preserved=template.read_bytes()
    s.native_identity('fresh-invalid-template');s.event()
    s.error(s.invoke({'operation':'prepare'}),'template_invalid');assert template.read_bytes()==preserved


def test_session_resources_require_exact_owner_and_explicit_adoption(s):
    s.ready();s.read();before=files(s.resource)
    code,value=s.invoke({'operation':'adopt'});assert code==0 and value['details']['episode']==s.episode
    assert s.metadata()[0]
    owner=list(s.resource.rglob(s.data['session']['ownership_filename']))
    assert len(owner)==1
    original=owner[0].read_bytes();owner[0].write_bytes(b'{"foreign":"do not replace"}')
    snapshot=files(s.resource)
    for req in [s.request(),{'operation':'prepare'},{'operation':'adopt'}]:
        result=s.invoke(req);assert result[0]==1
        assert result[1]['error']['code'] in ['ownership_unknown','episode_mismatch','authority_mismatch']
        assert files(s.resource)==snapshot
    owner[0].write_bytes(original)
    assert s.read()['items'][0]['status']=='unchanged'


def test_cleanup_incomplete_preserves_diagnostics_and_recovery(s,monkeypatch):
    s.ready();s.read();s.event('SessionEnd');old=s.resource
    original=shutil.rmtree
    def fail(path,*args,**kwargs):
        if Path(path)==old:raise PermissionError('fixture removal refused')
        return original(path,*args,**kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(shutil,'rmtree',fail);code,out,err=s.transport(patch,{'operation':'recover'})
    assert code==1 and err=='';v=strict_json(out);assert v['status']=='incomplete' and old.exists()
    diagnostic=Path(s.data['session']['diagnostic_file']);assert diagnostic.is_file()
    assert not diagnostic.is_relative_to(old) and diagnostic.stat().st_size>0
    assert s.invoke({'operation':'recover'})[1]['status']=='cleaned' and not old.exists()
    # Diagnostic write after deletion is a separate observable failure boundary.
    s.event();s.ready();old=s.resource;s.event('SessionEnd');open_original=Path.open
    def denied(path,*args,**kwargs):
        mode=args[0] if args else kwargs.get('mode','r')
        if path==diagnostic and any(c in mode for c in 'wa+'):raise OSError('fixture diagnostic failure')
        return open_original(path,*args,**kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(Path,'open',denied);code,out,err=s.transport(patch,{'operation':'recover'})
    assert code==1 and not old.exists()
    assert strict_json(out)['status']=='incomplete'
    assert s.invoke({'operation':'recover'})[1]['status']=='already_absent'


def test_unknown_or_active_native_authority_preserves_owned_tree(s,monkeypatch):
    s.ready();s.read();before=files(s.resource)
    code,value=s.invoke({'operation':'recover'});assert code==1 and value['status'] in ['stale','incomplete']
    assert files(s.resource)==before
    from poise.infrastructure.hook_transport import HookService
    def unknown(*args,**kwargs):raise OSError('fixture authority transport unavailable')
    with monkeypatch.context() as patch:
        patch.setattr(HookService,'diagnose',unknown);code,out,err=s.transport(patch,{'operation':'recover'})
    assert code==1 and strict_json(out)['status'] in ['stale','incomplete']
    assert files(s.resource)==before
    s.event('SessionEnd');assert s.invoke({'operation':'recover'})[0]==0


def test_native_settings_revision_preserves_valid_binding_context(s):
    s.ready();s.read();episode=s.episode.copy()
    settings=s.root/'hook-settings.json';doc=json.loads(settings.read_text());doc['output_chars']+=1
    settings.write_text(json.dumps(doc,ensure_ascii=False,indent=2)+'\n')
    assert s.read()['items'][0]['status']=='unchanged' and s.context()['context']['native_episode']==episode
    # An unregistered settings copy is not a benign revision of the selected installation.
    foreign=s.root/'foreign-hook-settings.json';foreign.write_bytes(settings.read_bytes())
    s.data['admission']['hook_settings']=str(foreign);s.save_policy()
    code,value=s.invoke(s.request());assert code==1
    assert value['error']['code'] in ['admission_unavailable','authority_mismatch']


def test_session_end_receipt_is_exact_and_replay_safe(s):
    s.ready();s.read();old=s.resource;old_episode=s.episode.copy()
    # Caller-provided receipts cannot authorize deletion.
    s.error(s.invoke({'operation':'recover','receipt':{'event':'SessionEnd','event_id':999999,'episode':old_episode}}),'invalid_request')
    assert old.exists();s.event('SessionEnd')
    assert s.invoke({'operation':'recover'})[1]['status']=='cleaned'
    assert s.invoke({'operation':'recover'})[1]['status']=='already_absent'
    s.event();s.ready();s.read();new=s.resource
    assert new!=old and s.episode['start_event_id']>old_episode['start_event_id']
    snapshot=files(new)
    assert s.invoke({'operation':'recover'})[0]==1 and files(new)==snapshot


def test_installed_package_reader_template_and_text_consumer(s,tmp_path):
    s.prove()
    from tests.content_reader.installed_consumer import verify_installed
    verify_installed(s,tmp_path)


def test_registration_failure_and_saved_output_failure_are_truthful(s,monkeypatch):
    s.ready();before=s.metadata();connect=sqlite3.connect
    class FailCommit(sqlite3.Connection):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.mutation_seen = False
            self.set_trace_callback(self.observe)
        def observe(self, sql):
            if sql.lstrip().split(None, 1)[0].upper() in ('INSERT','UPDATE','DELETE','REPLACE'):
                self.mutation_seen = True
        def execute(self,sql,parameters=()):
            if sql.strip().upper().rstrip(';')=='COMMIT' and self.in_transaction and self.mutation_seen:raise sqlite3.OperationalError('fixture commit failure')
            return super().execute(sql,parameters)
        def commit(self):
            if self.in_transaction and self.mutation_seen:raise sqlite3.OperationalError('fixture commit failure')
            return super().commit()
        def __exit__(self,kind,value,tb):
            if kind is None and self.in_transaction and self.mutation_seen:
                self.rollback();raise sqlite3.OperationalError('fixture commit failure')
            return super().__exit__(kind,value,tb)
    def fail(database,*args,**kwargs):
        if str(s.db) in str(database):kwargs['factory']=FailCommit
        return connect(database,*args,**kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(sqlite3,'connect',fail);code,out,err=s.transport(patch,s.request())
    assert code==1 and err=='' and strict_json(out)['items'][0]['error']['code']=='registry_write'
    assert s.metadata()==before
    from poise.infrastructure.result_views import SavedResponse
    original = SavedResponse.save
    def save_failure(saved, result, *, canonical):
        if saved.path.is_relative_to(s.resource):
            raise OSError('fixture saved response refused')
        return original(saved, result, canonical=canonical)
    with monkeypatch.context() as patch:
        patch.setattr(SavedResponse, 'save', save_failure)
        code,out,err=s.transport(patch,s.request())
    assert code==1
    value=strict_json(out or err);assert value['status']=='unconfirmed' and value['error']['code']=='result_save_failed'
    assert len(s.metadata()[0])==1 and len(s.metadata()[1])==1
    assert s.read()['items'][0]['status']=='unchanged'
