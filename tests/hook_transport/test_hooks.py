import json
import os
import shlex
import subprocess
import sys
from copy import deepcopy
from pathlib import Path
import pytest
from poise.common import PoiseError
from poise.modules.hook_transport.domain import HookDefinition
from poise.infrastructure.hook_transport import HookService
from poise.modules.ownership.domain import Liveness
from batch.helpers import request
from .helpers import settings,definition,install,event


def service(project,tmp_path):return HookService(settings(project,tmp_path))


def native(s,out,name='SessionStart',session='conversation',turn='turn1'):
    e=event(name,session,turn);e['cwd']=str(s.settings.root)
    return s.event(out['definition_path'],e)


def test_definition_requires_explicit_fields():
    d=definition();d['events'][0].pop('timeout_seconds')
    with pytest.raises(PoiseError):HookDefinition.parse(d)


def test_unsafe_async_state_binding_rejected():
    d=definition();d['events'][0]['async']=True
    with pytest.raises(PoiseError):HookDefinition.parse(d)


def test_unsupported_events_no_silent_fallback():
    d=definition();d['events'][0]['event']='ImaginaryEvent'
    with pytest.raises(PoiseError):HookDefinition.parse(d)


def test_install_preserves_foreign_hooks_and_trust_not_assumed(project,tmp_path):
    s=service(project,tmp_path)
    path=s.settings.hooks_file;path.parent.mkdir(parents=True)
    foreign={'description':'user-owned','hooks':{'PostToolUse':[{'hooks':[{'type':'command','command':'echo foreign'}]}]}}
    path.write_text(json.dumps(foreign))
    out=install(s,revision=s.revision())
    doc=json.loads(path.read_text());assert doc['description']=='user-owned'
    assert doc['hooks']['PostToolUse']==foreign['hooks']['PostToolUse']
    assert out['trust_status']=='requires_user_review' and out['live_codex']=='not_observed'
    assert not (s.settings.root/'.codex/config.toml').exists()


def test_install_replay_and_edit_do_not_duplicate(project,tmp_path):
    s=service(project,tmp_path);out=install(s)
    assert install(s)==out
    d=definition();d['events'][0]['timeout_seconds']=20
    edit=install(s,d,'edit',out['revision'])
    doc=json.loads(s.settings.hooks_file.read_text())
    assert len(doc['hooks']['SessionStart'])==1 and edit['revision']!=out['revision']
    with pytest.raises(PoiseError):install(s,d,'install-1',out['revision'])


def test_stale_config_rejected_without_changes(project,tmp_path):
    s=service(project,tmp_path);out=install(s)
    before=s.settings.hooks_file.read_bytes()
    with pytest.raises(PoiseError):install(s,request_id='stale',revision='0'*64)
    assert s.settings.hooks_file.read_bytes()==before


def test_invalid_plan_writes_no_hooks(project,tmp_path):
    s=service(project,tmp_path);d=definition();d['probes'][0].pop('cwd')
    with pytest.raises(PoiseError):install(s,d)
    assert not s.settings.hooks_file.exists()


def test_session_launcher_is_quoted_and_executable(project,tmp_path):
    s=service(project,tmp_path);out=install(s);native_out=native(s,out)
    context=native_out['hookSpecificOutput']['additionalContext']
    binding=s.latest_binding('conversation','primary')
    path=Path(binding['launcher']);assert str(path) in context and path.is_file()
    assert (" -P -B -X pycache_prefix=" + os.devnull + " -m poise hook-work ") in path.read_text()
    p=subprocess.run([str(path)],input=json.dumps(request('bootstrap',{'task':None,'decision':None,'feedback':None,'rework_stage':None})),text=True,capture_output=True,timeout=15)
    assert p.returncode==0,p.stderr
    assert json.loads(p.stdout)['status']=='read_only'


