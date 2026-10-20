"""Taskless delivered adapter, configuration, accounting failure and isolation."""
from copy import deepcopy
import fcntl
import json
import shlex
from pathlib import Path
import sqlite3
import subprocess
import threading

import pytest

from tests.command_intake.helpers import Intake, cases, each_port, fixture, set_field, named_project
from tests.content_reader.helpers import other_project, canonical
from tests.conftest import write_json


@pytest.mark.parametrize('case', cases('native'), ids=lambda x:x['id'])
def test_native_contract(case, project, tmp_path, monkeypatch):
    functions={'N01':installation, 'N02':binding_required, 'N03':isolation,
               'N04':repository_mismatch, 'N05':malformed_events,
               'N06':pre_storage, 'N07':post_storage, 'N08':bounded_summary,
               'N09':privacy, 'N10':concurrent_registration,
               'N11':configuration_links, 'N12':policy_validation,
               'N13':corruption}
    for i in each_port(project,tmp_path,monkeypatch):functions[case['id'][:3]](i)


def installation(i):
    from poise.modules.hook_transport.domain import HookDefinition
    definition=HookDefinition.parse(i.definition)
    assert definition.data==i.definition
    path=i.service.settings.hooks_file
    doc=json.loads(path.read_text())
    assert set(doc['hooks'])=={'SessionStart','UserPromptSubmit','Stop','SessionEnd','PreToolUse','PostToolUse'}
    for name in ['PreToolUse','PostToolUse']:
        assert len(doc['hooks'][name])==1 and doc['hooks'][name][0]['matcher']=='Bash'
    for name in ['SessionStart','UserPromptSubmit','Stop','SessionEnd']:
        assert len(doc['hooks'][name])==1
    # Install idempotence and reconfigure/reconcile are genuine public mutations
    # confined to these test-owned settings and their hook file.
    packet={'request_id':'command-events-replay','expected_revision':i.service.revision(),
            'definition':i.definition}
    installed=i.service.install(packet);before=path.read_bytes()
    assert i.service.install(packet)==installed and path.read_bytes()==before
    foreign={'matcher':'OtherTool','hooks':[{'type':'command','command':'printf foreign'}]}
    doc=json.loads(path.read_text());doc['hooks']['PreToolUse'].append(foreign);write_json(path,doc)
    # Reconcile is a distinct-identity repair of proven live-file drift.
    # End the former authority with its own definition before arranging drift.
    i.s.event('SessionEnd');assert i.s.invoke({'operation':'recover'})[0]==0
    previous_id=i.definition['id']
    current=deepcopy(i.definition);current['id']='reconciled-command-hooks'
    live=deepcopy(current);live['events'][0]['status_message']='Previous six-event command binding'
    from poise.common import digest
    live_path=i.service.settings.definitions/(digest(live)+'.json')
    write_json(live_path,live)
    for groups in doc['hooks'].values():
        for group in groups:
            if group==foreign:continue
            for hook in group['hooks']:
                argv=shlex.split(hook['command'])
                assert argv[-2:]==['--definition',i.installation['definition_path']]
                argv[-1]=str(live_path)
                hook['command']=shlex.join(argv)
                if group.get('matcher')==live['events'][0]['matcher']:
                    hook['statusMessage']=live['events'][0]['status_message']
    write_json(path,doc)
    packet={'request_id':'six-events-reconcile','expected_revision':i.service.revision(),
            'definition':current,'previous_installation_id':previous_id,
            'live_definition_path':str(live_path)}
    result=i.service.reconcile(packet)
    assert i.service.reconcile(packet)==result
    after=json.loads(path.read_text())
    assert foreign in after['hooks']['PreToolUse']
    assert len([g for g in after['hooks']['PreToolUse'] if g!=foreign])==1
    assert result['reconciliation']['previous_installation_id']==previous_id
    assert result['reconciliation']['installation_id']==current['id']
    i.definition=current;i.installation=result;i.s.installation=result
    i.s.data['admission']['definition_path']=result['definition_path'];i.s.save_policy()
    i.s.native_identity('reconciled-command-native')
    i.s.event('SessionStart');i.s.ready()
    binding=i.service.latest_binding(i.s.identity,'primary')
    assert binding['definition_path']==result['definition_path']
    i.read('head -n2 docs/A.txt')


