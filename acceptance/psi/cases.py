"""PSI cases. Arrange constructs real configuration; Act uses the installed CLI."""
from __future__ import annotations
import argparse, json, os, sqlite3, traceback
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from support import Case, snapshot, write


def taskless(c):
    c.arrange(); before=snapshot(c.repo)
    c.phase='act'; opened=c.bootstrap(); assert opened['status']=='read_only',opened
    closed=c.work('verify',{'result':None,'artifacts':[]})
    c.phase='assert'; assert closed['status']=='read_only_verified'; c.assert_empty()
    assert snapshot(c.repo)==before
    assert not list((c.state/'worktrees').iterdir())


def standalone(c):
    c.arrange();c.seed_requirements();ready=c.create();assert ready['status']=='available',ready
    before=c.git('rev-parse','HEAD')
    c.phase='act';opened=c.bootstrap('T1');assert opened['status']=='active',opened
    assert Path(opened['worktree']).resolve()!=c.repo.resolve()
    c.finish(context=opened)
    c.phase='assert';assert c.git('rev-parse','HEAD')!=before
    assert not c.git('status','--porcelain')


def maintenance(c):
    c.arrange(install=False)
    before=snapshot(c.profile); before_repo=snapshot(c.repo)
    c.phase='act'
    for verb, flags in [('check',()),('infra',('--dry-run',)),('deps',('--dry-run',))]:
        report=c.maintenance(verb,*flags,expected=(0,3))
        assert all('apply' not in item for item in report['requirements'])
    assert before==snapshot(c.profile) and before_repo==snapshot(c.repo)
    assert not c.state.exists()
    for verb in ('deps','infra','check'):
        assert c.maintenance(verb)['status'] in ('ready','repaired')
    before_state=snapshot(c.state);before=snapshot(c.profile)
    for verb in ('infra','deps','check'):
        assert c.maintenance(verb)['status']=='ready'
    c.phase='assert';c.assert_empty()
    assert snapshot(c.state)==before_state and snapshot(c.profile)==before


def damaged(c):
    c.arrange()
    # Deliberately corrupt a disposable fixture, never operational state.
    db=c.state/'state.sqlite';db.write_bytes(b'PSI corrupt SQLite fixture')
    before=snapshot(c.state);before_profile=snapshot(c.profile)
    c.phase='act'
    for verb in ('check','infra'):
        report=c.maintenance(verb,expected=((3,) if verb=='check' else (4,)))
        item=next(x for x in report['requirements'] if x['id']=='poise-task-database')
        assert item['recommendation']
        assert 'corrupt' in item['initial_check']['response']['code']
    c.phase='assert';assert snapshot(c.state)==before and snapshot(c.profile)==before_profile


def permissions(c):
    c.arrange()
    db=c.state/'state.sqlite';before=db.read_bytes();db.chmod(0)
    c.phase='act'
    report=c.maintenance('check',expected=(3,))
    item=next(x for x in report['requirements'] if x['id']=='poise-task-database')
    assert item['initial_check']['response']['code']=='permissions'
    c.phase='assert';assert db.stat().st_mode&0o777==0
    db.chmod(0o600);assert db.read_bytes()==before
    assert c.maintenance('check')['status']=='ready'


def override(c):
    c.arrange(install=False);other=c.root/'other-state'
    c.phase='act'
    for verb in ('deps','infra','check'):
        assert c.maintenance(verb,'--set','state_root='+str(other))['status'] in ('ready','repaired')
    c.phase='assert';assert (other/'state.sqlite').is_file() and not c.state.exists()
    assert c.maintenance('check',expected=(3,))['status']=='action_required'


def restart(c):
    c.arrange();c.seed_requirements();c.create()
    opened=c.bootstrap('T1');tree=c.produce(opened,value='WIP preserved\n')
    c.git('add','src/T1.txt',cwd=tree)
    before=c.git('status','--porcelain',cwd=tree)
    c.phase='act'
    born=c.work('task',{'action':'restart','request_id':'restart-fixture','task_id':'T1',
        'expected_version':opened['version'],'reason':'Exercise authorized restart preserving WIP.',
        'authorization':{'role':'user','decision':'Acceptance requires restarting this disposable fixture.'}})
    assert born['status']=='newborn'
    assert c.git('status','--porcelain',cwd=tree)==before
    assert (tree/'src/T1.txt').read_text()=='WIP preserved\n'
    ready=c.work('task',{'action':'ready','request_id':'restart-ready','task_id':'T1','expected_revision':born['revision']})
    assert ready['status']=='available'
    resumed=c.bootstrap('T1');assert Path(resumed['worktree'])==tree
    c.finish(context=resumed)
    c.phase='assert';assert (c.repo/'src/T1.txt').read_text()=='ACCEPTED\n'


