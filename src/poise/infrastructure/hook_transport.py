"""Managed Codex hook installation and a session-scoped entry to WorkTools.

The adapter never edits Codex trust or starts task transitions from native events.
"""
from copy import deepcopy
import ast
import json
import os
from pathlib import Path
import shlex
import subprocess
from ..common import PoiseError,descendant,exact_keys,digest,file_digest,encoded,load_config
from ..modules.hook_transport.domain import HookDefinition,parse_native_event,merge_hook_document
from ..modules.capabilities.domain import positive,nonempty
from ..modules.runtime_adapter.domain import RuntimeIdentity
from ..application.capabilities import CapabilityChecks
from ..application.hook_transport import HookCommands
from ..application.work import WorkTools
from ..runtime import Poise
from .capabilities import LocalProbeExecutor
from .goal_config import read_document,atomic_write,strict_json
from .sqlite.hook_transport import HookRegistry


BOUND_SOURCE_ENV='POISE_NATIVE_BOUND_SOURCE'
BOUND_SOURCE_FIELDS={'task_id','worktree','branch','source_root','package_root','git_common','binding_digest'}


class HookSettings:
    def __init__(self,path):
        self.path=Path(path).resolve(strict=True);self.raw=read_document(self.path)
        self._validate()

    @classmethod
    def proposed(cls,path,document):
        obj=cls.__new__(cls);obj.path=Path(path).resolve();obj.raw=deepcopy(document)
        obj._validate()
        return obj

    def _validate(self):
        s=self.raw
        exact_keys(s,{'schema','root','project_config','python','source_root','hooks_file','definitions',
            'state','lock','database','bindings','launcher','binding_file','observations','response_file',
            'cwd_roots','file_mode','executable_mode','lock_seconds','lock_poll_seconds','max_input_bytes',
            'output_chars','max_probes','exit_codes','probe_files'},'hook settings')
        if s['schema']!='hook-settings-1':raise PoiseError('Unsupported hook settings schema')
        self.root=(self.path.parent/s['root']).resolve(strict=True)
        self.project_config=descendant(self.root,s['project_config'])
        self.hooks_file=descendant(self.root,s['hooks_file'])
        self.definitions=descendant(self.root,s['definitions'])
        self.state=descendant(self.root,s['state']);self.lock=descendant(self.root,s['lock'])
        self.database=descendant(self.state,s['database'])
        self.bindings=descendant(self.state,s['bindings'])
        self.observations=descendant(self.state,s['observations'])
        exact_keys(s['probe_files'],{'stdout','stderr','receipt'},'probe_files')
        if len(set(s['probe_files'].values()))!=3:raise PoiseError('Probe files must be distinct')
        for name in s['probe_files'].values():descendant(self.state,name)
        for key in ('launcher','binding_file','response_file'):descendant(self.state,s[key])
        if s['launcher']==s['binding_file']:raise PoiseError('Binding and launcher filenames must differ')
        if self.state.is_relative_to(self.definitions) or self.definitions.is_relative_to(self.state):
            raise PoiseError('Definitions and operational state must have distinct roots')
        if self.hooks_file.is_relative_to(self.state):raise PoiseError('Native hooks must not live in ephemeral operational state')
        for key in ('python','source_root'):
            nonempty(s[key],key)
            if not Path(s[key]).is_absolute():raise PoiseError(f'{key} must be an explicit absolute path')
        if not Path(s['python']).is_file() or not os.access(s['python'],os.X_OK):raise PoiseError('Configured interpreter is not executable')
        if not (Path(s['source_root'])/'poise/__main__.py').is_file():raise PoiseError('Configured Poise source root missing')
        for key in ('max_input_bytes','output_chars','max_probes'):positive(s[key],key,True)
        for key in ('lock_seconds','lock_poll_seconds'):positive(s[key],key)
        for key in ('file_mode','executable_mode'):
            if type(s[key]) is not int or not 0<=s[key]<=0o777:raise PoiseError(f'{key}: explicit POSIX mode required')
        if not s['executable_mode'] & 0o100:raise PoiseError('Launcher mode must allow owner execution')
        if not isinstance(s['cwd_roots'],list) or not s['cwd_roots']:raise PoiseError('Explicit cwd roots required')
        for p in s['cwd_roots']:
            if not isinstance(p,str) or not Path(p).is_absolute():raise PoiseError('cwd_roots require absolute paths')
        exact_keys(s['exit_codes'],{'success','incomplete','rejected'},'exit_codes')
        if len(set(s['exit_codes'].values()))!=3 or any(type(v) is not int or not 0<=v<=255 for v in s['exit_codes'].values()):
            raise PoiseError('Explicit distinct CLI exit codes required')
        minimal=encoded({'status':'capabilities_unavailable','response_path':str(self.observations/('0'*32)/s['response_file'])})+'\n'
        if len(minimal)>s['output_chars']:raise PoiseError('Output budget cannot fit the configured receipt address')

    def command(self,verb,*args):
        return shlex.join(['env','PYTHONPATH='+self.raw['source_root'],self.raw['python'],'-B','-m','poise',verb,'--settings',str(self.path),*args])


