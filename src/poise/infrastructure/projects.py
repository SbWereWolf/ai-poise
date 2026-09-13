"""Create-only project bundles, atomically published within the Poise codebase.

Readiness probes are local Git reads. No target modifications, task DB, network,
trust changes or implicit project selection occur here.
"""
from __future__ import annotations
import json
import math
import os
from pathlib import Path
import subprocess
import tempfile
from ..common import (
    descendant,
    configured_root,
    exact_keys,
    digest,
    load_config,
    file_digest,
    prohibit_remote_git_publication,
)
from ..modules.projects.domain import ProjectBlueprint
from ..modules.foundation.errors import PoiseError,VersionConflict
from .goal_config import read_document,atomic_write
from .locking import exclusive_lock


def confined(root,relative):
    target=descendant(root,relative)
    lexical=root/relative
    for parent in (lexical,*lexical.parents):
        if parent==root:break
        if parent.is_symlink():raise PoiseError(f'Symlink in managed project path: {relative}')
    return target


def publish_directory(source,target):
    if target.exists():raise VersionConflict('Project destination already exists')
    os.rename(source,target)
    fd=os.open(target.parent,os.O_RDONLY|os.O_DIRECTORY)
    try:os.fsync(fd)
    finally:os.close(fd)


class ProjectSettings:
    def __init__(self,path):
        self.path=Path(path).resolve();self.raw=read_document(self.path);c=self.raw
        exact_keys(c,{'schema','root','templates','manifest','receipt','lock','file_mode','directory_mode',
            'json_indent','lock_seconds','lock_poll_seconds','git_seconds','max_input_bytes','max_edits',
            'max_survey_steps','output_chars','exit_codes'},'project setup settings')
        if c['schema']!='project-setup-settings-1':raise PoiseError('Unsupported setup settings schema')
        if not isinstance(c['root'],str) or not c['root']:raise PoiseError('Explicit Poise root required')
        self.root=(self.path.parent/c['root']).resolve(strict=True)
        if not self.root.is_dir():raise PoiseError('Poise root is not a directory')
        for k in ('lock_seconds','lock_poll_seconds','git_seconds'):
            if type(c[k]) not in (int,float) or not math.isfinite(c[k]) or c[k]<=0:raise PoiseError(f'Explicit positive {k} required')
        for k in ('max_input_bytes','max_edits','max_survey_steps','output_chars'):
            if type(c[k]) is not int or c[k]<=0:raise PoiseError(f'Explicit positive integer {k} required')
        for k in ('file_mode','directory_mode'):
            if type(c[k]) is not int or not 0<=c[k]<=0o777:raise PoiseError(f'Invalid {k}')
        if type(c['json_indent']) is not int or c['json_indent']<0:raise PoiseError('Explicit json_indent required')
        for k in ('manifest','receipt'):
            if not isinstance(c[k],str) or Path(c[k]).name!=c[k] or c[k] in ('','.','..'):
                raise PoiseError(f'{k} must be a single filename')
        if c['manifest']==c['receipt']:raise PoiseError('Receipt and manifest must be distinct')
        confined(self.root,c['lock'])
        exact_keys(c['exit_codes'],{'success','rejected','aborted'},'setup exit codes')
        if any(type(v) is not int or not 0<=v<=255 for v in c['exit_codes'].values()) or len(set(c['exit_codes'].values()))!=3:
            raise PoiseError('Distinct explicit exit codes required')
        if not isinstance(c['templates'],dict) or not c['templates']:raise PoiseError('Explicit template registry required')
        for item in c['templates'].values():
            exact_keys(item,{'path','version','digest'},'template registry')
            if any(not isinstance(v,str) or not v for v in item.values()):raise PoiseError('Incomplete registry entry')
            confined(self.root,item['path'])