def rework(c):
    c.arrange();c.seed_requirements();c.create()
    opened=c.bootstrap('T1');tree=c.produce(opened,value='WRONG\n')
    before=c.git('rev-parse','HEAD')
    c.phase='act';failed=c.verify(opened,expected=(1,));assert failed['status']=='checks_failed',failed
    assert c.git('rev-parse','HEAD')==before
    rejected=c.work('accept',{},expected=(1,2));assert rejected['status']!='completed'
    resumed=c.bootstrap('T1');assert resumed['status']=='active'
    c.finish(context=resumed)
    c.phase='assert';assert (c.repo/'src/T1.txt').read_text()=='ACCEPTED\n'


def crash(c):
    c.arrange();c.seed_requirements();c.create()
    opened=c.bootstrap('T1');tree=c.produce(opened,value='INTERRUPTED\n')
    before=snapshot(tree)
    # Every old CLI child has exited; no writer survives in this isolated fixture.
    c.phase='act';blocked=c.bootstrap('T1',expected=(0,1,2),actor='recovery')
    assert blocked['status']!='active',blocked
    out=c.work('show',{'queries':[{'id':'recovery','kind':'ownership_recovery','task_ids':['T1']}]},actor='recovery')
    def find(value):
        if isinstance(value,dict):
            if 'recovery_template' in value: return value['recovery_template']
            for child in value.values():
                found=find(child)
                if found:return found
        if isinstance(value,list):
            for child in value:
                found=find(child)
                if found:return found
    packet=find(out);assert packet, out
    body=packet['input'] if 'input' in packet else packet
    body['reason']='All old command processes exited; isolated acceptance fixture.'
    body['authorization']={'role':'user','decision':'Exercise explicit crash recovery of this disposable fixture.'}
    body['writers_stopped']=True
    recovered=c.work('recover_ownership',body,actor='recovery');assert recovered['status']=='ownership_recovered'
    assert snapshot(tree)==before
    c.actor='recovery';resumed=c.bootstrap('T1');assert resumed['status']=='active'
    assert (tree/'src/T1.txt').read_text()=='INTERRUPTED\n'
    c.finish(context=resumed);c.phase='assert'


def cancel(c):
    c.arrange();c.seed_requirements();c.create()
    opened=c.bootstrap('T1');tree=c.produce(opened,value='CANCELLED WIP\n')
    target=c.git('rev-parse','HEAD')
    c.phase='act';result=c.work('cancel',{'reason':'Acceptance explicitly cancels this disposable fixture.'})
    assert result['status']=='cancelled',result
    c.phase='assert';terminal=c.bootstrap('T1');assert terminal['status']=='cancelled',terminal
    assert (tree/'src/T1.txt').read_text()=='CANCELLED WIP\n'
    assert c.git('rev-parse','HEAD')==target
    listing=c.json([c.cli,'next','--settings',c.profile/'setup.json'])
    write(c.root/'next-after-cancel.json',listing)
    assert 'T1' not in json.dumps(listing)