class FileHookRepository:
    def __init__(self,settings,registry):self.settings,self.registry=settings,registry

    def revision(self):
        p=self.settings.hooks_file
        return file_digest(p) if p.exists() else None

    def _groups(self,definition,path):
        command=self.settings.command('hook','--definition',str(path))
        groups=[]
        for e in definition.data['events']:
            handler={'type':'command','command':command,'timeout':e['timeout_seconds'],
                     'async':e['async'],'statusMessage':e['status_message']}
            if e['context_limit'] is not None:handler['additionalContextLimit']=e['context_limit']
            group={'hooks':[handler]}
            if e['event'] not in ('UserPromptSubmit','Stop'):group['matcher']=e['matcher']
            groups.append([e['event'],group])
        return groups

    def install(self,request_id,expected_revision,definition):
        s=self.settings
        if len(definition.data['probes'])>s.raw['max_probes']:raise PoiseError('Too many probes')
        _,project,_=load_config(s.project_config)
        if project['batch']['message_source']!={'id':definition.data['message_source'],'mode':'runtime_event'}:
            raise PoiseError('Project message source must explicitly match the hook event source')
        packet=digest([request_id,expected_revision,definition.data,str(s.hooks_file)])
        # External file effects are protected by the same bounded installer lock.
        # Pending intent is committed before replacement, so a retry can reconcile it.
        with self.registry.transaction() as db:
            row=db.execute('SELECT data FROM operations WHERE id=?',(request_id,)).fetchone()
            if row is not None:
                operation=json.loads(row[0])
                if operation['digest']!=packet:raise PoiseError('Request id reused with conflicting hook definition/destination')
            else:
                before=self.revision()
                if before!=expected_revision:raise PoiseError(f'Hook configuration revision changed: {before}')
                old=db.execute('SELECT data FROM installations WHERE id=?',(definition.data['id'],)).fetchone()
                previous=[] if old is None else json.loads(old[0])['groups']
                document=None if before is None else read_document(s.hooks_file)
                definition_path=descendant(s.definitions,digest(definition.data)+'.json')
                desired=self._groups(definition,definition_path)
                candidate=merge_hook_document(document,previous,desired)
                body=(json.dumps(candidate,ensure_ascii=False,indent=2,sort_keys=True)+'\n').encode()
                import hashlib
                revision=hashlib.sha256(body).hexdigest()
                receipt={'status':'installed','request_id':request_id,'revision':revision,
                    'definition_path':str(definition_path),'hooks_path':str(s.hooks_file),
                    'events':[e['event'] for e in definition.data['events']],
                    'trust_status':'requires_user_review','live_codex':'not_observed'}
                operation={'digest':packet,'phase':'pending','before':before,'candidate':candidate,
                    'groups':desired,'definition':definition.data,'receipt':receipt}
                db.execute('INSERT INTO operations VALUES(?,?)',(request_id,encoded(operation)))
                self.registry.event_in(db,None,'install.pending',{'request_id':request_id})
        with self.registry.transaction() as db:
            # Reload after acquiring the lock: concurrent retries see the same receipt.
            operation=json.loads(db.execute('SELECT data FROM operations WHERE id=?',(request_id,)).fetchone()[0])
            receipt=operation['receipt'];now=self.revision()
            if operation['phase']=='completed':
                if now!=receipt['revision']:raise PoiseError('Installed hooks changed since the saved receipt')
                return receipt
            if now not in (operation['before'],receipt['revision']):raise PoiseError('Pending hook installation conflicts with current file')
            path=Path(receipt['definition_path']);body=(json.dumps(operation['definition'],ensure_ascii=False,indent=2)+'\n').encode()
            if path.exists() and path.read_bytes()!=body:raise PoiseError('Immutable hook definition was changed')
            if not path.exists():atomic_write(path,body,s.raw['file_mode'])
            if now!=receipt['revision']:
                atomic_write(s.hooks_file,(json.dumps(operation['candidate'],ensure_ascii=False,indent=2,sort_keys=True)+'\n').encode(),s.raw['file_mode'])
            operation['phase']='completed'
            db.execute('UPDATE operations SET data=? WHERE id=?',(encoded(operation),request_id))
            db.execute('INSERT INTO installations VALUES(?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data',
                (operation['definition']['id'],encoded({'groups':operation['groups'],'receipt':receipt})))
            self.registry.event_in(db,None,'install.completed',{'request_id':request_id,'revision':receipt['revision']})
            return receipt