class FileProjectSetup:
    def __init__(self,settings):self.settings=settings

    def template(self,selection):
        settings=self.settings;c=settings.raw
        if selection['id'] not in c['templates']:raise PoiseError('Unknown selected project template')
        entry=c['templates'][selection['id']]
        if any(selection[k]!=entry[k] for k in ('version','digest')):
            raise VersionConflict('Project template selection is stale')
        raw=read_document(confined(settings.root,entry['path']))
        if digest(raw)!=entry['digest'] or raw['version']!=entry['version']:
            raise VersionConflict('Selected project template has changed')
        return ProjectBlueprint.parse(raw)

    def _probe(self,cfg,enabled):
        if cfg['git']['push_required']:
            prohibit_remote_git_publication()
        if not enabled:return {'repository':'not_checked','remote':'not_checked'}
        repository=Path(cfg['git']['repository'])
        if not repository.is_absolute() or not repository.is_dir():raise PoiseError('Git repository must be an existing absolute path')
        def git(*args):
            try:r=subprocess.run(['git','-C',str(repository),*args],capture_output=True,text=True,timeout=self.settings.raw['git_seconds'])
            except (OSError,subprocess.TimeoutExpired) as exc:raise PoiseError(f'Local Git probe failed: {exc}') from exc
            if r.returncode:raise PoiseError(f'Local Git probe {args[0]} failed: {r.stderr[-cfg["limits"]["preview_chars"]:]}')
            return r.stdout.strip()
        top=git('rev-parse','--show-toplevel')
        if Path(top).resolve()!=repository.resolve():raise PoiseError('Configured repository is not its worktree root')
        commit=git('rev-parse','--verify','--end-of-options',cfg['git']['base_ref']+'^{commit}')
        git('check-ref-format','--branch',cfg['git']['branch_template'].format(task_id='probe',session_id='probe'))
        return {'repository':'verified','base_revision':commit,'remote':'not_required'}

    def apply(self,request):
        s=self.settings;c=s.raw;target=confined(s.root,request['destination'])
        lock=confined(s.root,c['lock'])
        if lock.is_relative_to(target):raise PoiseError('Setup lock cannot be inside project destination')
        request_digest=digest(request)
        minimal={'status':'created','project':'','revision':'0'*64,'config_path':str(target/c['manifest']),
                 'process_count':0,'readiness':{'repository':'not_checked','remote':'not_checked'},'replayed':False}
        if len(json.dumps(minimal,ensure_ascii=False)+'\n')>c['output_chars']:
            raise PoiseError('output_chars cannot hold project receipt; no publication attempted')
        try:
            with exclusive_lock(lock,c['lock_seconds'],c['lock_poll_seconds']):
                if target.exists():
                    receipt_path=confined(target,c['receipt'])
                    if not receipt_path.is_file():raise VersionConflict('Destination exists without this setup receipt')
                    saved=read_document(receipt_path)
                    if saved['request_digest']!=request_digest:raise VersionConflict('Different request for existing project')
                    for rel,expected in saved['files'].items():
                        file=confined(target,rel)
                        if not file.is_file() or file_digest(file)!=expected:raise VersionConflict('Published project changed; replay cannot overwrite it')
                    return {**saved['result'],'replayed':True}
                blueprint=self.template(request['template']);cfg=blueprint.build(request['edits'],c['max_edits'])
                sources=blueprint.data['process_sources'];documents={}
                if not isinstance(cfg['processes'],dict) or not cfg['processes']:raise PoiseError('Explicit selected processes required')
                for goal,relative in cfg['processes'].items():
                    if goal not in sources:raise PoiseError(f'No source snapshot for {goal}')
                    source=sources[goal];raw=read_document(confined(s.root,source['path']))
                    if digest(raw)!=source['digest']:raise VersionConflict(f'Process source changed: {goal}')
                    documents[goal]=raw
                paths=[confined(target,rel) for rel in cfg['processes'].values()]+[target/c['manifest'],target/c['receipt']]
                if any(a==b or a.is_relative_to(b) or b.is_relative_to(a) for i,a in enumerate(paths) for b in paths[i+1:]):
                    raise PoiseError('Project manifest, receipt and process paths overlap')
                target.parent.mkdir(parents=True,exist_ok=True)
                with tempfile.TemporaryDirectory(prefix='.project-setup-',dir=target.parent) as temporary:
                    candidate=Path(temporary)/'candidate';candidate.mkdir(mode=c['directory_mode'])
                    def write(rel,value):
                        p=confined(candidate,rel)
                        atomic_write(p,(json.dumps(value,ensure_ascii=False,indent=c['json_indent'],allow_nan=False)+'\n').encode(),c['file_mode'])
                    write(c['manifest'],cfg)
                    for goal,document in documents.items():write(cfg['processes'][goal],document)
                    # The runtime remains the authority on complete configuration.
                    load_config(candidate/c['manifest'])
                    state=configured_root(target,cfg['paths']['state'])
                    if any(state.is_relative_to(p) or p.is_relative_to(state) for p in paths):
                        raise PoiseError('Mutable state overlaps published project files')
                    readiness=self._probe(cfg,request['probe_repository'])
                    result={'status':'created','project':cfg['project'],'revision':digest({'config':cfg,'processes':documents}),
                            'config_path':str(target/c['manifest']),'process_count':len(documents),'readiness':readiness,'replayed':False}
                    if len(json.dumps(result,ensure_ascii=False)+'\n')>c['output_chars']:
                        raise PoiseError('output_chars cannot hold project result; no publication attempted')
                    files={str(p.relative_to(candidate)):file_digest(p) for p in candidate.rglob('*') if p.is_file()}
                    write(c['receipt'],{'schema':'project-receipt-1','request_id':request['request_id'],
                        'request_digest':request_digest,'template':request['template'],'files':files,'result':result})
                    publish_directory(candidate,target)
                return result
        except OSError as exc:
            raise PoiseError(f'Project storage operation failed; retry same request to inspect publication: {exc}') from exc
