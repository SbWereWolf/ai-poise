import pytest
from copy import deepcopy
from pathlib import Path
from conftest import write_json
from poise.runtime import Poise
from poise.application.work import WorkTools
from tests.runner.helpers import process,inspect,finding,resolution,decision
from tests.batch.helpers import request
from .test_paths import DeterministicClock,metrics,send
from .test_domain import sample


@pytest.mark.parametrize('mode',['tool_cycle','reported'])
def test_only_explicit_late_findings_count_and_group_cost_is_not_divided(project,mode):
    project['cfg']['accounting']['time_mode']=mode
    p=process('development');project['cfg']['automatic_checks']=[]
    write_json(project['root']/'config/processes/development.json',p);write_json(project['config_path'],project['cfg'])
    t=deepcopy(project['task']);t['methods']=[];t['method_inputs']=[];t['checks']={s['id']:[] for s in p['stages']}
    t['evidence_plan']={s['id']:{'subject_methods':{},'arguments':[],'review_arguments':[]} for s in p['stages']}
    h=Poise(project['config_path'],'Q',DeterministicClock());w=WorkTools(h)
    ctx=w.invoke(request('bootstrap',{'task':t,'decision':None,'feedback':None,'rework_stage':None}))
    def verify(work):
        value=deepcopy(ctx['result_template']);value['sections']['report']='Current result';value['commit_message']='feat: result';value['stage_work']=work
        return w.invoke(request('verify',{'result':value,'artifacts':[]}))
    (Path(ctx['worktree'])/'src/double.py').write_text('def double(n):\n    return n*2\n')
    verify({})
    ctx=w.invoke(request('bootstrap',{'task':None,'decision':'continue','feedback':None,'rework_stage':None}))
    verify(inspect([finding('F1'),finding('F2')]))
    assert metrics(w)['tasks'][0]['quality_findings_count']==0
    ctx=w.invoke(request('bootstrap',{'task':None,'decision':'continue','feedback':None,'rework_stage':None}))
    span={'source':'test','stream':'fix-time','event_id':'t1','started_at':'2026-09-07T10:00:00Z','ended_at':'2026-09-07T10:10:00Z'}
    send(w,[sample()],cause='delivered_rework',intervals=[span] if mode=='reported' else [],finding_targets=[
        {'finding_id':'F1','stage':'draft','iteration':1},{'finding_id':'F2','stage':'draft','iteration':1}])
    r=metrics(w);assert r['tasks'][0]['quality_findings_count']==2
    assert r['totals']['by_cause']['delivered_rework']['model_tokens']==120
    assert r['totals']['remediation_groups'][0]['finding_ids']==['F1','F2']
    assert r['totals']['remediation_groups'][0]['model_tokens']==120
    if mode=='reported':
        assert r['totals']['remediation_groups'][0]['active_seconds']==600
        assert r['totals']['by_cause']['delivered_rework']['active_seconds']==600
    verify({'resolutions':[resolution('R1','F1'),resolution('R2','F2')]})
    ctx=w.invoke(request('bootstrap',{'task':None,'decision':'continue','feedback':None,'rework_stage':None}))
    verify(inspect(decisions=[decision('R1'),decision('R2')]))
    w.invoke(request('accept',{}))
    assert metrics(w)['tasks'][0]['resolved_quality_findings_count']==2