def binding_required(i):
    from poise.common import PoiseError
    value=i.event_value('PreToolUse',i.command('head -n2 docs/A.txt'),'without-start')
    value['session_id']='never-started-native'
    before=i.service.settings.bindings.exists()
    _,result=i.hook(value)
    assert result['status']=='denied' and result['counts']['registered']==0
    assert result['error']['action']
    with pytest.raises(PoiseError):i.service.latest_binding('never-started-native','primary')
    assert i.service.settings.bindings.exists()==before
    i.counts(pending=0,current=0)
    i.read('head -n2 docs/A.txt')


def task_database(i):
    path=i.s.state/i.s.base['cfg']['paths']['database']
    if not path.exists():return None
    with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True) as db:
        # Stable logical snapshot avoids SQLite layout/WAL timing assumptions.
        return db.execute('SELECT id,status,version,metadata FROM tasks ORDER BY id').fetchall()


def isolation(i):
    from tests.content_reader.helpers import Scenario
    before=task_database(i)
    i.read('head -n2 docs/A.txt');initial=i.coverage()
    second=i.root/'registered-second'
    cp=subprocess.run(['git','-C',str(i.s.checkout),'worktree','add','-b','intake-second',str(second)],capture_output=True)
    assert cp.returncode==0,cp.stderr
    original=i.s.checkout;i.s.checkout=second
    i.s.write('docs/A.txt',fixture('cases.json')['common_vectors']['files']['A'].encode())
    empty=i.coverage();assert empty['context']['checkout_key']!=initial['context']['checkout_key']
    i.counts(pending=0,current=0);i.read('head -n2 docs/A.txt')
    i.s.checkout=original;i.ranges([[1,2]])
    peer=Scenario(i.s.base,i.root,identity='second-actual-session')
    peer.native_identity('second-actual-session')
    other=Intake(peer,i.root/'peer',i.monkeypatch,i.port)
    other.root.mkdir();other.enable();other.counts(pending=0,current=0)
    other.read('head -n3 docs/A.txt');other.advance()
    assert i.coverage()['context']['generation']==0;i.ranges([[1,2]])
    root=i.root/'another-project';root.mkdir()
    foreign=Intake(named_project(i.seed,root,'gamma'),root,i.monkeypatch,i.port).enable()
    foreign.counts(pending=0,current=0);foreign.read('head -n4 docs/A.txt')
    assert foreign.coverage()['context']['caller_key']!=initial['context']['caller_key']
    assert task_database(i)==before


def repository_mismatch(i):
    before=i.coverage()['details']
    root=i.root/'unregistered';root.mkdir();(root/'A.txt').write_text('foreign body\n')
    value=i.event_value('PreToolUse',i.command('cat A.txt',root),'foreign-checkout')
    _,result=i.hook(value)
    assert result['status']=='denied' and result['error']['action']
    assert i.coverage()['details']==before
    wrong=i.event_value('PreToolUse',i.command('head -n2 docs/A.txt'),'foreign-native-cwd')
    wrong['cwd']=str(root)
    _,result=i.hook(wrong)
    assert result['status']=='denied'
    assert i.coverage()['details']==before
    i.read('head -n2 docs/A.txt')


def malformed_events(i):
    for row in fixture('native.json')['malformed_events']:
        value=i.event_value('PreToolUse',i.command('head -n2 docs/A.txt'),'bad-event')
        if row.get('mutation')=='delete':value.pop(row['field'])
        else:value[row['field']]=row['value']
        _,result=i.hook(value)
        assert result['status']=='denied' and result['error']['action']
        assert result['counts']['registered']==0
        i.counts(pending=0,current=0)
    call,_,_=i.pre('head -n2 docs/A.txt')
    event=deepcopy(i.calls[call]);event['hook_event_name']='PostToolUse'
    _,result=i.hook(event)
    assert result['status'] in {'discarded','unconfirmed'}
    assert result['counts']['registered']==0
    # An unrelated command is a successful ignored event, not malformed input.
    _,_,result=i.pre('printf metadata')
    assert result['status']=='ignored' and result['counts']['registered']==0


