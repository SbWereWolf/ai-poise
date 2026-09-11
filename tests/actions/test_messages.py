from harness.application.work import WorkTools
from conftest import WorkHarness as Harness
from batch.helpers import request,message,bootstrap


def test_all_received_user_messages_count_in_active_task_including_readonly_and_unknown(project):
    tools=WorkTools(Harness(project['config_path'],'MESSAGES'))
    ctx=bootstrap(tools,project,[message('start','initial')])
    events=[message('continue','continue'),message('clarify','clarification'),message('unknown',None),
            message('permission','authorization'),message('feedback','feedback')]
    query={'queries':[{'id':'context','kind':'task'}]}
    out=tools.invoke(request('show',query,events))
    assert out['interaction']['user_messages_count']==6
    assert out['interaction']['unclassified_messages']==1
    assert tools.invoke(request('show',query,events))['interaction']['user_messages_count']==6
    cancelled=tools.invoke(request('cancel',{'reason':'User: stop'},[message('stop','cancel')]))
    assert cancelled['interaction']['user_messages_count']==7


def test_later_taskless_read_does_not_charge_completed_or_cancelled_task(project):
    h=Harness(project['config_path'],'MESSAGES');tools=WorkTools(h)
    bootstrap(tools,project,[message('start','initial')])
    tools.invoke(request('cancel',{'reason':'User stop'},[message('cancel','cancel')]))
    tools.invoke(request('show',{'queries':[{'id':'context','kind':'task'}]},[message('new-topic',None)]))
    assert h.interactions.summary(h.current_task())['user_messages_count']==2
    assert h.interactions.summary(None)['user_messages_count']==3


def test_mechanical_publication_is_not_counted_as_content_stage(project):
    from actions.helpers import setup, result, verify, advance, inspect, call
    h,ctx,plan,base=setup(project,conflict=False)
    assert h.interactions.summary(h.current_task())['planned_content_stages_count']==4
    applied=verify(h,result(ctx,{'plan':plan,'phase':'prepare','resolutions':[],'finding_resolutions':[]}))
    assert applied['status']=='verified'
    inspected=inspect(h,advance(h));assert inspected['status']=='verified'
    ctx=advance(h)
    out=verify(h,result(ctx,{'target_ref':'refs/heads/main','expected_commit':base,
                            'authorization':'User: publish the accepted integration'}))
    assert out['status']=='verified'
    final=call(h,'accept',{})
    stats=final['interaction']
    assert stats['planned_content_stages_count']==4
    assert stats['delivered_stages_count']==2
    assert stats['delivered_iterations_count']==2