class HookService:
    """Composition boundary. Native events observe/bind; WorkTools alone changes work."""
    def __init__(self,settings_path):
        self.settings=HookSettings(settings_path);self.registry=HookRegistry(self.settings)
        self.repository=FileHookRepository(self.settings,self.registry)
        self.commands=HookCommands(self.repository)

    def revision(self):return self.repository.revision()
    def install(self,packet):return self.commands.install(packet)

    def definition(self,path):
        p=Path(path)
        if not p.is_absolute() or p.is_symlink() or not p.resolve().is_relative_to(self.settings.definitions):
            raise PoiseError('Hook definition must be within configured definition root')
        value=read_document(p)
        if p.stem!=digest(value):raise PoiseError('Hook definition digest mismatch')
        return HookDefinition.parse(value)

    def _bind(self,definition_path,event):
        definition=self.definition(definition_path)
        native=parse_native_event(event,definition)
        cwd=Path(native['cwd'])
        if not cwd.is_absolute() or not any(cwd.resolve().is_relative_to(Path(r).resolve()) for r in self.settings.raw['cwd_roots']):
            raise PoiseError('Native cwd outside configured roots')
        _,cfg,_=load_config(self.settings.project_config)
        d=definition.data
        if cfg['batch']['message_source']!={'id':d['message_source'],'mode':'runtime_event'}:
            raise PoiseError('Do not combine hook and transcript/reported message sources')
        identity=RuntimeIdentity.parse(cfg['project'],d['message_source'],
            {'kind':'external','session_id':native['session_id'],'agent_id':d['agent_id']})
        root=descendant(self.settings.bindings,identity.key)
        launcher=descendant(root,self.settings.raw['launcher']);binding=descendant(root,self.settings.raw['binding_file'])
        record={'session_id':identity.key,'external_session':native['session_id'],'agent_id':d['agent_id'],
            'project':cfg['project'],'settings':str(self.settings.path),'settings_digest':digest(self.settings.raw),
            'definition_path':str(Path(definition_path).resolve()),'launcher':str(launcher),'binding_path':str(binding)}
        self.registry.bind(record)
        # Both files are deterministic for the binding; no task root or global current file.
        raw=(json.dumps(record,ensure_ascii=False,indent=2)+'\n').encode()
        if binding.exists() and binding.read_bytes()!=raw:raise PoiseError('Binding file was changed externally')
        if not binding.exists():atomic_write(binding,raw,self.settings.raw['file_mode'])
        script=('#!/bin/sh\nexec '+self.settings.command('hook-work','--binding',str(binding))+'\n').encode()
        if launcher.exists() and launcher.read_bytes()!=script:raise PoiseError('Launcher was changed externally')
        if not launcher.exists():atomic_write(launcher,script,self.settings.raw['executable_mode'])
        return definition,native,record

    def _record(self,path):
        p=Path(path)
        if not p.is_absolute() or p.is_symlink() or not p.resolve().is_relative_to(self.settings.bindings):
            raise PoiseError('Binding outside configured root')
        raw=read_document(p)
        exact_keys(raw,{'session_id','external_session','agent_id','project','settings','settings_digest',
            'definition_path','launcher','binding_path'},'hook binding')
        stored,message=self.registry.get(raw['session_id'])
        if raw!=stored or stored['settings_digest']!=digest(self.settings.raw):raise PoiseError('Stale or changed hook binding')
        self.definition(stored['definition_path'])
        return stored,message

    def bound_runtime(self,binding_path):
        from .clock import SystemClock
        record,_=self._record(binding_path)
        return Poise(self.settings.project_config,record['session_id'],SystemClock())

    def latest_binding(self,external,agent):return self.registry.find(external,agent)

    def event(self,definition_path,event):
        from .clock import SystemClock
        definition,native,binding=self._bind(definition_path,event)
        h=Poise(self.settings.project_config,binding['session_id'],SystemClock());task=h.current_task()
        active=task if task is not None and task['status'] not in ('completed','cancelled','superseded') else None
        message=None
        if native['event']=='UserPromptSubmit':
            message={'conversation_id':native['session_id'],'message_id':native['turn_id'],
                     'occurred_at':None,'reason':None,'subject':None}
            h.interactions.record(h.interactions.prepare([message]),h.session,active)
        self.registry.record_event(h.session,native['event'],native['turn_id'],message)
        state='idle' if task is None else f"{task['id']} / {task['status']} / {task['process']['stages'][task['stage_index']]['id']}"
        if native['event'] in ('SessionStart','UserPromptSubmit'):
            context=definition.data['context_template'].format(launcher=binding['launcher'],state=state)
            return {'hookSpecificOutput':{'hookEventName':native['event'],'additionalContext':context}}
        if native['event']=='Stop':return {'systemMessage':definition.data['stop_template'].format(state=state)}
        return {}  # SessionEnd is advisory; no handoff/accept/cancel is attempted.

    def probes(self,definition_path,workspace):
        d=self.definition(definition_path)
        return CapabilityChecks(LocalProbeExecutor(self.settings.observations,self.settings.raw['file_mode'],self.settings.raw['probe_files']),
            self.settings.raw['max_probes']).run(d.data['probes'],workspace)

    @staticmethod
    def _plain_path(path,label):
        if not path.is_absolute() or '..' in path.parts:
            raise PoiseError(f'{label} path must be explicit and absolute')
        if path.is_symlink():raise PoiseError(f'{label} must not be a symlink')
        try:resolved=path.resolve(strict=True)
        except OSError as exc:raise PoiseError(f'{label} is missing: {path}') from exc
        if resolved!=path:raise PoiseError(f'{label} has a symlinked path component')
        return resolved

    def _source_suffix(self,h):
        repository=self._plain_path(Path(h.cfg['git']['repository']),'Configured Git repository')
        source=self._plain_path(Path(self.settings.raw['source_root']),'Configured Poise source')
        try:suffix=source.relative_to(repository)
        except ValueError:return None
        return repository,suffix

    @staticmethod
    def _git_path(h,cwd,*args):
        value=Path(h._git(cwd,*args))
        if not value.is_absolute():value=cwd/value
        return value.resolve(strict=True)

    def _task_source_facts(self,h,task,binding_path):
        if not isinstance(task,dict) or not isinstance(task.get('id'),str):
            raise PoiseError('Task source requires a registered Task record')
        if not isinstance(task.get('worktree'),str) or not task['worktree']:
            raise PoiseError(f"Task {task['id']} worktree is missing from managed state")
        if not isinstance(task.get('branch'),str) or not task['branch']:
            raise PoiseError(f"Task {task['id']} branch is missing from managed state")
        worktree=self._plain_path(Path(task['worktree']),f"Task {task['id']} worktree")
        topology=self._source_suffix(h)
        if topology is None:raise PoiseError('Bound Task source has no configured repository-relative source')
        repository,suffix=topology
        source=worktree/suffix
        current=worktree
        for part in suffix.parts:
            current=current/part
            if current.is_symlink():raise PoiseError(f"Task {task['id']} source must not contain a symlink")
        source=self._plain_path(source,f"Task {task['id']} Poise source")
        package=source/'poise'
        if package.is_symlink():raise PoiseError(f"Task {task['id']} Poise package must not be a symlink")
        package=self._plain_path(package,f"Task {task['id']} Poise package")
        entry=package/'__main__.py'
        transport=package/'infrastructure'/'hook_transport.py'
        for path,label in ((entry,'Poise source package entrypoint'),(transport,'Poise source HookService entrypoint')):
            if path.is_symlink():raise PoiseError(f"Task {task['id']} {label} must not be a symlink")
            if not path.is_file():raise PoiseError(f"Task {task['id']} {label} is missing")
        try:tree=ast.parse(transport.read_text(encoding='utf-8'))
        except (OSError,UnicodeError,SyntaxError) as exc:
            raise PoiseError(f"Task {task['id']} HookService entrypoint is structurally invalid") from exc
        service=next((node for node in tree.body if isinstance(node,ast.ClassDef) and node.name=='HookService'),None)
        has_work=service is not None and any(
            isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef)) and node.name=='work'
            for node in service.body
        )
        if not has_work:raise PoiseError(f"Task {task['id']} HookService.work entrypoint is missing")
        top=self._git_path(h,worktree,'rev-parse','--path-format=absolute','--show-toplevel')
        if top!=worktree:raise PoiseError(f"Task {task['id']} Git root does not match its registered worktree")
        branch=h._git(worktree,'symbolic-ref','--short','HEAD')
        if branch!=task['branch']:raise PoiseError(f"Task {task['id']} Git branch does not match managed state")
        configured_common=self._git_path(h,repository,'rev-parse','--path-format=absolute','--git-common-dir')
        task_common=self._git_path(h,worktree,'rev-parse','--path-format=absolute','--git-common-dir')
        if task_common!=configured_common:
            raise PoiseError(f"Task {task['id']} Git repository does not match configured repository")
        binding=Path(binding_path)
        return {'task_id':task['id'],'worktree':str(worktree),'branch':branch,
                'source_root':str(source),'package_root':str(package),'git_common':str(task_common),
                'binding_digest':file_digest(binding)}

    def _bound_source_facts(self,binding_path):
        raw=os.environ.pop(BOUND_SOURCE_ENV,None)
        if raw is None:return None
        try:facts=strict_json(raw)
        except (PoiseError,UnicodeError) as exc:raise PoiseError('Bound Task source facts are invalid') from exc
        exact_keys(facts,BOUND_SOURCE_FIELDS,'bound Task source facts')
        if any(not isinstance(value,str) or not value for value in facts.values()):
            raise PoiseError('Bound Task source facts require non-empty strings')
        loaded=Path(__file__).resolve().parents[1]
        if loaded!=Path(facts['package_root']):raise PoiseError('Loaded Poise source differs from bound Task source')
        if file_digest(Path(binding_path))!=facts['binding_digest']:
            raise PoiseError('Native binding changed before bound Task execution')
        return facts

    def _child_result(self,h,completed):
        if not completed.stdout:
            reason=completed.stderr[-self.settings.raw['output_chars']:] or 'no JSON result'
            raise PoiseError(f'Bound Task source process failed: {reason}')
        try:view=strict_json(completed.stdout)
        except (PoiseError,UnicodeError) as exc:
            raise PoiseError('Bound Task source returned invalid JSON') from exc
        result=view
        if isinstance(view,dict) and 'response_path' in view:
            path=Path(view['response_path'])
            if (not path.is_absolute() or path.is_symlink()
                    or not path.resolve(strict=True).is_relative_to(h.state.resolve(strict=True))):
                raise PoiseError('Bound Task source response path is outside managed state')
            result=read_document(path)
        if not isinstance(result,dict) or not isinstance(result.get('status'),str):
            raise PoiseError('Bound Task source returned an invalid result')
        incomplete={'capabilities_unavailable','checks_failed','content_requirements_failed',
            'evidence_requirements_failed','observations_stale','action_failed','action_blocked'}
        expected=(self.settings.raw['exit_codes']['rejected'] if result['status']=='rejected'
                  else self.settings.raw['exit_codes']['incomplete'] if result['status'] in incomplete
                  else self.settings.raw['exit_codes']['success'])
        if completed.returncode!=expected:
            raise PoiseError('Bound Task source exit code does not match its result status')
        if result['status']=='rejected':raise PoiseError(result.get('reason','Bound Task source rejected work'))
        return result

    def _dispatch_bound_source(self,h,binding_path,packet,facts):
        environment={**os.environ,'PYTHONPATH':facts['source_root'],'PYTHONDONTWRITEBYTECODE':'1',
                     BOUND_SOURCE_ENV:json.dumps(facts,ensure_ascii=False,separators=(',',':'))}
        completed=subprocess.run(
            [self.settings.raw['python'],'-B','-m','poise','hook-work','--settings',str(self.settings.path),
             '--binding',str(binding_path)],
            input=encoded(packet)+'\n',text=True,capture_output=True,env=environment)
        return self._child_result(h,completed)

    def _execute_bound(self,h,record,req,definition,bound_facts=None,binding_path=None):
        def invoke():
            if bound_facts is not None:
                current=(h.task_queries.record(req['input']['task_id'])
                         if req['operation']=='integrate' else h.current_task())
                if current is None or current['id']!=bound_facts['task_id']:
                    raise PoiseError('Native binding no longer owns the bound Task source')
                if self._task_source_facts(h,current,binding_path)!=bound_facts:
                    raise PoiseError('Bound Task source facts changed before execution')
            return WorkTools(h).invoke(req)

        gated=req['operation'] in definition['gate_operations']
        result=None;checks=None
        # Bootstrap prepares a worktree before its project-bound capability probe.
        if req['operation']=='bootstrap':result=invoke()
        if gated:
            task=h.current_task()
            active_task=task is not None and task['status'] not in ('completed','cancelled','superseded')
            workspace=task['worktree'] if active_task else h.cfg['git']['repository']
            checks=self.probes(record['definition_path'],workspace)
            if not checks['ready'] and not (req['operation']=='bootstrap' and not active_task):
                h.interactions.record(h.interactions.prepare(req['messages']),h.session,
                    task if task is not None and task['status'] not in ('completed','cancelled','superseded') else None)
                return {'status':'capabilities_unavailable','capability_checks':checks,
                        'context':result if result is not None else h.show(),
                        'interaction':h.interactions.summary(task)}
        if result is None:result=invoke()
        return {**result,'capability_checks':checks,'hook_session':record['session_id']}

    def work(self,binding_path,packet):
        from .clock import SystemClock
        bound_facts=self._bound_source_facts(binding_path)
        record,message=self._record(binding_path)
        h=Poise(self.settings.project_config,record['session_id'],SystemClock());self.runtime=h
        from ..modules.work.domain import parse_request
        req=parse_request(packet,h.cfg['batch'])
        if req['messages']:raise PoiseError('Hooked work derives messages from UserPromptSubmit; do not supply a second source')
        req=deepcopy(req);req['messages']=[] if message is None else [message]
        d=self.definition(record['definition_path']).data
        if h.cfg['batch']['message_source']!={'id':d['message_source'],'mode':'runtime_event'}:
            raise PoiseError('Message source changed after hook binding')
        if bound_facts is not None:
            return self._execute_bound(h,record,req,d,bound_facts,binding_path)
        route,task=WorkTools(h).prepare_bound_source(req)
        if route.source!='installation':
            if self._source_suffix(h) is None:
                return self._execute_bound(h,record,req,d)
            facts=self._task_source_facts(h,task,binding_path)
            return self._dispatch_bound_source(h,binding_path,packet,facts)
        return self._execute_bound(h,record,req,d)