def sprint(c):
    c.arrange();c.seed_requirements()
    draft=c.work('sprint',{'action':'draft','sprint_id':'S','request_id':'sprint-create','expected_revision':None,
        'template':{'id':'basic','version':'1'},'changes':[
            {'kind':'purpose','goal':'A supplies a file required by B.','requirements':['B consumes A output.'],
             'definition_of_done':['Both outputs are integrated.']},
            {'kind':'sections','values':{'plan':'Complete and integrate A before B consumes its output.'}},
            {'kind':'upsert_tasks','tasks':[]},{'kind':'dependencies','items':[]}]})
    c.create('A','S'); c.create('B','S')
    plan=c.work('show',{'queries':[{'id':'plan','kind':'sprint','sprint_id':'S','view':'current'}]})
    write(c.root/'sprint-before-publish.json',plan)
    def revision(v):
        if isinstance(v,dict):
            if 'revision' in v:return v['revision']
            for x in v.values():
                found=revision(x)
                if found is not None:return found
        if isinstance(v,list):
            for x in v:
                found=revision(x)
                if found is not None:return found
    rev=revision(plan);assert rev is not None,plan
    updated=c.work('sprint',{'action':'draft','sprint_id':'S','request_id':'sprint-edges','expected_revision':rev,
        'template':None,'changes':[{'kind':'dependencies','items':[{'predecessor':'A','successor':'B','kind':'result'}]}]})
    published=c.work('sprint',{'action':'publish','sprint_id':'S','request_id':'sprint-publish','expected_revision':updated['revision']})
    assert published['status']=='planned' and not published['errors'],published
    assert published['eligible']==['A']
    assert published['blocked'][0]['task']=='B'
    assert {x['status'] for x in published['tasks']}=={'available'}
    c.phase='act';blocked=c.bootstrap('B',expected=(0,1,2))
    assert blocked['status']!='active',blocked
    c.finish('A')
    opened=c.bootstrap('B');assert opened['status']=='active',opened
    assert (Path(opened['worktree'])/'src/A.txt').read_text()=='ACCEPTED\n'
    c.finish('B',context=opened)
    c.phase='assert';assert (c.repo/'src/A.txt').exists() and (c.repo/'src/B.txt').exists()

def integration(c):
    c.arrange();c.seed_requirements();c.create()
    opened=c.bootstrap('T1');tree=c.produce(opened)
    main_before=c.git('rev-parse','HEAD')
    assert not (c.repo/'src/T1.txt').exists()
    c.phase='act';checked=c.verify(opened);assert checked['status']=='verified'
    accepted=c.work('accept',{});assert accepted['status']=='completed'
    accepted_commit=accepted.get('commit') or checked['commit']
    assert c.git('rev-parse','HEAD')==main_before
    # A real target advancement is a fixture input, not a mocked Git result.
    (c.repo/'src/parallel.txt').write_text('Keep this independent change.\n')
    c.git('add','src/parallel.txt');c.git('commit','-m','Concurrent fixture change')
    target=c.git('rev-parse','HEAD')
    intent={'request_id':'drift-integration','task_id':'T1','expected_source_commit':accepted_commit,
            'expected_target_commit':target,'authorization':'PSI local integration is authorized.','resolutions':[]}
    out=c.work('integrate',intent);assert out['status']=='integrated',out
    c.phase='assert'
    c.git('merge-base','--is-ancestor',accepted_commit,'HEAD');c.git('merge-base','--is-ancestor',target,'HEAD')
    assert (c.repo/'src/T1.txt').read_text()=='ACCEPTED\n'
    assert (c.repo/'src/parallel.txt').read_text()=='Keep this independent change.\n'
    assert not tree.exists() and not c.git('status','--porcelain')
    head=c.git('rev-parse','HEAD')
    out=c.work('integrate',intent);assert out['replayed'] is True and c.git('rev-parse','HEAD')==head

CASES={'integration':integration,'maintenance':maintenance,'damaged':damaged,'permissions':permissions,'override':override,
       'taskless':taskless,'standalone':standalone,'restart':restart,'rework':rework,
       'crash':crash,'cancel':cancel,'sprint':sprint}


def main():
    p=argparse.ArgumentParser();p.add_argument('--case',required=True,choices=sorted(CASES));p.add_argument('--workspace',type=Path,required=True)
    p.add_argument('--prepared-workspace',action='store_true');p.add_argument('--python',type=Path,required=True);p.add_argument('--source',type=Path,required=True)
    a=p.parse_args()
    if not a.prepared_workspace:a.workspace.mkdir(parents=True,exist_ok=False)
    elif not a.workspace.is_dir() or (a.workspace/'result.json').exists():raise ValueError('PSI workspace already contains a result')
    c=Case(a.workspace.resolve(),a.python.resolve(),a.source.resolve(),a.case)
    report={'schema':'poise/psi-result/v1','case':a.case,'status':'FAIL','uid':os.geteuid(),'commands':c.commands}
    try:
        if os.geteuid()==0: raise RuntimeError('Run PSI under a non-root UID to verify actual access permissions')
        CASES[a.case](c);report['status']='PASS'
    except BaseException as e:
        report.update(error=str(e),phase=c.phase,traceback=traceback.format_exc())
    write(c.root/'result.json',report)
    print(json.dumps({k:v for k,v in report.items() if k!='commands'},ensure_ascii=False))
    return 0 if report['status']=='PASS' else 1

if __name__=='__main__':raise SystemExit(main())
