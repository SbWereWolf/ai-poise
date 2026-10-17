"""Falsify test consumers directly; no reader implementation or future import."""
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import uuid

import pytest

from tests.content_reader.helpers import Scenario, fixture, canonical, sha
from tests.content_reader.contract_checks import (
    complete_expected, assert_complete_read, assert_header, header_expected,
    assert_text, render_text, PrefixFailure, assert_template,
)


def check_consumers(tmp_path):
    root = tmp_path / 'wire-consumer'
    root.mkdir()
    s = Scenario.__new__(Scenario)
    s.project, s.origin, s.identity = 'fixture-project', 'native', 'fixture-native'
    s.root, s.checkout = root, root / 'checkout'
    s.policy = root / 'policy.json'; s.policy.write_text(canonical(fixture()['policy']))
    s.data = {'session':{'temporary_root':str(root/'temporary'),'response_directory':'responses'},
              'storage':{'control_response_root':str(root/'control')}}
    s.resource = root/'temporary/own-episode'
    s.episode = {'project_id':s.project,'session_id':s.identity,'binding_id':s.identity,'start_event_id':7}
    s.checkout_keys = {str(s.checkout):'independent-fixture-checkout'}
    path = s.resource/'responses/independent-fixture-checkout'/uuid.UUID(int=17).hex/'response.json'
    path.parent.mkdir(parents=True)
    good = complete_expected(s,items=fixture()['first_response']['items'],
                             summary=fixture('context-sequences.json')['first'],generation=0,path=str(path))
    def save(value):
        path.write_text(canonical(value))
    def row(value):
        return s.row(value,text='βeta\nlast',selection='lines:2-3',classification='first_read',
                     summary=fixture('context-sequences.json')['first'])
    save(good)
    s.envelope(good,operation='read',exit_code=0); row(good)
    assert_complete_read(s,good,good);assert_text(s,render_text(good),good)
    # Legitimate error frames have no content metadata and exactly zero body.
    error_spec=fixture('error-frame-cases.json')
    error_result=complete_expected(s,items=[error_spec['failed_item']],summary=error_spec['empty_summary'],
                                   generation=0,path=str(path),status='failed')
    save(error_result);s.envelope(error_result,operation='read',exit_code=3)
    frames=assert_text(s,render_text(error_result),error_result)
    assert len(frames)==1 and frames[0][1]==b''
    equal_item=deepcopy(good['items'][0]);equal_item['key']='z'
    repeat_item=deepcopy(equal_item);repeat_item.update(key='a',status='unchanged',classification='exact_repeat',
               content_follows=False,previous_read_available=True);del repeat_item['content']
    mixed=complete_expected(s,items=[error_spec['failed_item'],equal_item,repeat_item],
                            summary=error_spec['mixed_summary'],generation=0,path=str(path),status='partial')
    save(mixed);s.envelope(mixed,operation='read',exit_code=1)
    frames=assert_text(s,render_text(mixed),mixed)
    assert [v[0]['item']['key'] for v in frames]==error_spec['mixed_keys']
    assert [v[1] for v in frames]==[b'','βeta\nlast'.encode(),b'']
    from tests.content_reader.helpers import text_frames
    for item in [equal_item,repeat_item]:
        for missing in ['content_follows','content_bytes','content_lines','context_generation','hash']:
            bad_item={k:v for k,v in item.items() if k not in {'content',missing}}
            with pytest.raises(AssertionError):text_frames(canonical(header_expected(good,bad_item)))
        for name,value in [('content_follows',0),('content_bytes',True),('content_lines',2.0),('context_generation',False)]:
            bad_item={k:v for k,v in item.items() if k!='content'};bad_item[name]=value
            with pytest.raises(AssertionError):text_frames(canonical(header_expected(good,bad_item)))
    bad_error=dict(error_spec['failed_item'],content_follows=False)
    with pytest.raises(AssertionError):text_frames(canonical(header_expected(error_result,bad_error)))
    bad_error=deepcopy(error_spec['failed_item']);bad_error['error']['key']='foreign-key'
    with pytest.raises(AssertionError):text_frames(canonical(header_expected(error_result,bad_error)))
    save(good)
    cases = fixture('consumer-invalid.json')
    for case in cases:
        bad = deepcopy(good); target = bad
        for key in case['path'][:-1]: target = target[key]
        target[case['path'][-1]] = case['value']
        save(bad)  # Wire and saved bytes agree: semantic rejection must do the work.
        with pytest.raises(AssertionError):
            s.envelope(bad,operation='read',exit_code=0)
        with pytest.raises(AssertionError):row(bad)
        with pytest.raises(AssertionError):assert_complete_read(s,bad,good)
        if case['path'][0] != 'items':
            actual_header = header_expected(bad,bad['items'][0])
            with pytest.raises(AssertionError):assert_header(actual_header,header_expected(good,good['items'][0]))
            with pytest.raises(AssertionError):assert_text(s,render_text(bad),good)
    save(good)
    # Legitimate early/template nulls retain their distinct public contract.
    early = deepcopy(good)
    error = next(v for v in fixture()['errors'] if v['code']=='invalid_request')
    early.update(status='rejected',operation=None,project=None,checkout=None,policy_sha256=None,
                 context=None,items=[],summary=None,details=None,response_path=None,
                 error={k:error[k] for k in ['code','field','message','action']}|{'key':None})
    s.envelope(early,exit_code=2)
    control = root/'control'/uuid.UUID(int=18).hex/'response.json';control.parent.mkdir(parents=True)
    prepared = deepcopy(good);prepared.update(operation='prepare_template',context=None,items=[],summary=None,
              details={'path':str(root/'template.sqlite'),'schema_version':2,'sha256':'0'*64,'status':'prepared'},
              response_path=str(control))
    control.write_text(canonical(prepared));s.envelope(prepared,operation='prepare_template',exit_code=0)
    for code,status,details in [('template_preparation_failed','failed',None),
                              ('template_publication_unconfirmed','unconfirmed',dict(prepared['details'],status='published_durability_unconfirmed'))]:
        v=deepcopy(prepared);e=next(e for e in fixture()['errors'] if e['code']==code)
        v.update(status=status,details=details,error={k:e[k] for k in ['code','field','message','action']}|{'key':None})
        control.write_text(canonical(v));s.envelope(v,operation='prepare_template',exit_code=1)
    # Independently retain the exact arithmetic reproducer of the original flaw.
    wrong=deepcopy(good);wrong['items'][0]['classification']='new_context_generation'
    assert len(canonical(wrong).encode())-len(canonical(good).encode())==12
    next_item=deepcopy(good['items'][0]);next_item.update(context_generation=1,classification='new_context_generation')
    correct=complete_expected(s,items=[next_item],summary=fixture('context-sequences.json')['second_generation_content'],generation=1,path=str(path))
    assert correct['context']['generation']==correct['items'][0]['context_generation']==1
    assert correct['summary']['event_count']==correct['summary']['content_events']==2
    for wire in [canonical(good),render_text(good)]:
        for limit in [0,17,len(wire)-len('βeta\nlast')+1]:
            for chunks in [[wire],[wire[:5],wire[5:19],wire[19:]]]:
                sink=PrefixFailure(limit)
                with pytest.raises(OSError):
                    for chunk in chunks:sink.write(chunk)
                assert sink.failed and sink.getvalue()==wire[:limit]
    # A schema with literal columns but missing a required constraint must fail.
    shape=fixture('schema2-shape.json')
    schema=root/'template-consumer.sqlite'
    with sqlite3.connect(schema) as db:
        for table,columns in shape['columns'].items():
            parts=[]
            composite=sorted((c[3],c[0]) for c in columns if c[3])
            for name,kind,notnull,pk in columns:
                part='"'+name+'" '+kind
                if notnull:part+=' NOT NULL'
                if name in ('singleton','id'):
                    part+=' PRIMARY KEY'
                    if name=='id':part+=' AUTOINCREMENT'
                    if name=='singleton':part+=' CHECK (singleton=1)'
                parts.append(part)
            if len(composite)>1:parts.append('PRIMARY KEY('+','.join('"'+name+'"' for _,name in composite)+')')
            if table=='read_events':parts.append('UNIQUE(session_id,tool_use_id,path_label,operation_key)')
            db.execute('CREATE TABLE "'+table+'" ('+','.join(parts)+')')
        for table,indexes in shape['indexes'].items():
            for number,index in enumerate(indexes):
                if not index['unique']:
                    db.execute('CREATE INDEX "fixture_'+table+'_'+str(number)+'" ON "'+table+'" ('+','.join('"'+c+'"' for c in index['columns'])+')')
        db.execute('INSERT INTO registry_schema(singleton,schema_version) VALUES (?,?)',(1,2))
    assert_template(schema)
    with sqlite3.connect(schema) as db:
        db.execute('DROP TABLE pending_calls')
        db.execute('CREATE TABLE pending_calls (session_id TEXT NOT NULL,turn_id TEXT NOT NULL,tool_use_id TEXT NOT NULL,epoch INTEGER NOT NULL,plan_hash TEXT NOT NULL,plans_json TEXT NOT NULL,created_at TEXT NOT NULL)')
    with pytest.raises(AssertionError):assert_template(schema)
