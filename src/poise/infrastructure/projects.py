"""Project creation with explicit configuration and workflow destinations.

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
from copy import deepcopy
from ..common import (
    descendant,
    configured_root,
    digest,
    load_config,
    load_config_document,
    validate_candidate_config,
    prohibit_remote_git_publication,
)
from ..modules.projects.domain import ProjectBlueprint
from ..modules.foundation.errors import PoiseError,VersionConflict
from ..modules.foundation.validation import configuration_fields, nonempty
from .goal_config import read_document,atomic_write
from .locking import exclusive_lock
from .workflow_files import workflow_path


def valid_project_probe_readiness(value):
    """Validate saved native probe data without repeating the Git observation."""
    if not isinstance(value, dict):
        return False
    if set(value) == {"repository", "remote"}:
        return value["repository"] == "not_checked" and value["remote"] == "not_checked"
    if set(value) == {"repository", "remote", "base_revision"}:
        return (
            value["repository"] == "verified"
            and value["remote"] == "not_required"
            and isinstance(value["base_revision"], str)
            and bool(value["base_revision"])
        )
    return False


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
        configuration_fields(c,{'schema','root','templates','manifest','registry','lock','file_mode','directory_mode',
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
        for k in ('manifest',):
            if not isinstance(c[k],str) or Path(c[k]).name!=c[k] or c[k] in ('','.','..'):
                raise PoiseError(f'{k} must be a single filename')
        for key in ('registry','lock'):
            if not isinstance(c[key],str) or not c[key].strip():raise PoiseError(f'Explicit {key} path required')
        self.registry=confined(self.root,c['registry'])
        self.lock=confined(self.root,c['lock'])
        if self.registry==self.lock:raise PoiseError('Project registry and setup lock must be distinct')
        codes = configuration_fields(c['exit_codes'],{'success','rejected','aborted'},'setup exit codes')
        if any(type(v) is not int or not 0<=v<=255 for v in codes.values()) or len(set(codes.values()))!=3:
            raise PoiseError('Distinct explicit exit codes required')
        if not isinstance(c['templates'],dict) or not c['templates']:raise PoiseError('Explicit template registry required')
        for item in c['templates'].values():
            item = configuration_fields(item,{'path'},'template registry')
            if not isinstance(item['path'],str) or not item['path']:raise PoiseError('Incomplete registry entry')


class FileProjectSetup:
    def __init__(self,settings):self.settings=settings

    def template(self,selection):
        settings=self.settings;c=settings.raw
        if selection['id'] not in c['templates']:raise PoiseError('Unknown selected project template')
        entry=c['templates'][selection['id']]
        raw=read_document((settings.root/entry['path']).resolve())
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
            if r.returncode:raise PoiseError(f'Local Git probe {args[0]} failed: {r.stderr}')
            return r.stdout.strip()
        top=git('rev-parse','--show-toplevel')
        if Path(top).resolve()!=repository.resolve():raise PoiseError('Configured repository is not its worktree root')
        commit=git('rev-parse','--verify','--end-of-options',cfg['git']['base_ref']+'^{commit}')
        git('check-ref-format','--branch',cfg['git']['branch_template'].format(task_id='probe',session_id='probe'))
        return {'repository':'verified','base_revision':commit,'remote':'not_required'}

    def _registry(self):
        document=read_document(self.settings.registry)
        configuration_fields(document,{'schema','projects'},'configured project registry')
        if document['schema']!='configured-project-registry-1':
            raise PoiseError('Unsupported configured project registry schema')
        projects=document['projects']
        if not isinstance(projects,dict):raise PoiseError('Configured project registry projects must be an object')
        for project,entry in projects.items():
            if not isinstance(project,str) or not project.strip():
                raise PoiseError('Configured project registry requires non-empty project identities')
            configuration_fields(entry,{'config_path'},f'configured project registry entry {project}')
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
            if confined(self.settings.root, existing['config_path']) != (target / self.settings.raw['manifest']).resolve():
                raise VersionConflict(f'Project identity {project} already registered to another configuration')
            return False
        updated=deepcopy(document)
        updated['projects'][project]={'config_path':relative}
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

    def _documents(self, blueprint, config, paths, *, resume, selection=None):
        documents = {}
        for goal, path in paths.items():
            if resume and path.is_file():
                documents[goal] = read_document(path)
            else:
                if blueprint is None:
                    blueprint = self.template(selection)
                sources = blueprint.data['workflow_sources']
                if goal not in sources:
                    raise PoiseError(f'No source snapshot for {goal}')
                source = (self.settings.root / sources[goal]['path']).resolve()
                documents[goal] = read_document(source)
        return documents

    def _destinations(self, target, config):
        manifest = target / self.settings.raw['manifest']
        marker = target / '.project-setup-pending.json'
        if not isinstance(config['workflows'], dict) or not config['workflows']:
            raise PoiseError('Explicit selected workflows required')
        paths = {goal: workflow_path(manifest, value)
                 for goal, value in config['workflows'].items()}
        state = configured_root(target, config['paths']['state'])
        if state == manifest or state.is_relative_to(manifest):
            raise PoiseError('Mutable state overlaps project manifest')
        reserved = [manifest, marker, self.settings.path, self.settings.registry,
                    self.settings.lock]
        for key in ('database', 'lock', 'requirements_database', 'requirements_lock'):
            value = Path(config['paths'][key])
            reserved.append(value if value.is_absolute() else state / value)
        for key in ('database', 'lock'):
            value = Path(config['accounting']['storage'][key])
            reserved.append(value if value.is_absolute() else state / value)
        selected = [path.resolve() for path in paths.values()]
        reserved = [path.resolve() for path in reserved]
        if any(path == state.resolve() for path in selected):
            raise PoiseError('Workflow path overlaps mutable state directory')
        directories = [configured_root(state, config['paths'][key])
                       for key in ('runtime', 'standalone_tasks', 'sprints', 'worktrees')]
        if any(directory == path or directory.is_relative_to(path)
               for path in selected for directory in directories):
            raise PoiseError('Workflow path overlaps a configured runtime directory')
        if any(a == b or a.is_relative_to(b) or b.is_relative_to(a)
               for i, a in enumerate(selected) for b in selected[i + 1:] + reserved):
            raise PoiseError('Project manifest, reserved storage and workflow paths overlap')
        return paths, marker

    def _result(self, target, config, documents, request, replayed):
        result = {'status': 'created', 'project': config['project'],
                  'revision': digest({'config': config, 'workflows': documents}),
                  'config_path': str(target / self.settings.raw['manifest']),
                  'workflow_count': len(documents),
                  'readiness': self._probe(config, request['probe_repository']),
                  'replayed': replayed}
        if len(json.dumps(result, ensure_ascii=False) + '\n') > self.settings.raw['output_chars']:
            raise PoiseError('output_chars cannot hold project result')
        return result

    def apply(self, request):
        settings = self.settings
        c = settings.raw
        target = confined(settings.root, request['destination'])
        if settings.lock.is_relative_to(target) or settings.registry.is_relative_to(target):
            raise PoiseError('Setup lock and registry cannot be inside project destination')
        created_files = []
        created_config = False
        complete = False
        marker = target / '.project-setup-pending.json'
        try:
            with exclusive_lock(settings.lock, c['lock_seconds'], c['lock_poll_seconds']):
                registry = self._registry()
                if target.exists():
                    manifest = target / c['manifest']
                    config = read_document(manifest)
                    configuration_fields(config, {'project'}, 'project config')
                    nonempty(config['project'], 'project')
                    identities = [edit['value'] for edit in request['edits']
                                  if edit.get('path') == ['project']]
                    if identities and identities != [config['project']]:
                        raise VersionConflict('Existing project identity differs from requested project')
                    binding = registry['projects'].get(config['project'])
                    registered = (binding is not None
                                  and confined(settings.root, binding['config_path']) == manifest.resolve())
                    if registered:
                        _, config, documents = load_config(manifest)
                        result = self._result(target, config, documents, request, True)
                        marker.unlink(missing_ok=True)
                        return result
                    if not marker.is_file():
                        raise VersionConflict('Existing project is not registered and has no pending setup')
                    configuration_fields(config, {'workflows'}, 'project config')
                    if not isinstance(config['workflows'], dict) or not config['workflows']:
                        raise PoiseError('Explicit selected workflows required')
                    for goal, value in config['workflows'].items():
                        if not isinstance(value, str) or not value or '\0' in value:
                            raise PoiseError(f'workflows.{goal}: explicit string path without NUL required')
                    selected_paths = {goal: workflow_path(manifest, value)
                                      for goal, value in config['workflows'].items()}
                    documents = self._documents(None, config, selected_paths, resume=True,
                                                selection=request['template'])
                    load_config_document(manifest, config, workflow_documents=documents)
                    paths, marker = self._destinations(target, config)
                    intent = read_document(marker)
                    expected = {'schema': 'project-setup-pending-1',
                                'request_id': request['request_id'], 'project': config['project'],
                                'workflows': {goal: str(path) for goal, path in paths.items()}}
                    if intent != expected:
                        raise VersionConflict('Pending setup identifies another operation or destination set')
                    validate_candidate_config(manifest, target, workflow_documents=documents)
                    result = self._result(target, config, documents, request, True)
                else:
                    blueprint = self.template(request['template'])
                    config = blueprint.build(request['edits'], c['max_edits'])
                    if not isinstance(config.get('workflows'), dict) or not config['workflows']:
                        raise PoiseError('Explicit selected workflows required')
                    documents = self._documents(blueprint, config,
                        {goal: None for goal in config['workflows']}, resume=False)
                    load_config_document(target / c['manifest'], config, workflow_documents=documents)
                    existing = registry['projects'].get(config['project'])
                    if existing is not None:
                        raise VersionConflict('Project identity already registered to another configuration')
                    paths, marker = self._destinations(target, config)
                    for path in paths.values():
                        if path.exists():
                            raise VersionConflict(f'Workflow destination already exists: {path}')
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with tempfile.TemporaryDirectory(prefix='.project-setup-', dir=target.parent) as temporary:
                        candidate = Path(temporary) / 'candidate'
                        candidate.mkdir(mode=c['directory_mode'])
                        def write(path, value):
                            atomic_write(path, (json.dumps(value, ensure_ascii=False,
                                indent=c['json_indent'], allow_nan=False) + '\n').encode(), c['file_mode'])
                        write(candidate / c['manifest'], config)
                        validate_candidate_config(candidate / c['manifest'], target,
                                                  workflow_documents=documents)
                        result = self._result(target, config, documents, request, False)
                        write(candidate / marker.name, {'schema': 'project-setup-pending-1',
                            'request_id': request['request_id'], 'project': config['project'],
                            'workflows': {goal: str(path) for goal, path in paths.items()}})
                        publish_directory(candidate, target)
                        created_config = True
                try:
                    for goal, path in paths.items():
                        if not created_config and path.is_file():
                            continue  # Reserved by this explicit pending operation; preserve operator edits.
                        path.parent.mkdir(parents=True, exist_ok=True)
                        with path.open('xb') as stream:
                            created_files.append(path)
                            os.fchmod(stream.fileno(), c['file_mode'])
                            stream.write((json.dumps(documents[goal], ensure_ascii=False,
                                indent=c['json_indent'], allow_nan=False) + '\n').encode())
                            stream.flush()
                            os.fsync(stream.fileno())
                    load_config(target / c['manifest'])
                    complete = True
                    self._register(registry, config['project'], target)
                    marker.unlink()
                except BaseException:
                    if not complete:
                        for path in reversed(created_files):
                            path.unlink(missing_ok=True)
                        if created_config:
                            marker.unlink(missing_ok=True)
                            (target / c['manifest']).unlink(missing_ok=True)
                            try:
                                target.rmdir()
                            except OSError:
                                pass  # Preserve any unrelated files that appeared in the directory.
                    raise
                return result
        except OSError as exc:
            raise PoiseError(f'Project storage operation failed; retry same request to inspect publication: {exc}') from exc