def pre_storage(i):
    i.s.data['coordination']['wait_seconds']=0.15;i.s.save_policy()
    before=i.data_snapshot()
    with sqlite3.connect(i.s.db,timeout=0) as lock:
        lock.execute('BEGIN IMMEDIATE')
        _,native,result=i.pre('head -n2 docs/A.txt')
        assert result['status']=='denied' and result['error']['action']
        assert native['hookSpecificOutput']['permissionDecision']=='deny'
        lock.rollback()
    assert i.data_snapshot()==before;i.counts(pending=0,current=0)
    original=i.s.db.read_bytes();i.s.db.chmod(0)
    try:
        with pytest.raises(PermissionError):i.s.db.read_bytes()
        _,_,result=i.pre('head -n2 docs/A.txt')
        assert result['status']=='denied' and result['counts']['registered']==0
    finally:i.s.db.chmod(0o600)
    assert i.s.db.read_bytes()==original


def post_storage(i):
    i.s.data['coordination']['wait_seconds']=0.15;i.s.save_policy()
    call,_,_=i.pre('head -n2 docs/A.txt')
    response=i.actual(call)
    assert response['stdout']=='01 A\n02 A\n' and len(i.commands)==1
    with sqlite3.connect(i.s.db,timeout=0) as lock:
        lock.execute('BEGIN IMMEDIATE')
        native,result=i.post(call,response)
        assert result['status']=='unconfirmed' and result['error']['action']
        assert 'systemMessage' in native
        assert native.get('continue') is not False
        assert native.get('hookSpecificOutput',{}).get('permissionDecision')!='deny'
        lock.rollback()
    assert len(i.commands)==1 and i.plans(call)==[]
    assert response['stdout']=='01 A\n02 A\n'


def bounded_summary(i):
    i.read('head -n2 docs/A.txt');i.read("sed -n '5,6p' docs/A.txt")
    i.read('head -n2 docs/B.txt')
    i.s.write('docs/B.txt',b'changed B\n')
    i.pre('head -n2 docs/long.txt');i.pre('head -n1 docs/C.txt')
    i.configure(**{'context.max_documents':1,'context.max_ranges_per_document':1,
                   'context.max_pending_records':1,'context.max_bytes':1600})
    before=i.s.db.read_bytes();value=i.coverage();d=value['details']
    assert d['counts']=={'current_documents':1,'stale_documents':1,'pending':2,'settled':3}
    assert len(d['documents'])<=1 and len(d['pending'])==1
    assert d['omitted']['pending']==1
    assert d['omitted']['ranges']==1
    assert len(canonical(d).encode())<=1600
    assert i.s.db.read_bytes()==before
    assert fixture('cases.json')['common_vectors']['files']['A'] not in canonical(value)


def privacy(i):
    marker=fixture('native.json')['privacy_marker']
    command_marker=fixture('native.json')['command_marker']
    i.s.write('docs/private.txt',(marker+'\n').encode())
    call,_,_=i.pre('env '+command_marker+'=1 cat docs/private.txt')
    response=i.actual(call);assert marker in response['stdout']
    _,result=i.post(call,response);assert result['status']=='settled'
    assert marker.encode() not in i.s.db.read_bytes()
    assert command_marker.encode() not in i.s.db.read_bytes()
    diagnostics=Path(i.s.data['session']['diagnostic_file'])
    if diagnostics.exists():
        assert marker not in diagnostics.read_text()
        assert command_marker not in diagnostics.read_text()
    for _,control in i.controls().values():
        assert marker not in canonical(control)
        assert command_marker not in canonical(control)
    assert marker not in canonical(i.coverage())
    assert len(i.plans(call))==1


