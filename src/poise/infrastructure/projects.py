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
from ..common import descendant,configured_root,exact_keys,digest,load_config,file_digest
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


def publish_registry(settings,document):
    content=(json.dumps(document,ensure_ascii=False,indent=settings.raw['json_indent'],allow_nan=False)+'\n').encode()
    atomic_write(settings.registry,content,settings.raw['file_mode'])


class ProjectSettings:
    def __init__(self,path):
        self.path=Path(path).resolve();self.raw=read_document(self.path);c=self.raw
        exact_keys(c,{'schema','root','templates','manifest','receipt','registry','lock','file_mode','directory_mode',
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
        for key in ('registry','lock'):
            if not isinstance(c[key],str) or not c[key].strip():raise PoiseError(f'Explicit {key} path required')
        self.registry=confined(self.root,c['registry'])
        self.lock=confined(self.root,c['lock'])
        if self.registry==self.lock:raise PoiseError('Project registry and setup lock must be distinct')
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
        remote='not_required'
        if cfg['git']['push_required']:
            git('remote','get-url','--',cfg['git']['remote']);remote='configured_not_contacted'
        return {'repository':'verified','base_revision':commit,'remote':remote}

    def _registry(self):
        document=read_document(self.settings.registry)
        exact_keys(document,{'schema','projects'},'configured project registry')
        if document['schema']!='configured-project-registry-1':
            raise PoiseError('Unsupported configured project registry schema')
        projects=document['projects']
        if not isinstance(projects,dict):raise PoiseError('Configured project registry projects must be an object')
        for project,entry in projects.items():
            if not isinstance(project,str) or not project.strip():
                raise PoiseError('Configured project registry requires non-empty project identities')
            exact_keys(entry,{'config_path'},f'configured project registry entry {project}')
            if not isinstance(entry['config_path'],str) or not entry['config_path'].strip():
                raise PoiseError(f'Configured project registry entry {project} requires config_path')
            confined(self.settings.root,entry['config_path'])
        return document

    def _relative_config_path(self,target):
        return str((target/self.settings.raw['manifest']).resolve().relative_to(self.settings.root))

    def _register(self,document,project,target):
        relative=self._relative_config_path(target)
        existing=document['projects'].get(project)
        if existing is not None:
            if existing['config_path']!=relative:
                raise VersionConflict(f'Project identity {project} already registered to another configuration')
            return False
        updated={'schema':'configured-project-registry-1','projects':{
            **document['projects'],project:{'config_path':relative}}}
        publish_registry(self.settings,updated)
        return True

    def list(self):
        s=self.settings
        document=self._registry()
        projects=[];errors=[]
        for project in sorted(document['projects']):
            path=confined(s.root,document['projects'][project]['config_path'])
            if not path.is_file():
                errors.append({'project':project,'config_path':str(path),'status':'missing',
                    'reason':'Configured project manifest does not exist'})
                continue
            try:
                _,cfg,_=load_config(path)
                if cfg['project']!=project:
                    raise PoiseError(f'Manifest project identity is {cfg["project"]!r}, expected {project!r}')
            except PoiseError as exc:
                errors.append({'project':project,'config_path':str(path),'status':'invalid','reason':str(exc)})
                continue
            projects.append({'project':project,'config_path':str(path)})
        return {'status':'listed_with_errors' if errors else 'listed','projects':projects,'errors':errors}

    def apply(self,request):
        s=self.settings;c=s.raw;target=confined(s.root,request['destination'])
        lock=s.lock
        if lock.is_relative_to(target):raise PoiseError('Setup lock cannot be inside project destination')
        if s.registry.is_relative_to(target):raise PoiseError('Project registry cannot be inside project destination')
        request_digest=digest(request)
        minimal={'status':'created','project':'','revision':'0'*64,'config_path':str(target/c['manifest']),
                 'process_count':0,'readiness':{'repository':'not_checked','remote':'not_checked'},'replayed':False}
        if len(json.dumps(minimal,ensure_ascii=False)+'\n')>c['output_chars']:
            raise PoiseError('output_chars cannot hold project receipt; no publication attempted')
        try:
            with exclusive_lock(lock,c['lock_seconds'],c['lock_poll_seconds']):
                registry=self._registry()
                if target.exists():
                    receipt_path=confined(target,c['receipt'])
                    if not receipt_path.is_file():raise VersionConflict('Destination exists without this setup receipt')
                    saved=read_document(receipt_path)
                    if saved['request_digest']!=request_digest:raise VersionConflict('Different request for existing project')
                    for rel,expected in saved['files'].items():
                        file=confined(target,rel)
                        if not file.is_file() or file_digest(file)!=expected:raise VersionConflict('Published project changed; replay cannot overwrite it')
                    self._register(registry,saved['result']['project'],target)
                    return {**saved['result'],'replayed':True}
                blueprint=self.template(request['template']);cfg=blueprint.build(request['edits'],c['max_edits'])
                existing=registry['projects'].get(cfg['project'])
                relative=self._relative_config_path(target)
                if existing is not None and existing['config_path']!=relative:
                    raise VersionConflict(f'Project identity {cfg["project"]} already registered to another configuration')
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
                self._register(registry,cfg['project'],target)
                return result
        except OSError as exc:
            raise PoiseError(f'Project storage operation failed; retry same request to inspect publication: {exc}') from exc