def test_existing_owned_launcher_is_upgraded_to_bytecode_safe_command(project,tmp_path):
    s=service(project,tmp_path);out=install(s);native(s,out)
    binding=s.latest_binding('conversation','primary')
    path=Path(binding['launcher']);current=path.read_text()
    # The historical entry neither used -B nor forwarded caller arguments.
    legacy='#!/bin/sh\nexec '+shlex.join([
        'env','PYTHONPATH='+s.settings.raw['source_root'],s.settings.raw['python'],
        '-m','poise','hook-work','--settings',str(s.settings.path),
        '--binding',str(binding['binding_path']),
    ])+'\n'
    assert legacy!=current
    path.write_text(legacy);path.chmod(s.settings.raw['executable_mode'])
    native(s,out)
    assert path.read_text()==current


def test_external_launcher_edit_is_rejected(project,tmp_path):
    s=service(project,tmp_path);out=install(s);native(s,out)
    binding=s.latest_binding('conversation','primary')
    path=Path(binding['launcher']);changed='#!/bin/sh\necho external-change\n'
    path.write_text(changed);path.chmod(s.settings.raw['executable_mode'])
    with pytest.raises(PoiseError,match='Launcher was changed externally'):
        native(s,out)
    assert path.read_text()==changed


def test_hooks_count_all_user_turns_once_without_prompt_text(project,tmp_path):
    s=service(project,tmp_path);out=install(s);native(s,out)
    native(s,out,'UserPromptSubmit');native(s,out,'UserPromptSubmit')
    binding=s.latest_binding('conversation','primary')
    work=request('bootstrap',{'task':project['task'],'decision':None,'feedback':None,'rework_stage':None})
    start=s.work(binding['binding_path'],work)
    assert start['interaction']['observed_messages_count']==1
    native(s,out,'UserPromptSubmit',turn='turn2')
    read=s.work(binding['binding_path'],request('show',{'queries':[{'id':'m','kind':'messages'}]}))
    assert read['interaction']['observed_messages_count']==2
    raw=s.settings.database.read_bytes()
    assert b'private user content' not in raw and b'private assistant content' not in raw


def test_two_conversations_get_separate_bindings(project,tmp_path):
    s=service(project,tmp_path);out=install(s)
    native(s,out,session='A');native(s,out,session='B')
    a=s.latest_binding('A','primary');b=s.latest_binding('B','primary')
    assert a['session_id']!=b['session_id'] and a['launcher']!=b['launcher']


def test_native_session_liveness_is_fail_closed_and_session_end_is_definitive(project,tmp_path):
    s=service(project,tmp_path);out=install(s)
    assert s.registry.liveness('missing') is Liveness.UNCERTAIN
    native(s,out,session='owner')
    binding=s.latest_binding('owner','primary')
    assert s.registry.liveness(binding['session_id']) is Liveness.LIVE
    native(s,out,'SessionEnd',session='owner')
    assert s.registry.liveness(binding['session_id']) is Liveness.DEAD


def test_public_native_dead_owner_recovery_moves_both_claims(project,tmp_path,monkeypatch):
    s=service(project,tmp_path);out=install(s)
    native(s,out,session='owner');native(s,out,session='contender')
    owner=s.latest_binding('owner','primary')
    contender=s.latest_binding('contender','primary')
    monkeypatch.setenv('CODEX_THREAD_ID', 'owner')
    started=s.work(owner['binding_path'],request('bootstrap',{
        'task':project['task'],'decision':None,'feedback':None,'rework_stage':None}))
    acquire=request('bootstrap',{
        'task':{'id':started['task']},'decision':None,'feedback':None,'rework_stage':None})
    owner_runtime=s.bound_runtime(owner['binding_path'])

    def claims():
        with owner_runtime.store.transaction() as db:
            return (
                dict(db.execute('SELECT id,claimed_by FROM tasks')),
                dict(db.execute('SELECT id,task_id FROM sessions')),
            )

    before=claims()
    monkeypatch.setenv('CODEX_THREAD_ID', 'contender')
    with pytest.raises(PoiseError,match='live'):
        s.work(contender['binding_path'],acquire)
    assert claims()==before
    native(s,out,'SessionEnd',session='owner')
    recovered=s.work(contender['binding_path'],acquire)
    tasks,worktrees=claims()
    assert recovered['worktree']==started['worktree']
    assert tasks[started['task']]==contender['session_id']
    assert worktrees[owner['session_id']] is None
    assert worktrees[contender['session_id']]==started['task']
    assert list(worktrees.values()).count(started['task'])==1


