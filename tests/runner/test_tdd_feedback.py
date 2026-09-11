"""The test-fix branch uses RED, before the code-change branch uses GREEN."""
import json
from pathlib import Path
from conftest import write_json, add_test
from conftest import Poise
from .helpers import inspect, finding, resolution, decision
from .test_runner_paths import result


def test_test_remediation_reuses_handlers_with_red_contract(project):
    root=Path(__file__).resolve().parents[2]
    proc=json.loads((root/'config/processes/development.json').read_text())
    write_json(project['root']/'config/processes/development.json',proc)
    cfg=project['cfg'];cfg['schema']='ddd-accounting-11';cfg['automatic_checks']=[]
    write_json(project['config_path'],cfg)
    task=project['task']
    task['checks']={s['id']: (['RED'] if s['id'] in ('tests','test_fix') else ['GREEN'] if s['id'] in ('implementation','code_review','code_fix','code_recheck') else []) for s in proc['stages']}
    task['evidence_plan']={s['id']:{'subject_methods':{},'arguments':[],'review_arguments':[]} for s in proc['stages']};write_json(project['task_path'],task)
    h=Poise(project['config_path'],'S1');ctx=h.bootstrap(task_file=project['task_path'])
    add_test(ctx['worktree']);result(ctx,{})
    assert h.verify()['checks'][0]['actual_exit_code']==1
    ctx=h.bootstrap(decision='continue');assert ctx['stage']=='test_review'
    result(ctx,inspect([finding()]));h.verify()
    ctx=h.bootstrap(decision='continue');assert ctx['stage']=='test_fix'
    test=Path(ctx['worktree'])/'tests/test_double.py'
    test.write_text(test.read_text()+'\n# Clarified fixture intent.\n')
    result(ctx,{'resolutions':[resolution()]})
    checked=h.verify();assert checked['status']=='verified' and checked['checks'][0]['actual_exit_code']==1
    ctx=h.bootstrap(decision='continue');assert ctx['stage']=='test_recheck'
    result(ctx,inspect(decisions=[decision()]));h.verify()
    ctx=h.bootstrap(decision='continue');assert ctx['stage']=='implementation'
    Path(ctx['worktree'],'src/double.py').write_text('def double(n):\n    return n * 2\n')
    result(ctx,{});assert h.verify()['checks'][0]['actual_exit_code']==0
    ctx=h.bootstrap(decision='continue');assert ctx['stage']=='code_review'
    result(ctx,inspect());h.verify()
    assert h.accept()['status']=='completed'
    assert h.show()['workflow']['feedback']['open_findings']==[]
