"""Pending intentions become coverage only through eligible actual outcomes."""
from copy import deepcopy

import pytest

from tests.command_intake.helpers import Intake, FakeClock, cases, each_port, fixture


@pytest.mark.parametrize('case', cases('settlement'), ids=lambda x:x['id'])
def test_settlement_contract(case, project, tmp_path, monkeypatch):
    functions={'S01':pending, 'S02':immutable_pre, 'S03':conflicting_pre,
               'S04':success, 'S05':nonzero, 'S06':invalid_result,
               'S07':unknown_exit, 'S08':post_replay, 'S09':late_turn,
               'S10':expiry, 'S11':clock_comparison, 'S12':stale_partial,
               'S13':changed_generation, 'S14':new_episode}
    if case['id'][:3] in {'S10','S11'}:
        i=Intake(project,tmp_path,monkeypatch).enable()
        functions[case['id'][:3]](i)
    else:
        for i in each_port(project,tmp_path,monkeypatch):functions[case['id'][:3]](i)


def pending(i):
    call,_,pre=i.pre('head -n2 docs/A.txt')
    assert pre['status']=='pending' and pre['counts']['registered']==0
    i.counts(pending=1,current=0)
    assert i.plans(call)==[]
    _,post=i.post(call,i.actual(call))
    assert post['status']=='settled' and post['counts']['registered']==1
    i.counts(pending=0,current=1); i.ranges([[1,2]])
    plans=i.plans(call);assert len(plans)==1 and plans[0][0]==0


def immutable_pre(i):
    call,_,first=i.pre('head -n2 docs/A.txt')
    command=i.calls[call]['tool_input']['command']
    i.s.write('docs/A.txt',b'new current file\n')
    _,_,again=i.pre(command,call_id=call,raw=True)
    assert again==first
    i.counts(pending=1,current=0)
    _,post=i.post(call,{'output':'new current file\n','exit_code':0})
    assert post['counts']['registered']==0
    i.counts(pending=0,current=0)


def conflicting_pre(i):
    call,_,_=i.pre('head -n2 docs/A.txt')
    initial=deepcopy(i.calls[call])
    for changes in [{'tool_input':{'command':i.command('head -n3 docs/A.txt')}},
                    {'turn_id':'another-pre-turn'}, {'tool_name':'OtherReader'}]:
        event=deepcopy(initial);event.update(changes)
        _,result=i.hook(event)
        assert result['status']=='conflict',result
        assert result['counts']['registered']==0
        i.counts(pending=1,current=0)
    _,post=i.post(call,i.actual(call))
    assert post['status']=='settled';i.ranges([[1,2]])


def success(i):
    i.configure(**{'registry.post_result.minimum_response_bytes':10})
    for response in fixture('settlement.json')['known_success']:
        i.restart_episode()
        call,_,_=i.pre('head -n2 docs/A.txt')
        actual=i.actual(call)
        assert actual['stdout']=='01 A\n02 A\n' and actual['exit_code']==0
        _,post=i.post(call,response)
        assert post['status']=='settled'
        assert post['counts']['registered']==1
        i.ranges([[1,2]]);i.counts(pending=0,current=1)


def nonzero(i):
    call,_,_=i.pre('head -n2 docs/A.txt')
    _,post=i.post(call,{'output':fixture('settlement.json')['body'],'exit_code':1})
    assert post['status']=='discarded' and post['counts']['registered']==0
    assert post['decision']['reason_code']=='known_failure'
    assert i.plans(call)==[];i.counts(pending=0,current=0)


def invalid_result(i):
    i.configure(**{'registry.post_result.minimum_response_bytes':10})
    for response in fixture('settlement.json')['invalid']:
        call,_,_=i.pre('head -n2 docs/A.txt')
        _,post=i.post(call,response)
        assert post['status']=='discarded'
        assert post['decision']['reason_code'] in {'insufficient_response','invalid_response'}
        assert post['counts']['registered']==0 and i.plans(call)==[]
        i.counts(pending=0,current=0)