def test_session_end_releases_files_and_actual_working_entry_restores_access(project,tmp_path):
    s=service(project,tmp_path);out=install(s)
    native(s,out,session='released-session')
    binding=s.latest_binding('released-session','primary')
    session_runtime=s.bound_runtime(binding['binding_path']).runtime
    session_runtime.mkdir(parents=True,exist_ok=True)
    marker=session_runtime/'temporary-agent-data.txt'
    marker.write_text('session transient\n')
    assert marker.read_text()=='session transient\n'
    directory=Path(binding['binding_path']).parent
    unrelated=directory/'delivered-result.txt'
    unrelated.write_text('accepted result\n')
    assert (directory/'capabilities.json').is_file()

    native(s,out,'SessionEnd',session='released-session')

    with s.registry.read_transaction() as db:
        assert db.execute('SELECT id FROM bindings WHERE id=?',(binding['session_id'],)).fetchall()==[]
    assert not Path(binding['binding_path']).exists()
    assert not Path(binding['launcher']).exists()
    assert not (directory/'capabilities.json').exists()
    assert not session_runtime.exists()
    assert unrelated.read_text()=='accepted result\n'
    assert s.registry.liveness(binding['session_id']) is Liveness.DEAD
    assert s.registry.liveness('never-observed') is Liveness.UNCERTAIN

    assert native(s,out,'SessionEnd',session='released-session')=={}
    assert native(s,out,'Stop',session='released-session')=={}
    assert not Path(binding['launcher']).exists()
    resumed=native(s,out,'UserPromptSubmit',session='released-session')
    restored=s.latest_binding('released-session','primary')
    assert restored['session_id']==binding['session_id']
    assert restored['launcher']==binding['launcher']
    assert Path(restored['launcher']).is_file()
    assert Path(restored['binding_path']).is_file()
    assert s.registry.liveness(binding['session_id']) is Liveness.LIVE
    assert restored['launcher'] in resumed['hookSpecificOutput']['additionalContext']
    assert unrelated.read_text()=='accepted result\n'


def test_active_prompt_recreates_missing_binding_and_issues_usable_entry(project,tmp_path,monkeypatch):
    s=service(project,tmp_path);out=install(s)
    native(s,out,session='active-missing-binding')
    binding=s.latest_binding('active-missing-binding','primary')
    with s.registry.transaction() as db:
        start_id=db.execute("SELECT id FROM hook_events WHERE binding=? AND event='SessionStart' ORDER BY id DESC LIMIT 1",(binding['session_id'],)).fetchone()[0]
        db.execute('DELETE FROM bindings WHERE id=?',(binding['session_id'],))
    Path(binding['binding_path']).unlink()
    Path(binding['launcher']).unlink()

    delivered=native(s,out,'UserPromptSubmit',session='active-missing-binding')

    restored=s.latest_binding('active-missing-binding','primary')
    assert restored==binding
    assert Path(binding['binding_path']).is_file()
    assert Path(binding['launcher']).is_file()
    assert binding['launcher'] in delivered['hookSpecificOutput']['additionalContext']
    assert 'systemMessage' not in delivered
    with s.registry.read_transaction() as db:
        assert [tuple(row) for row in db.execute("SELECT id FROM hook_events WHERE binding=? AND event='SessionStart'",(binding['session_id'],))]==[(start_id,)]
    monkeypatch.setenv('CODEX_THREAD_ID','active-missing-binding')
    observed=s.work(binding['binding_path'],request('show',{'queries':[{'id':'m','kind':'messages'}]}))
    assert observed['status']=='read_only'