def concurrent_registration(i):
    call,_,_=i.pre('head -n4 docs/A.txt');response=i.actual(call)
    event=deepcopy(i.calls[call]);event.update(hook_event_name='PostToolUse',tool_response=response)
    barrier=threading.Barrier(3);errors=[];native=[]
    def settle():
        try:
            barrier.wait(timeout=120)
            native.append(i.service.event(i.installation['definition_path'],deepcopy(event)))
        except BaseException as exc:errors.append(exc)
    threads=[threading.Thread(target=settle,name='native-settler-'+str(n)) for n in [1,2]]
    for thread in threads:thread.start()
    barrier.wait(timeout=120)
    direct=i.s.read(path='docs/A.txt',start=3,end=6)
    for thread in threads:thread.join(timeout=120);assert not thread.is_alive()
    assert not errors and len(native)==2
    assert all(x.get('continue') is not False for x in native)
    assert direct['items'][0]['content']=='03 A\n04 A\n05 A\n06 A\n'
    assert len(i.plans(call))==1;i.ranges([[1,6]])
    later,_,_=i.pre("sed -n '7,8p' docs/A.txt")
    i.advance();_,post=i.post(later,i.actual(later))
    assert post['status']=='discarded' and i.plans(later)==[]
    ended,_,_=i.pre('head -n2 docs/B.txt');response=i.actual(ended)
    late=deepcopy(i.calls[ended]);late.update(hook_event_name='PostToolUse',tool_response=response)
    resource=i.s.resource
    i.s.event('SessionEnd');assert i.s.invoke({'operation':'recover'})[0]==0
    assert not resource.exists()
    binding_id=i.s.episode['binding_id']
    with i.service.registry.read_transaction() as db:
        starts=db.execute("SELECT count(id) FROM hook_events WHERE binding=? AND event='SessionStart'",
                          (binding_id,)).fetchone()[0]
    barrier=threading.Barrier(3);errors.clear();native.clear()
    def late_settle():
        try:
            barrier.wait(timeout=120)
            native.append(i.service.event(i.installation['definition_path'],deepcopy(late)))
        except BaseException as exc:errors.append(exc)
    threads=[threading.Thread(target=late_settle,name='ended-settler-'+str(n)) for n in [1,2]]
    for thread in threads:thread.start()
    barrier.wait(timeout=120)
    for thread in threads:thread.join(timeout=120);assert not thread.is_alive()
    assert not errors and len(native)==2 and not resource.exists()
    with i.service.registry.read_transaction() as db:
        assert db.execute("SELECT count(id) FROM hook_events WHERE binding=? AND event='SessionStart'",
                          (binding_id,)).fetchone()[0]==starts


def selected_files(i):
    return {'settings':i.service.settings.path,'profile':i.s.policy,'policy':i.policy_path}


def mutate_file(path,row):
    if row.get('mutation')=='unreadable':
        path.chmod(0)
        with pytest.raises(PermissionError):path.read_bytes()
        return
    value=json.loads(path.read_text());field=row['field']
    if row.get('mutation')=='delete':
        parts=field.split('.');owner=value
        for key in parts[:-1]:owner=owner[key]
        del owner[parts[-1]]
    else:
        replacement=row['value']
        if replacement=='@MISSING@':replacement=str(path.parent/'absent-required-file.json')
        set_field(value,field,replacement)
        if 'total_limit' in row:set_field(value,'registry.post_generation.max_total_lines',row['total_limit'])
    write_json(path,value)


def configuration_row(i,row):
    from poise.common import PoiseError
    from poise.infrastructure.hook_transport import HookService
    paths=selected_files(i);saved={k:p.read_bytes() for k,p in paths.items()}
    target=paths[row['target']]
    hook_bytes=i.service.settings.hooks_file.read_bytes()
    for phase in ['install','pre','post']:
        i.restart_episode();i.policy=fixture('policy.json');i.policy_save()
        i.read('head -n2 docs/B.txt');state=i.data_snapshot()
        call=None
        if phase=='post':
            call,_,_=i.pre('head -n2 docs/A.txt');response=i.actual(call)
            i.counts(pending=1,current=1)
        else:
            i.counts(pending=0,current=1)
        # Post starts with a valid pending intent; refused install/Pre do not.
        general=i.general_snapshot()
        mutate_file(target,row)
        try:
            if phase=='install':
                with pytest.raises(PoiseError) as error:
                    service=HookService(paths['settings'])
                    service.install({'request_id':'invalid-new-policy-'+str(i.sequence),
                                     'expected_revision':service.revision(),'definition':i.definition})
                assert str(error.value)
                assert i.service.settings.hooks_file.read_bytes()==hook_bytes
            else:
                if phase=='pre':_,native,result=i.pre('head -n2 docs/A.txt')
                else:native,result=i.post(call,response)
                assert result['counts']['registered']==0
                assert result['error']['message'] and result['error']['action']
                if row['field']:
                    # Validation identifies the literal bad known key.
                    assert row['field'].split('.')[-1] in result['error']['field']
                if phase=='pre':
                    assert result['status']=='denied'
                    assert native['hookSpecificOutput']['permissionDecision']=='deny'
                else:
                    assert result['status']=='unconfirmed' and native['systemMessage']
                    assert response['stdout']=='01 A\n02 A\n'
                    assert native.get('hookSpecificOutput',{}).get('permissionDecision')!='deny'
        finally:
            for key,path in paths.items():
                path.chmod(0o600);path.write_bytes(saved[key])
        assert i.data_snapshot()==state
        after=i.general_snapshot()
        assert after['events']==general['events']
        if phase=='post':
            # Failure may retain or discard the existing intent. No new call
            # may be invented; separate diagnostic metadata is not compared.
            assert set(after['calls']).issubset(general['calls'])
        else:
            assert after['calls']==general['calls']
            i.counts(pending=0,current=1)
        assert i.coverage()['context']['generation']==0
        i.ranges([[1,2]],'B')
        if call is not None:assert i.plans(call)==[]


