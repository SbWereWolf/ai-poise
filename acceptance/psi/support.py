"""Black-box acceptance driver; only the installed public CLI mutates Poise state."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from copy import deepcopy


def write(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')


def digest_file(path: Path) -> str:
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def snapshot(root: Path) -> dict:
    return {str(p.relative_to(root)): {'sha256':digest_file(p),'mode':p.stat().st_mode&0o777}
            for p in root.rglob('*') if p.is_file() and not p.is_symlink()}


class Case:
    def __init__(self, root: Path, python: Path, source: Path, case_id: str):
        self.root, self.python, self.source, self.case_id = root, python, source, case_id
        self.log = root/'evidence'; self.log.mkdir(parents=True, exist_ok=False)
        self.commands: list[dict] = []
        self.env = {'PATH': str(python.parent)+':/usr/bin:/bin', 'HOME':str(root),
                    'LANG':'C.UTF-8', 'PYTHONDONTWRITEBYTECODE':'1'}
        self.cli = python.parent/'poise'
        self.repo, self.profile, self.state = root/'repository', root/'profile', root/'state'
        self.actor = 'executor'
        self.phase = 'arrange'

    def run(self, argv, packet=None, expected=(0,), cwd=None, env=None, timeout=90):
        argv = list(map(str, argv)); ident = len(self.commands)+1
        prefix = self.log/f'{ident:03d}'
        if packet is not None: write(prefix.with_suffix('.input.json'), packet)
        started = time.monotonic()
        with prefix.with_suffix('.stdout').open('wb') as out, prefix.with_suffix('.stderr').open('wb') as err:
            try:
                proc = subprocess.run(argv, input=None if packet is None else json.dumps(packet).encode(),
                                      stdout=out, stderr=err, cwd=cwd or self.root,
                                      env=env or self.env, timeout=timeout)
                rc = proc.returncode
            except subprocess.TimeoutExpired:
                rc = 124
        stdout = prefix.with_suffix('.stdout').read_text(errors='replace')
        stderr = prefix.with_suffix('.stderr').read_text(errors='replace')
        receipt = {'argv':argv, 'phase':self.phase, 'exit_code':rc,
                   'seconds':time.monotonic()-started,
                   'stdout':str(prefix.with_suffix('.stdout').relative_to(self.root)),
                   'stderr':str(prefix.with_suffix('.stderr').relative_to(self.root)),
                   'stdout_sha256':digest_file(prefix.with_suffix('.stdout')),
                   'stderr_sha256':digest_file(prefix.with_suffix('.stderr'))}
        self.commands.append(receipt); write(prefix.with_suffix('.receipt.json'),receipt)
        if rc not in expected:
            raise AssertionError(f'{argv[:3]} exited {rc}; expected {expected}: {stdout[-2500:]} {stderr[-2000:]}')
        return stdout

    def json(self, argv, packet=None, expected=(0,), **kw):
        value=json.loads(self.run(argv,packet,expected,**kw))
        if value.get('details') == 'full_result':
            # This is the CLI's documented durable full-response reference.
            path=Path(value['response_path']).resolve()
            if not path.is_relative_to(self.root.resolve()):
                raise AssertionError('Response reference escaped the PSI workspace')
            value=json.loads(path.read_text())
            write(self.log/f'{len(self.commands):03d}.full.json',value)
        return value

    def git(self,*args,cwd=None):
        return self.run(['git','-C',cwd or self.repo,*args]).strip()

    def work(self,operation,input,expected=(0,),actor=None):
        env=dict(self.env,POISE_CONFIG=str(self.profile/'project/project.json'),
                 POISE_CALLER_BINDING=str(self.root/'callers'/f'{actor or self.actor}.json'))
        return self.json([self.cli,'work'],{'operation':operation,'input':input,'messages':[]},
                         expected,env=env,cwd=self.repo)

    def bootstrap(self,identifier=None,**kw):
        return self.work('bootstrap',{'task':None if identifier is None else {'id':identifier},
                                     'decision':None,'feedback':None,'rework_stage':None},**kw)

    def requirements(self,operation,input):
        return self.json([self.cli,'requirements','--config',self.profile/'project/project.json'],
                         {'operation':operation,'input':input})

    def maintenance(self,verb,*args,expected=(0,)):
        return self.json([self.cli,verb,'--catalog',self.profile/'requirements.json',
                          '--repairs',self.profile/'repairs.json',*args],expected=expected)

    def arrange(self, install=True):
        (self.root/'callers').mkdir()
        self.run(['git','init','-b','main',self.repo])
        self.git('config','user.name','PSI executor');self.git('config','user.email','psi@example.invalid')
        (self.repo/'src').mkdir();(self.repo/'src/baseline.txt').write_text('baseline\n')
        self.git('add','src');self.git('commit','-m','Acceptance baseline')
        self.json([self.python,'-I','-m','poise_environment.profile','--source',self.source,
                   '--output',self.profile,'--state',self.state,'--repository',self.repo,
                   '--project',self.case_id,'--base-ref','main','--author-name','PSI executor',
                   '--author-email','psi@example.invalid'])
        assert not self.state.exists(), 'Profile generation unexpectedly created DB state'
        if install:
            for verb in ('deps','infra','check'):
                assert self.maintenance(verb)['status'] in ('repaired','ready')
            self.assert_empty()
        return self

    def assert_empty(self):
        import sqlite3
        # Independent read-only assertion, never lifecycle writes.
        with sqlite3.connect((self.state/'state.sqlite').as_uri()+'?mode=ro',uri=True) as db:
            assert db.execute('SELECT count(*) FROM tasks').fetchone()[0]==0
            assert db.execute('PRAGMA integrity_check').fetchall()==[('ok',)]

    def seed_requirements(self):
        return self.requirements('apply',{'request_id':'psi-requirements','expected_revision':0,'operations':[
            {'kind':'put_requirement','requirement':{'id':'PSI-SYSTEM','level':'system','status':'current',
                'text':'Execute declared tasks and preserve their verified results.'}},
            {'kind':'put_requirement','requirement':{'id':'PSI-APPLICATION','level':'application','status':'current',
                'text':'Deliver the exact acceptance fixture output through the public lifecycle.'}},
            {'kind':'link','system':'PSI-SYSTEM','application':'PSI-APPLICATION'}]})

    def draft(self,identifier='T1',sprint=None,worktree=True):
        text=f'File src/{identifier}.txt contains exactly ACCEPTED\\n.'
        if sprint and identifier=='B': text+=' The prerequisite src/A.txt must contain the accepted A result.'
        planned=self.requirements('query',{'queries':[{'id':'plan','kind':'plan_task',
            'task_requirements':[{'text':text,'applications':['PSI-APPLICATION']}]}]})['results'][0]['value']
        assert planned['status']=='ready', planned
        stage={'id':'work','handler':'produce','instruction':'Produce and verify the fixture result.',
            'transitions':{'complete':None},'rework_targets':['work'],'read_only':False,
            'allowed_paths':['src/**'],'normalization':'strip','sections':{'report':'Describe the result.'},
            'required_sections':['report'],'artifact_requirements':[]}
        process={'goal_type':'psi_delivery','worktree_required':worktree,'route':{'entry':'work'},
                 'content_contract':{'sections':[],'routes':[],'requirements':[]},'stages':[stage],
                 'benefit':{'git_categories':['code'],'sections':[]}}
        return {'goal_type':'psi_delivery','goal':f'Deliver fixture result {identifier}.',
            'requirements':[text],'requirements_snapshot':planned['snapshot'],
            'requirements_agreement':{'accepted':True,'chains':planned['chains']},
            'definition_of_done':['The exact output is verified and integrated.'],
            'methods':[{'id':'OUTPUT','argv':[str(self.python),'-I','-c',
                f'from pathlib import Path; assert Path("src/{identifier}.txt").read_text()=="ACCEPTED\\n"; '+('assert Path("src/A.txt").read_text()=="ACCEPTED\\n"; ' if sprint and identifier=='B' else '')+'print("OUTPUT_OK")'],
                'cwd':'.','environment':{},'source_under_test':{'kind':'repository','bindings':[{'kind':'cwd','path':'.'}]},
                'verification_plan':{'responsibility':'Verify the actual fixture file content.','change_surface':['src/**'],
                    'red_stages':[],'green_stages':['work'],'red_failure':None},
                'expected_exit_code':0,'stdout_contains':['OUTPUT_OK'],'stderr_contains':[]}],
            'method_inputs':[{'method_id':'OUTPUT','repository_inputs':[],'future_outputs':[],
                'reference_profile':{'runner':'python','parser':'inline-no-path-arguments','version':1}}],
            'checks':{'work':['OUTPUT']},'artifact_requirements':[],
            'content_contract':{'sections':[],'routes':[],'requirements':[]},
            'evidence_plan':{'work':{'subject_methods':{},'arguments':[],'review_arguments':[]}},
            'decomposition':{'kind':'ordinary','phases':[{'stage':'work','skills':['general'],'areas':[]}],'integration':None},
            'stage_contracts':[{'stage_id':'work','allowed_paths':['src/**'],'entry_requirements':[],'exit_requirements':[]}],
            'process':process,'planning':{'schema':'task-planning-1','template':None,
                'restart_revision_policy':{'reviewer':[],'user':['goal','process','stage_contracts','checks','methods',
                    'method_inputs','definition_of_done','evidence_plan','decomposition']}}}

    def create(self,identifier='T1',sprint=None,worktree=True):
        born=self.work('task',{'action':'create','request_id':'create-'+identifier,'task_id':identifier,'sprint_id':sprint})
        edited=self.work('task',{'action':'edit','request_id':'edit-'+identifier,'task_id':identifier,
            'expected_revision':born['revision'],'patch':self.draft(identifier,sprint,worktree),'remove':[]})
        return self.work('task',{'action':'ready','request_id':'ready-'+identifier,'task_id':identifier,
            'expected_revision':edited['revision']})

    def produce(self,context,identifier='T1',value='ACCEPTED\n'):
        tree=Path(context['worktree'])
        assert tree.resolve().is_relative_to(self.root.resolve()),tree
        (tree/'src'/f'{identifier}.txt').write_text(value)
        return tree

    def verify(self,context,expected=(0,)):
        result=deepcopy(context['result_template'])
        result['sections']={k:'Fixture output verified through public execution.' for k in result['sections']}
        result['commit_message']='Deliver verified acceptance fixture'
        return self.work('verify',{'result':result,'artifacts':[]},expected=expected)

    def finish(self,identifier='T1',context=None):
        context=context or self.bootstrap(identifier)
        self.produce(context,identifier)
        checked=self.verify(context);assert checked['status']=='verified',checked
        accepted=self.work('accept',{});assert accepted['status']=='completed',accepted
        source=accepted.get('commit') or checked.get('commit')
        assert source, (accepted,checked)
        intent={'request_id':'integrate-'+identifier,'task_id':identifier,
            'expected_source_commit':source,'expected_target_commit':self.git('rev-parse','HEAD'),
            'authorization':'The user authorized acceptance fixtures and their local integration.','resolutions':[]}
        integrated=self.work('integrate',intent)
        assert integrated['status']=='integrated',integrated
        assert (self.repo/'src'/f'{identifier}.txt').read_text()=='ACCEPTED\n'
        self.git('merge-base','--is-ancestor',source,'HEAD')
        assert not Path(context['worktree']).exists()
        replay=self.work('integrate',intent);assert replay['status']=='integrated' and replay.get('replayed') is True
        return integrated