def test_releasing_task_preserves_continuing_session_access(project,tmp_path,monkeypatch):
    s=service(project,tmp_path);out=install(s)
    native(s,out,session='continuing-session')
    binding=s.latest_binding('continuing-session','primary')
    monkeypatch.setenv('CODEX_THREAD_ID','continuing-session')
    started=s.work(binding['binding_path'],request('bootstrap',{
        'task':project['task'],'decision':None,'feedback':None,'rework_stage':None}))
    runtime=s.bound_runtime(binding['binding_path'])
    current=runtime.task_queries.record(started['task'])
    released=s.work(binding['binding_path'],request('release',{
        'request_id':'release-for-next-task','reason':'Release Task while the session continues.',
        'task_id':started['task'],'expected_version':current['version']}))
    assert released['status']=='ownership_released'
    with runtime.store.transaction() as db:
        assert tuple(db.execute('SELECT claimed_by FROM tasks WHERE id=?',(started['task'],)).fetchone())==(None,)
        assert tuple(db.execute('SELECT task_id FROM sessions WHERE id=?',(binding['session_id'],)).fetchone())==(None,)
    assert Path(binding['launcher']).is_file()
    assert s.registry.liveness(binding['session_id']) is Liveness.LIVE
    again=s.work(binding['binding_path'],request('bootstrap',{
        'task':{'id':started['task']},'decision':None,'feedback':None,'rework_stage':None}))
    assert again['task']==started['task']
    assert again['worktree']==started['worktree']
    with runtime.store.transaction() as db:
        assert tuple(db.execute('SELECT claimed_by FROM tasks WHERE id=?',(started['task'],)).fetchone())==(binding['session_id'],)
        assert tuple(db.execute('SELECT task_id FROM sessions WHERE id=?',(binding['session_id'],)).fetchone())==(started['task'],)


def test_missing_turn_id_is_not_invented(project,tmp_path):
    s=service(project,tmp_path);out=install(s);e=event('UserPromptSubmit');e.pop('turn_id');e['cwd']=str(project['root'])
    with pytest.raises(PoiseError):s.event(out['definition_path'],e)


def test_stop_does_not_accept_or_force_another_user_prompt(project,tmp_path):
    s=service(project,tmp_path);out=install(s);native(s,out);b=s.latest_binding('conversation','primary')
    s.work(b['binding_path'],request('bootstrap',{'task':project['task'],'decision':None,'feedback':None,'rework_stage':None}))
    reply=native(s,out,'Stop')
    assert 'decision' not in reply and 'systemMessage' in reply
    h=s.bound_runtime(b['binding_path'])
    assert h.current_task()['status']=='active'
    native(s,out,'SessionEnd')
    assert h.current_task()['status']=='active'


def test_unavailable_required_probe_blocks_write_not_read_or_cancel(project,tmp_path):
    s=service(project,tmp_path);d=definition();d['probes'][0]['argv']=['/missing/required-program']
    out=install(s,d);native(s,out);b=s.latest_binding('conversation','primary')
    result=s.work(b['binding_path'],request('bootstrap',{'task':project['task'],'decision':None,'feedback':None,'rework_stage':None}))
    assert result['status']=='capabilities_unavailable'
    assert 'capability_checks' in result
    read=s.work(b['binding_path'],request('show',{'queries':[{'id':'m','kind':'messages'}]}))
    assert read['status']=='read_only'


def test_work_packet_cannot_supply_second_source_messages(project,tmp_path):
    s=service(project,tmp_path);out=install(s);native(s,out);b=s.latest_binding('conversation','primary')
    from batch.helpers import message
    with pytest.raises(PoiseError):s.work(b['binding_path'],request('show',{'queries':[]},[message()]))


def test_parent_cwd_outside_configured_roots_is_rejected(project,tmp_path):
    s=service(project,tmp_path);out=install(s);e=event();e['cwd']='/other'
    with pytest.raises(PoiseError):s.event(out['definition_path'],e)