def setup_runtime(settings_path,packet):
    """Create explicit adapter settings and install hooks with one declarative packet.

    Existing settings are immutable for this setup command. A retry may resume
    installation, but cannot reconfigure an active session by replacing settings.
    """
    exact_keys(packet,{'settings','installation'},'runtime setup packet')
    value=packet['installation']
    exact_keys(value,{'request_id','expected_revision','definition'},'hook installation')
    nonempty(value['request_id'],'request_id')
    if value['expected_revision'] is not None:nonempty(value['expected_revision'],'expected_revision')
    definition=HookDefinition.parse(value['definition'])
    s=HookSettings.proposed(settings_path,packet['settings'])
    if not s.path.is_relative_to(s.root):raise PoiseError('Adapter settings must live in the Poise codebase')
    if s.path.is_relative_to(s.state):raise PoiseError('Adapter settings must not live in operational state')
    if len(definition.data['probes'])>s.raw['max_probes']:raise PoiseError('Too many probes')
    _,project,_=load_config(s.project_config)
    if project['batch']['message_source']!={'id':definition.data['message_source'],'mode':'runtime_event'}:
        raise PoiseError('Project message source must explicitly match the hook event source')
    from .locking import exclusive_lock
    with exclusive_lock(s.lock,s.raw['lock_seconds'],s.raw['lock_poll_seconds']):
        if s.path.is_file():
            if read_document(s.path)!=s.raw:raise PoiseError('Existing settings differ; do not rewrite active bindings')
        else:
            before=file_digest(s.hooks_file) if s.hooks_file.exists() else None
            if before!=value['expected_revision']:raise PoiseError('Hook configuration revision changed before setup')
            if before is not None:merge_hook_document(read_document(s.hooks_file),[],[])
            atomic_write(s.path,(json.dumps(s.raw,ensure_ascii=False,indent=2)+'\n').encode(),s.raw['file_mode'])
    service=HookService(s.path)
    installed=service.install(value)
    return {**installed,'settings_path':str(s.path),
            'capability_checks':service.probes(installed['definition_path'],project['git']['repository'])}