def unknown_exit(i):
    for success_policy in [False,True]:
        for response in [fixture('settlement.json')['body'],
                         {'output':fixture('settlement.json')['body']}]:
            i.restart_episode()
            i.configure(**{'registry.post_result.unknown_exit_code_is_success':success_policy})
            call,_,_=i.pre('head -n2 docs/A.txt')
            _,post=i.post(call,response)
            assert post['status']==('settled' if success_policy else 'discarded')
            assert post['counts']['registered']==(1 if success_policy else 0)
            i.counts(pending=0,current=1 if success_policy else 0)
            if success_policy:i.ranges([[1,2]])
            else:assert i.plans(call)==[]


def post_replay(i):
    call,_,_=i.pre('head -n2 docs/A.txt');response=i.actual(call)
    _,first=i.post(call,response);before=i.plans(call)
    _,again=i.post(call,response)
    assert again==first and i.plans(call)==before
    _,conflict=i.post(call,dict(response,stdout='different response'))
    assert conflict['status']=='conflict' and conflict['counts']['registered']==0
    assert i.plans(call)==before


def late_turn(i):
    call,_,_=i.pre('head -n2 docs/A.txt')
    _,post=i.post(call,i.actual(call),turn='turn2')
    assert post['status']=='settled'
    assert post['call']['pre_turn_id']=='turn1' and post['call']['post_turn_id']=='turn2'
    assert len(i.plans(call))==1
    _,same=i.post(call,i.actual(call),turn='turn2')
    assert same==post and len(i.plans(call))==1


def expiry(i):
    clock=FakeClock();i.use_clock(clock)
    call,_,_=i.pre('head -n2 docs/A.txt')
    clock.ns=12_000_000_000;i.counts(pending=1,current=0)
    clock.ns=12_000_000_001
    _,late=i.post(call,{'output':fixture('settlement.json')['body'],'exit_code':0})
    assert late['status']=='discarded'
    assert late['decision']['reason_code']=='pending_expired'
    assert i.plans(call)==[];i.counts(pending=0,current=0)


def clock_comparison(i):
    for domain,ns in [('fixture-boot-B',12_000_000_001),('fixture-boot-A',9_999_999_999)]:
        clock=FakeClock();i.use_clock(clock)
        call,_,_=i.pre('head -n2 docs/A.txt')
        before=i.data_snapshot();clock.ns,clock.domain=ns,domain
        _,post=i.post(call,{'output':fixture('settlement.json')['body'],'exit_code':0})
        assert post['status']=='unconfirmed' and post['error']['code']=='incomparable_clock'
        assert post['error']['action'] and post['counts']['registered']==0
        assert i.data_snapshot()==before and i.plans(call)==[]
        clock.ns,clock.domain=11_000_000_000,'fixture-boot-A'
        i.counts(pending=1,current=0)
        _,settled=i.post(call,{'output':fixture('settlement.json')['body'],'exit_code':0})
        assert settled['status']=='settled'
        i.restart_episode()


def stale_partial(i):
    call,_,_=i.pre('head -n2 docs/A.txt docs/B.txt')
    i.s.write('docs/A.txt',b'changed A\n')
    _,post=i.post(call,i.actual(call))
    assert post['status']=='settled'
    assert post['counts']=={'eligible':2,'skipped':1,'registered':1}
    plans=i.plans(call);assert len(plans)==1 and plans[0][1]=='docs/B.txt'
    i.ranges([[1,2]],'B');i.counts(pending=0,current=1)


def changed_generation(i):
    call,_,_=i.pre('head -n2 docs/A.txt')
    i.advance()
    _,post=i.post(call,i.actual(call))
    assert post['status']=='discarded'
    assert post['decision']['reason_code']=='generation_changed'
    assert i.plans(call)==[];i.counts(pending=0,current=0)
    assert i.coverage()['context']['generation']==1


def new_episode(i):
    call,_,_=i.pre('head -n2 docs/A.txt');old_event=deepcopy(i.calls[call])
    old_resource=i.s.resource;old_episode=deepcopy(i.s.episode)
    i.restart_episode()
    assert i.s.episode!=old_episode and not old_resource.exists()
    binding=i.service.latest_binding(i.s.identity,'primary')
    i.calls[call]=old_event
    _,late=i.post(call,{'output':fixture('settlement.json')['body'],'exit_code':0})
    assert late['status']=='unmatched' and late['counts']['registered']==0
    assert not old_resource.exists()
    assert i.service.latest_binding(i.s.identity,'primary')==binding
    i.counts(pending=0,current=0)
    i.read('head -n2 docs/A.txt');i.ranges([[1,2]])