def test_partial_install_resumes_after_file_replace(project,tmp_path,monkeypatch):
    s=service(project,tmp_path)
    from poise.infrastructure import hook_transport as module
    original=module.atomic_write
    tripped=False
    def fail_after_replace(path,body,mode):
        nonlocal tripped
        original(path,body,mode)
        if path==s.settings.hooks_file and not tripped:
            tripped=True
            raise OSError('injected post-replace failure')
    monkeypatch.setattr(module,'atomic_write',fail_after_replace)
    with pytest.raises(OSError):install(s)
    assert s.settings.hooks_file.is_file()
    out=install(s)
    assert out==install(s)
    assert len(json.loads(s.settings.hooks_file.read_text())['hooks']['SessionStart'])==1


def test_external_managed_hook_edit_is_not_overwritten(project,tmp_path):
    s=service(project,tmp_path);out=install(s)
    raw=json.loads(s.settings.hooks_file.read_text())
    raw['hooks']['SessionStart'][0]['hooks'][0]['command']='echo user-edit'
    s.settings.hooks_file.write_text(json.dumps(raw))
    with pytest.raises(PoiseError):install(s,request_id='edit',revision=s.revision())
    assert json.loads(s.settings.hooks_file.read_text())==raw


def test_generated_hook_command_accepts_native_stdin(project,tmp_path):
    s=service(project,tmp_path);out=install(s)
    command=json.loads(s.settings.hooks_file.read_text())['hooks']['SessionStart'][0]['hooks'][0]['command']
    e=event();e['cwd']=str(project['root'])
    result=subprocess.run(command,shell=True,input=json.dumps(e),text=True,capture_output=True,timeout=10)
    assert result.returncode==0,result.stderr
    reply=json.loads(result.stdout)
    assert reply['hookSpecificOutput']['hookEventName']=='SessionStart'


def test_current_ide_probe_uses_new_task_worktree(project,tmp_path):
    s=service(project,tmp_path);d=definition()
    p=d['probes'][0]
    p.update(project_bound=True,stdout_contains=[],
        argv=[sys.executable,'-c','import os,json;print(json.dumps({"project":os.getcwd()}))'],
        json_assertions=[{'path':['project'],'equals':'${workspace}'}])
    out=install(s,d);native(s,out);b=s.latest_binding('conversation','primary')
    ctx=s.work(b['binding_path'],request('bootstrap',{'task':project['task'],'decision':None,'feedback':None,'rework_stage':None}))
    assert ctx['capability_checks']['ready']
    assert ctx['capability_checks']['observations'][0]['project_path']==ctx['worktree']
    assert ctx['worktree']!=str(project['app'])


def test_small_output_setting_rejected_before_installation(project,tmp_path):
    path=settings(project,tmp_path);raw=json.loads(path.read_text());raw['output_chars']=1;path.write_text(json.dumps(raw))
    with pytest.raises(PoiseError):HookService(path)
    assert not (project['root']/'.codex/hooks.json').exists()


def test_invalid_matcher_rejected_before_install(project,tmp_path):
    s=service(project,tmp_path);d=definition();d['events'][0]['matcher']='['
    with pytest.raises(PoiseError):install(s,d)
    assert not s.settings.hooks_file.exists()


def test_user_event_without_prompt_is_not_counted(project,tmp_path):
    s=service(project,tmp_path);out=install(s);e=event('UserPromptSubmit')
    e['cwd']=str(project['root']);e.pop('prompt')
    with pytest.raises(PoiseError):s.event(out['definition_path'],e)


def test_taskless_summary_not_blocked_by_missing_required_ide(project,tmp_path):
    s=service(project,tmp_path);d=definition();d['probes'][0]['argv']=['/missing/ide']
    out=install(s,d);native(s,out);b=s.latest_binding('conversation','primary')
    summary=s.work(b['binding_path'],request('bootstrap',{'task':None,'decision':None,'feedback':None,'rework_stage':None}))
    assert summary['status']=='read_only'
    assert summary['capability_checks']['ready'] is False


@pytest.fixture(autouse=True)
def primary_native_host(monkeypatch):
    """Primary-hook fixtures run as the conversation explicitly named in their events."""
    monkeypatch.delenv('CODEX_SESSION_ID', raising=False)
    monkeypatch.delenv('POISE_CALLER_BINDING', raising=False)
    monkeypatch.setenv('CODEX_THREAD_ID', 'conversation')
