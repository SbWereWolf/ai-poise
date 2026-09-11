"""Managed Codex hook installation and a session-scoped entry to WorkTools.

The adapter never edits Codex trust or starts task transitions from native events.
"""
from copy import deepcopy
import json
import os
from pathlib import Path
import shlex
from ..common import HarnessError,descendant,exact_keys,digest,file_digest,encoded,load_config
from ..modules.hook_transport.domain import HookDefinition,parse_native_event,merge_hook_document
from ..modules.capabilities.domain import positive,nonempty
from ..modules.runtime_adapter.domain import RuntimeIdentity
from ..application.capabilities import CapabilityChecks
from ..application.hook_transport import HookCommands
from ..application.work import WorkTools
from ..runtime import Harness
from .capabilities import LocalProbeExecutor
from .goal_config import read_document,atomic_write
from .sqlite.hook_transport import HookRegistry


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
        if s['schema']!='hook-settings-1':raise HarnessError('Unsupported hook settings schema')
        self.root=(self.path.parent/s['root']).resolve(strict=True)
        self.project_config=descendant(self.root,s['project_config'])
        self.hooks_file=descendant(self.root,s['hooks_file'])
        self.definitions=descendant(self.root,s['definitions'])
        self.state=descendant(self.root,s['state']);self.lock=descendant(self.root,s['lock'])
        self.database=descendant(self.state,s['database'])
        self.bindings=descendant(self.state,s['bindings'])
        self.observations=descendant(self.state,s['observations'])
        exact_keys(s['probe_files'],{'stdout','stderr','receipt'},'probe_files')
        if len(set(s['probe_files'].values()))!=3:raise HarnessError('Probe files must be distinct')
        for name in s['probe_files'].values():descendant(self.state,name)
        for key in ('launcher','binding_file','response_file'):descendant(self.state,s[key])
        if s['launcher']==s['binding_file']:raise HarnessError('Binding and launcher filenames must differ')
        if self.state.is_relative_to(self.definitions) or self.definitions.is_relative_to(self.state):
            raise HarnessError('Definitions and operational state must have distinct roots')
        if self.hooks_file.is_relative_to(self.state):raise HarnessError('Native hooks must not live in ephemeral operational state')
        for key in ('python','source_root'):
            nonempty(s[key],key)
            if not Path(s[key]).is_absolute():raise HarnessError(f'{key} must be an explicit absolute path')
        if not Path(s['python']).is_file() or not os.access(s['python'],os.X_OK):raise HarnessError('Configured interpreter is not executable')
        if not (Path(s['source_root'])/'harness/__main__.py').is_file():raise HarnessError('Configured Harness source root missing')
        for key in ('max_input_bytes','output_chars','max_probes'):positive(s[key],key,True)
        for key in ('lock_seconds','lock_poll_seconds'):positive(s[key],key)
        for key in ('file_mode','executable_mode'):
            if type(s[key]) is not int or not 0<=s[key]<=0o777:raise HarnessError(f'{key}: explicit POSIX mode required')
        if not s['executable_mode'] & 0o100:raise HarnessError('Launcher mode must allow owner execution')
        if not isinstance(s['cwd_roots'],list) or not s['cwd_roots']:raise HarnessError('Explicit cwd roots required')
        for p in s['cwd_roots']:
            if not isinstance(p,str) or not Path(p).is_absolute():raise HarnessError('cwd_roots require absolute paths')
        exact_keys(s['exit_codes'],{'success','incomplete','rejected'},'exit_codes')
        if len(set(s['exit_codes'].values()))!=3 or any(type(v) is not int or not 0<=v<=255 for v in s['exit_codes'].values()):
            raise HarnessError('Explicit distinct CLI exit codes required')
        minimal=encoded({'status':'capabilities_unavailable','response_path':str(self.observations/('0'*32)/s['response_file'])})+'\n'
        if len(minimal)>s['output_chars']:raise HarnessError('Output budget cannot fit the configured receipt address')

    def command(self,verb,*args):
        return shlex.join(['env','PYTHONPATH='+self.raw['source_root'],self.raw['python'],'-m','harness',verb,'--settings',str(self.path),*args])


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
        if len(definition.data['probes'])>s.raw['max_probes']:raise HarnessError('Too many probes')
        _,project,_=load_config(s.project_config)
        if project['batch']['message_source']!={'id':definition.data['message_source'],'mode':'runtime_event'}:
            raise HarnessError('Project message source must explicitly match the hook event source')
        packet=digest([request_id,expected_revision,definition.data,str(s.hooks_file)])
        # External file effects are protected by the same bounded installer lock.
        # Pending intent is committed before replacement, so a retry can reconcile it.
        with self.registry.transaction() as db:
            row=db.execute('SELECT data FROM operations WHERE id=?',(request_id,)).fetchone()
            if row is not None:
                operation=json.loads(row[0])
                if operation['digest']!=packet:raise HarnessError('Request id reused with conflicting hook definition/destination')
            else:
                before=self.revision()
                if before!=expected_revision:raise HarnessError(f'Hook configuration revision changed: {before}')
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
                if now!=receipt['revision']:raise HarnessError('Installed hooks changed since the saved receipt')
                return receipt
            if now not in (operation['before'],receipt['revision']):raise HarnessError('Pending hook installation conflicts with current file')
            path=Path(receipt['definition_path']);body=(json.dumps(operation['definition'],ensure_ascii=False,indent=2)+'\n').encode()
            if path.exists() and path.read_bytes()!=body:raise HarnessError('Immutable hook definition was changed')
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
            raise HarnessError('Hook definition must be within configured definition root')
        value=read_document(p)
        if p.stem!=digest(value):raise HarnessError('Hook definition digest mismatch')
        return HookDefinition.parse(value)

    def _bind(self,definition_path,event):
        definition=self.definition(definition_path)
        native=parse_native_event(event,definition)
        cwd=Path(native['cwd'])
        if not cwd.is_absolute() or not any(cwd.resolve().is_relative_to(Path(r).resolve()) for r in self.settings.raw['cwd_roots']):
            raise HarnessError('Native cwd outside configured roots')
        _,cfg,_=load_config(self.settings.project_config)
        d=definition.data
        if cfg['batch']['message_source']!={'id':d['message_source'],'mode':'runtime_event'}:
            raise HarnessError('Do not combine hook and transcript/reported message sources')
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
        if binding.exists() and binding.read_bytes()!=raw:raise HarnessError('Binding file was changed externally')
        if not binding.exists():atomic_write(binding,raw,self.settings.raw['file_mode'])
        script=('#!/bin/sh\nexec '+self.settings.command('hook-work','--binding',str(binding))+'\n').encode()
        if launcher.exists() and launcher.read_bytes()!=script:raise HarnessError('Launcher was changed externally')
        if not launcher.exists():atomic_write(launcher,script,self.settings.raw['executable_mode'])
        return definition,native,record

    def _record(self,path):
        p=Path(path)
        if not p.is_absolute() or p.is_symlink() or not p.resolve().is_relative_to(self.settings.bindings):
            raise HarnessError('Binding outside configured root')
        raw=read_document(p);stored,message=self.registry.get(raw['session_id'])
        if raw!=stored or stored['settings_digest']!=digest(self.settings.raw):raise HarnessError('Stale or changed hook binding')
        self.definition(stored['definition_path'])
        return stored,message

    def bound_runtime(self,binding_path):
        record,_=self._record(binding_path)
        return Harness(self.settings.project_config,record['session_id'])

    def latest_binding(self,external,agent):return self.registry.find(external,agent)

    def event(self,definition_path,event):
        definition,native,binding=self._bind(definition_path,event)
        h=Harness(self.settings.project_config,binding['session_id']);task=h.current_task()
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

    def work(self,binding_path,packet):
        record,message=self._record(binding_path)
        h=Harness(self.settings.project_config,record['session_id']);self.runtime=h
        from ..modules.work.domain import parse_request
        req=parse_request(packet,h.cfg['batch'])
        if req['messages']:raise HarnessError('Hooked work derives messages from UserPromptSubmit; do not supply a second source')
        req=deepcopy(req);req['messages']=[] if message is None else [message]
        d=self.definition(record['definition_path']).data
        if h.cfg['batch']['message_source']!={'id':d['message_source'],'mode':'runtime_event'}:
            raise HarnessError('Message source changed after hook binding')
        gated=req['operation'] in d['gate_operations']
        result=None;checks=None
        # Bootstrap must prepare the actual selected worktree before a project-bound probe.
        # It performs no target implementation. Failures retain the created context for recovery.
        if req['operation']=='bootstrap':result=WorkTools(h).invoke(req)
        if gated:
            task=h.current_task();workspace=h.cfg['git']['repository'] if task is None else task['worktree']
            checks=self.probes(record['definition_path'],workspace)
            active_task=task is not None and task['status'] not in ('completed','cancelled','superseded')
            if not checks['ready'] and not (req['operation']=='bootstrap' and not active_task):
                h.interactions.record(h.interactions.prepare(req['messages']),h.session,
                    task if task is not None and task['status'] not in ('completed','cancelled','superseded') else None)
                return {'status':'capabilities_unavailable','capability_checks':checks,
                        'context':result if result is not None else h.show(),
                        'interaction':h.interactions.summary(task)}
        if result is None:result=WorkTools(h).invoke(req)
        return {**result,'capability_checks':checks,'hook_session':record['session_id']}


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
    if not s.path.is_relative_to(s.root):raise HarnessError('Adapter settings must live in the Harness codebase')
    if s.path.is_relative_to(s.state):raise HarnessError('Adapter settings must not live in operational state')
    if len(definition.data['probes'])>s.raw['max_probes']:raise HarnessError('Too many probes')
    _,project,_=load_config(s.project_config)
    if project['batch']['message_source']!={'id':definition.data['message_source'],'mode':'runtime_event'}:
        raise HarnessError('Project message source must explicitly match the hook event source')
    from .locking import exclusive_lock
    with exclusive_lock(s.lock,s.raw['lock_seconds'],s.raw['lock_poll_seconds']):
        if s.path.is_file():
            if read_document(s.path)!=s.raw:raise HarnessError('Existing settings differ; do not rewrite active bindings')
        else:
            before=file_digest(s.hooks_file) if s.hooks_file.exists() else None
            if before!=value['expected_revision']:raise HarnessError('Hook configuration revision changed before setup')
            if before is not None:merge_hook_document(read_document(s.hooks_file),[],[])
            atomic_write(s.path,(json.dumps(s.raw,ensure_ascii=False,indent=2)+'\n').encode(),s.raw['file_mode'])
    service=HookService(s.path)
    installed=service.install(value)
    return {**installed,'settings_path':str(s.path),
            'capability_checks':service.probes(installed['definition_path'],project['git']['repository'])}