def configuration_links(i):
    i.read('head -n2 docs/A.txt')
    assert i.s.read(path='docs/A.txt',start=3,end=4)['items'][0]['status']=='content'
    for row in fixture('native.json')['links']:configuration_row(i,row)
    # Direct-only remains valid for direct use, separate from six-event activation.
    i.restart_episode();profile=deepcopy(i.s.data);profile.pop('command_intake')
    original=i.s.policy
    i.s.policy=i.root/'legacy-direct-profile.json'
    write_json(i.s.policy,profile)
    try:
        assert i.s.read(path='docs/A.txt',start=1,end=2)['items'][0]['content']=='01 A\n02 A\n'
    finally:i.s.policy=original


def policy_validation(i):
    i.read('head -n2 docs/A.txt')
    for row in fixture('cases.json')['common_vectors']['configuration_failures']['N12_rows']:
        configuration_row(i,dict(row,target='policy'))
    raw=i.policy_path.read_bytes();value=fixture('policy.json')
    value['future_raw_option']={'retained':[123,'opaque']};write_json(i.policy_path,value)
    expected=i.policy_path.read_bytes()
    try:
        i.read("sed -n '3,4p' docs/B.txt")
        i.ranges([[1,4]],'B')
        assert i.policy_path.read_bytes()==expected
    finally:i.policy_path.write_bytes(raw)


def corrupt_file(i,kind,phase):
    if kind.startswith('template'):
        path=Path(i.s.data['session']['registry_template'])
    else:path=i.s.db
    if kind=='registry-bytes':path.write_bytes(b'not a SQLite registry\n')
    else:
        with sqlite3.connect(path) as db:
            if kind=='registry-schema':db.execute('UPDATE registry_schema SET schema_version=?',(999,))
            elif kind=='registry-pending':
                db.execute('UPDATE pending_calls SET plans_json=?',('not-json',))
            elif kind=='registry-read':db.execute('UPDATE read_events SET plan_json=?',('not-json',))
            elif kind=='template-missing':db.execute('ALTER TABLE pending_calls RENAME COLUMN plans_json TO invalid_field')
            elif kind=='template-type':db.execute('UPDATE registry_schema SET schema_version=?',('bad-type',))
            else:raise AssertionError(kind)
    return path,path.read_bytes()


def corruption(i):
    kinds=['registry-bytes','registry-schema','registry-pending','registry-read',
           'template-missing','template-type']
    for kind in kinds:
        phases=['pre'] if kind.startswith('template') else ['pre','post']
        for phase in phases:
            i.restart_episode();i.read('head -n2 docs/B.txt')
            call,_,_=i.pre('head -n2 docs/A.txt');response=i.actual(call)
            template=Path(i.s.data['session']['registry_template']);template_original=template.read_bytes()
            if kind.startswith('template'):
                # New resource creation consumes the bad template, not a valid
                # already-created registry. Preserve the existing one as control.
                i.s.event('SessionEnd');assert i.s.invoke({'operation':'recover'})[0]==0
                i.s.event('SessionStart')
            path,corrupt=corrupt_file(i,kind,phase)
            try:
                if phase=='pre':_,native,result=i.pre('head -n2 docs/A.txt')
                else:native,result=i.post(call,response)
                assert result['counts']['registered']==0
                assert result['error']['message'] and result['error']['action']
                if phase=='pre':
                    assert result['status']=='denied'
                    assert native['hookSpecificOutput']['permissionDecision']=='deny'
                else:
                    assert result['status']=='unconfirmed' and native['systemMessage']
                    assert native.get('hookSpecificOutput',{}).get('permissionDecision')!='deny'
                assert path.read_bytes()==corrupt
            finally:
                template.write_bytes(template_original)
            # Cleanup through the actual lifecycle owner; no production repair.
            i.s.event('SessionEnd');assert i.s.invoke({'operation':'recover'})[0]==0
            i.s.event('SessionStart');i.s.ready()
