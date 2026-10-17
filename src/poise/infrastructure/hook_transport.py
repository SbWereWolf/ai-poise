"""Managed Codex hook installation and a session-scoped entry to WorkTools.

The adapter never edits Codex trust or starts task transitions from native events.
"""
from copy import deepcopy
from contextlib import contextmanager
import json
import os
from pathlib import Path
import shlex
import string
from dataclasses import replace
from ..common import PoiseError,descendant,exact_keys,digest,file_digest,encoded,load_config
from ..modules.hook_transport.domain import HookDefinition,parse_native_event,merge_hook_document,binding_context
from ..modules.capabilities.domain import positive
from ..modules.runtime_adapter.domain import RuntimeIdentity
from ..modules.session_establishment.domain import CallerIdentity
from ..modules.foundation.validation import configuration_fields, nonempty
from ..application.capabilities import CapabilityChecks,SessionReadiness
from ..application.hook_transport import HookCommands
from ..application.work import WorkTools
from .capabilities import LocalProbeExecutor,FileCapabilitySummary
from .goal_config import read_document,atomic_write
from .session_establishment import establish_poise,native_session
from .sqlite.hook_transport import HookRegistry


class HookSettings:
    def __init__(self,path):
        self.path=Path(path).resolve(strict=True);self.raw=read_document(self.path)
        self._validate()

    @classmethod
    def proposed(cls,path,document):
        nonempty(str(path) if isinstance(path,Path) else path, 'hook settings_path')
        obj=cls.__new__(cls);obj.path=Path(path).resolve();obj.raw=deepcopy(document)
        obj._validate()
        return obj

    def _validate(self):
        s=self.raw
        configuration_fields(s,{'schema','root','project_config','python','source_root','hooks_file','definitions',
            'state','lock','database','bindings','launcher','binding_file','observations','response_file',
            'cwd_roots','file_mode','executable_mode','lock_seconds','lock_poll_seconds','max_input_bytes',
            'output_chars','max_probes','exit_codes','probe_files'},'hook settings')
        if s['schema']!='hook-settings-1':raise PoiseError('Unsupported hook settings schema')
        for key in ('root','project_config','python','source_root','hooks_file','definitions',
                    'state','lock','database','bindings','launcher','binding_file','observations','response_file'):
            nonempty(s[key], 'hook settings.' + key)
        files = configuration_fields(s['probe_files'],{'stdout','stderr','receipt'},'probe_files')
        for key,value in files.items():nonempty(value, 'probe_files.' + key)
        if len(set(files.values()))!=3:raise PoiseError('Probe files must be distinct')
        codes = configuration_fields(s['exit_codes'],{'success','incomplete','rejected'},'exit_codes')
        for key,value in codes.items():
            if type(value) is not int or not 0<=value<=255:
                raise PoiseError('exit_codes.' + key + ': integer CLI exit code required')
        if len(set(codes.values()))!=3:raise PoiseError('Explicit distinct CLI exit codes required')
        if not isinstance(s['cwd_roots'],list) or not s['cwd_roots']:raise PoiseError('Explicit cwd roots required')
        for p in s['cwd_roots']:
            nonempty(p, 'cwd_roots element')
            if not Path(p).is_absolute():raise PoiseError('cwd_roots require absolute paths')
        self.root=(self.path.parent/s['root']).resolve()
        if not self.root.is_dir():raise PoiseError('hook settings.root: existing directory required')
        self.project_config=descendant(self.root,s['project_config'])
        native_path = Path(s['hooks_file'])
        if native_path.is_absolute():
            if 'repository_scope' not in s:
                raise PoiseError('An absolute native hooks file requires explicit repository_scope')
            self.hooks_file = native_path.resolve()
        else:
            self.hooks_file=descendant(self.root,s['hooks_file'])
        self.definitions=descendant(self.root,s['definitions'])
        self.state=descendant(self.root,s['state']);self.lock=descendant(self.root,s['lock'])
        self.database=descendant(self.state,s['database'])
        self.bindings=descendant(self.state,s['bindings'])
        self.observations=descendant(self.state,s['observations'])
        self.hooks_lock = None
        if 'repository_scope' in s:
            scope = configuration_fields(s['repository_scope'], {'repository', 'timeout_seconds'},
                                         'repository_scope')
            nonempty(scope['repository'], 'repository_scope.repository')
            if not Path(scope['repository']).is_absolute() or not Path(scope['repository']).is_dir():
                raise PoiseError('repository_scope.repository requires an existing absolute directory')
            positive(scope['timeout_seconds'], 'repository_scope.timeout_seconds')
            nonempty(s.get('hooks_lock'), 'hooks_lock')
            if not Path(s['hooks_lock']).is_absolute():
                raise PoiseError('hooks_lock requires an explicit absolute path')
            self.hooks_lock = Path(s['hooks_lock']).resolve()
            if self.hooks_lock in (self.hooks_file, self.database, self.lock, self.path):
                raise PoiseError('The shared native-file lock must have its own path')
        elif 'hooks_lock' in s:
            raise PoiseError('hooks_lock requires explicit repository_scope')
        for name in files.values():descendant(self.state,name)
        for key in ('launcher','binding_file','response_file'):descendant(self.state,s[key])
        if s['launcher']==s['binding_file']:raise PoiseError('Binding and launcher filenames must differ')
        if self.state.is_relative_to(self.definitions) or self.definitions.is_relative_to(self.state):
            raise PoiseError('Definitions and operational state must have distinct roots')
        if self.hooks_file.is_relative_to(self.state):raise PoiseError('Native hooks must not live in ephemeral operational state')
        for key in ('python','source_root'):
            if not Path(s[key]).is_absolute():raise PoiseError(f'{key} must be an explicit absolute path')
        self.require_runtime()
        for key in ('max_input_bytes','output_chars','max_probes'):positive(s[key],key,True)
        for key in ('lock_seconds','lock_poll_seconds'):positive(s[key],key)
        for key in ('file_mode','executable_mode'):
            if type(s[key]) is not int or not 0<=s[key]<=0o777:raise PoiseError(f'{key}: explicit POSIX mode required')
        if not s['executable_mode'] & 0o100:raise PoiseError('Launcher mode must allow owner execution')
        minimal=encoded({'status':'capabilities_unavailable','response_path':str(self.observations/('0'*32)/s['response_file'])})+'\n'
        if len(minimal)>s['output_chars']:raise PoiseError('Output budget cannot fit the configured receipt address')
        if 'capability_summary' in s:
            self.require_summary()
        if 'project_launcher' in s:
            value=configuration_fields(s['project_launcher'],{'settings_path','context_template'},'project_launcher')
            nonempty(value['settings_path'],'project_launcher.settings_path')
            if not Path(value['settings_path']).is_absolute():
                raise PoiseError('project_launcher.settings_path requires an absolute path')
            nonempty(value['context_template'],'project_launcher.context_template')
            try:
                fields={field for _,field,_,_ in string.Formatter().parse(value['context_template']) if field is not None}
                if fields!={'launcher'}:raise ValueError('Expected launcher field')
                value['context_template'].format(launcher='sample')
            except (KeyError,ValueError,IndexError) as exc:
                raise PoiseError('Invalid project_launcher.context_template') from exc

    def require_summary(self):
        """The feature is mandatory for its operations, independent of core inspection."""
        if 'capability_summary' not in self.raw:
            raise PoiseError('Missing capability_summary; configure file, header_template and item_template')
        value = configuration_fields(self.raw['capability_summary'],
            {'file', 'header_template', 'item_template'}, 'capability_summary')
        nonempty(value['file'], 'capability_summary.file')
        path = descendant(self.bindings, value['file'])
        if path in (descendant(self.bindings, self.raw['binding_file']),
                    descendant(self.bindings, self.raw['launcher'])):
            raise PoiseError('capability_summary.file must differ from binding and launcher')
        for key, allowed in (
            ('header_template', {'ready', 'checked_at', 'workspace', 'snapshot_path'}),
            ('item_template', {'id', 'status', 'required', 'reason', 'remediation'}),
        ):
            nonempty(value[key], 'capability_summary.' + key)
            try:
                fields = {field for _, field, _, _ in string.Formatter().parse(value[key]) if field is not None}
                if fields != allowed:
                    raise ValueError('Template must contain exactly its named fields')
                value[key].format(**{field: 'sample' for field in allowed})
            except (KeyError, ValueError, IndexError) as exc:
                raise PoiseError('Invalid capability_summary.' + key) from exc
        return value

    @contextmanager
    def native_file_locked(self):
        if self.hooks_lock is None:
            yield
            return
        from .locking import exclusive_lock
        with exclusive_lock(self.hooks_lock, self.raw['lock_seconds'], self.raw['lock_poll_seconds']):
            yield

    def require_runtime(self,launcher=None):
        """Check selected executable paths without choosing a replacement runtime."""
        paths=[('interpreter',Path(self.raw['python']))]
        if launcher is not None:paths.append(('launcher',Path(launcher)))
        for role,path in paths:
            if not path.exists():reason='missing'
            elif not path.is_file() or not os.access(path,os.X_OK):reason='not executable'
            else:continue
            raise PoiseError(
                f'Configured runtime environment/configuration failure: {role} {str(path)!r} is {reason}. '
                'Restore the selected environment or owned launcher, then repeat the native event '
                'with the same session binding. No Task work was started.')

        entry=Path(self.raw['source_root'])/'poise/__main__.py'
        if not entry.is_file():
            raise PoiseError(
                f'Configured runtime environment/configuration failure: entry point {str(entry)!r} is missing. '
                'Restore the configured installation source; do not substitute a Task checkout. '
                'No Task work was started.')

    def _direct_command(self,python_flags,verb,*args):
        return shlex.join(['env','PYTHONPATH='+self.raw['source_root'],self.raw['python'],
                           *python_flags,'-m','poise',verb,'--settings',str(self.path),*args])

    def _superseded_guarded_command(self,python_flags,verb,*args):
        """Serialize only the exact Task 0152 launcher form for one-way migration."""
        recovery=str(Path(self.raw['source_root'])/'poise/recover_runtime.sh')
        recovery_command=shlex.join(['bash',recovery,'--settings',str(self.path),
                                     '--python','/absolute/path/to/compatible/python'])
        guard=(
            'interpreter=$1; recovery_command=$2; source_root=$3; shift 3; '
            'if [ ! -x "$interpreter" ]; then '
            'printf "%s\\n" "Configured Poise interpreter is unavailable: $interpreter" >&2; '
            'printf "%s\\n" "Recovery: $recovery_command" >&2; '
            'exit 126; fi; '
            'exec env "PYTHONPATH=$source_root" "$interpreter" "$@"'
        )
        return shlex.join(['/bin/sh','-c',guard,'poise-runtime-guard',self.raw['python'],
                           recovery_command,self.raw['source_root'],
                           *python_flags,'-m','poise',verb,'--settings',str(self.path),*args])

    def command(self,verb,*args):
        guard=(
            'source_root=$1; settings=$2; rejected=$3; separator=$4; shift 4; '
            'case "$source_root" in *"$separator"*) valid=0;; *) valid=1;; esac; '
            'if [ "$valid" = 0 ] || [ ! -f "$source_root/poise/__init__.py" ] '
            '|| [ ! -f "$source_root/poise/__main__.py" ]; then '
            'printf "%s\\n" '
            '"Configured Poise source must be one directory containing poise/__init__.py and poise/__main__.py: $source_root" '
            '"Recovery: restore that source directory or correct source_root in $settings, then retry." >&2; '
            'exit "$rejected"; fi; exec "$@"'
        )
        prefix=shlex.join(['/bin/sh','-c',guard,'poise-source-check',
                           self.raw['source_root'],str(self.path),
                           str(self.raw['exit_codes']['rejected']),os.pathsep])
        return prefix+' '+self._direct_command(
            ['-P','-B','-X','pycache_prefix='+os.devnull],verb,*args)

    def launcher_bytes(self, binding_path):
        return ('#!/bin/sh\nexec ' + self.command('hook-work', '--binding', str(binding_path)) + ' "$@"\n').encode()

    def prior_launcher_commands(self,verb,*args):
        """Exact superseded forms accepted only for an owned launcher upgrade."""
        previous=self._direct_command(
            ['-P','-B','-X','pycache_prefix='+os.devnull],verb,*args)
        return (
            self._direct_command(['-B'],verb,*args),
            self._superseded_guarded_command(['-B'],verb,*args),
            self._superseded_guarded_command([],verb,*args),
            self._direct_command([],verb,*args),
            previous,
        ) + ((self._direct_command(['-B'],verb,*args)+' "$@"',
              previous+' "$@"') if verb=='hook-work' else ())


def _read_definition(settings,path):
    p=Path(path)
    if not p.is_absolute() or p.is_symlink() or not p.resolve().is_relative_to(settings.definitions):
        raise PoiseError('Hook definition must be within configured definition root')
    value=read_document(p)
    if p.stem!=digest(value):raise PoiseError('Hook definition digest mismatch')
    return HookDefinition.parse(value)


def matches_repository_scope(settings, cwd):
    """A shared native source selects its Git repository before Task runtime."""
    if 'repository_scope' not in settings.raw:
        return True
    from .git_transport import run_git_receipt
    nonempty(cwd, 'Native cwd')
    if not Path(cwd).is_absolute():
        raise PoiseError('Native cwd requires an absolute path')
    scope = settings.raw['repository_scope']
    arguments = ['rev-parse', '--path-format=absolute', '--git-common-dir']
    # These local observations select repositories by explicit paths, not by
    # a caller's inherited Git repository/configuration controls.
    environment = {key: value for key, value in os.environ.items() if not key.startswith('GIT_')}
    selected = run_git_receipt(scope['repository'], arguments, scope['timeout_seconds'], environment)
    if selected['actual_exit_code'] != 0:
        raise PoiseError('Configured session repository is unavailable: ' + selected['stderr'])
    def common_directory(receipt):
        value = receipt['stdout'].removesuffix('\n')
        if not value or not Path(value).is_absolute():
            raise PoiseError('An absolute Git common directory is required from the selected Git command')
        return Path(value).resolve()
    selected_directory = common_directory(selected)
    observed = run_git_receipt(cwd, arguments, scope['timeout_seconds'], environment)
    if observed['actual_exit_code'] != 0:
        return False
    return selected_directory == common_directory(observed)


class FileHookRepository:
    def __init__(self,settings,registry):self.settings,self.registry=settings,registry

    def relocate(self, target, installation, request_id):
        """Publish this installation under an already held project/source lease."""
        with self.settings.native_file_locked():
            return self._relocate(target, installation, request_id)

    def _relocate(self, target, installation, request_id):
        import hashlib
        import shutil
        proposed = HookSettings.proposed(target['settings_path'], target['settings'])
        source = self.settings
        if proposed.raw == source.raw and proposed.path == source.path:
            return installation['receipt']
        if proposed.project_config != source.project_config:
            raise PoiseError('Hook relocation cannot change project association')
        definition_path = Path(installation['receipt']['definition_path'])
        definition = _read_definition(source, definition_path)
        if definition.data['id'] != target['installation_id']:
            raise PoiseError('Hook relocation installation identity differs')
        if (proposed.definitions != source.definitions or proposed.hooks_file != source.hooks_file
                or proposed.hooks_lock != source.hooks_lock):
            raise PoiseError('Hook relocation requires explicit unchanged definition/native-file owners')
        if not proposed.state.exists():
            shutil.copytree(source.state, proposed.state)
        if not proposed.database.is_file():
            raise PoiseError('Hook relocation requires the copied existing registry')
        repository = FileHookRepository(proposed, HookRegistry(proposed))
        desired = repository._groups(definition, definition_path)
        with repository.registry.transaction() as db:
            row = db.execute('SELECT data FROM installations WHERE id=?', (target['installation_id'],)).fetchone()
            if row is None:
                raise PoiseError('Copied hook installation identity is missing')
            current = json.loads(row['data'])
            if current['groups'] not in (installation['groups'], desired):
                raise PoiseError('Copied hook installation changed before relocation')
            native = read_document(source.hooks_file)
            if all(native.get('hooks', {}).get(event, []).count(group) == 1 for event, group in desired):
                candidate = native
            else:
                if any(native.get('hooks', {}).get(event, []).count(group) != 1
                       for event, group in installation['groups']):
                    raise PoiseError('Native hook groups changed before relocation')
                candidate = merge_hook_document(native, installation['groups'], desired)
            body = (json.dumps(candidate, ensure_ascii=False, indent=2, sort_keys=True) + '\n').encode()
            receipt = {**installation['receipt'], 'revision': hashlib.sha256(body).hexdigest()}
            if proposed.path.exists():
                if read_document(proposed.path) != proposed.raw:
                    raise PoiseError('Hook relocation settings destination conflicts')
            else:
                atomic_write(proposed.path, (json.dumps(proposed.raw, ensure_ascii=False, indent=2) + '\n').encode(),
                             proposed.raw['file_mode'])
            if source.hooks_file.read_bytes() != body:
                atomic_write(source.hooks_file, body, proposed.raw['file_mode'])
            db.execute('UPDATE installations SET data=? WHERE id=?',
                       (encoded({'groups': desired, 'receipt': receipt}), target['installation_id']))
            return receipt

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

    def _reject_unregistered_groups(self,document,previous,definition):
        """Recognize only exact commands of this settings/source installation."""
        if document is None:return
        merge_hook_document(document,[],[])  # Validate the existing envelope without changing it.
        prefixes=[shlex.split(self.settings.command('hook')),
                  shlex.split(self.settings._direct_command([],'hook'))]
        for event,groups in document.get('hooks',{}).items():
            for group in groups:
                if not isinstance(group,dict) or [event,group] in previous:continue
                hooks=group.get('hooks',[])
                if not isinstance(hooks,list):continue
                for hook in hooks:
                    if not isinstance(hook,dict) or hook.get('type')!='command':continue
                    if not isinstance(hook.get('command'),str):continue
                    try:argv=shlex.split(hook.get('command',''))
                    except ValueError:continue
                    if not any(argv[:len(prefix)]==prefix and len(argv)==len(prefix)+2
                               and argv[-2]=='--definition' for prefix in prefixes):continue
                    observed=_read_definition(self.settings,argv[-1])
                    if observed.data['id']==definition.data['id']:
                        raise PoiseError('Unregistered managed hook groups: use runtime-config reconcile '
                                         'with explicit previous installation identity and live definition')

    def _reconciled_groups(self,db,document,previous,definition,reconciliation):
        old_id=reconciliation['previous_installation_id']
        if old_id==definition.data['id']:raise PoiseError('Reconciliation requires distinct old/new installation identities')
        row=db.execute('SELECT data FROM installations WHERE id=?',(old_id,)).fetchone()
        if row is None:raise PoiseError('Unknown previous installation identity')
        old=json.loads(row[0])
        if old.get('migrated_to') is not None:raise PoiseError('Previous installation identity already migrated')
        if old['receipt']['hooks_path']!=str(self.settings.hooks_file):
            raise PoiseError('Previous installation belongs to another native hooks file')
        live_path=reconciliation['live_definition_path']
        live=_read_definition(self.settings,live_path)
        if live.data['id']!=definition.data['id']:
            raise PoiseError('Live definition does not prove the requested current installation identity')
        if not isinstance(document,dict) or not isinstance(document.get('hooks'),dict):
            raise PoiseError('Reconciliation requires an existing native hooks document')
        removed=[]
        # Every declared live group must be present exactly once. The only older
        # direct form accepted here is the existing owned no-bytecode-flag form.
        for event,group in self._groups(live,Path(live_path)):
            older=deepcopy(group)
            older['hooks'][0]['command']=self.settings._direct_command([],'hook','--definition',live_path)
            choices=[group] if older==group else [group,older]
            groups=document['hooks'].get(event,[])
            found=[candidate for candidate in choices for _ in range(groups.count(candidate))]
            if len(found)!=1:raise PoiseError('Exact live managed hook group is missing, changed or duplicated')
            removed.append([event,found[0]])
        for event,group in [*old['groups'],*previous]:
            count=document['hooks'].get(event,[]).count(group)
            if count>1:raise PoiseError('Previously owned group multiplicity is ambiguous')
            if count==1 and [event,group] not in removed:removed.append([event,group])
        self._reject_unregistered_groups(document,removed,definition)
        proof={**reconciliation,'installation_id':definition.data['id'],
               'group_fingerprints':[{'event':event,'sha256':digest(group)} for event,group in removed]}
        return removed,proof,row[0]

    def install(self,request_id,expected_revision,definition):
        with self.settings.native_file_locked():
            return self._install(request_id,expected_revision,definition,None)

    def reconcile(self,request_id,expected_revision,definition,previous_installation_id,live_definition_path):
        with self.settings.native_file_locked():
            return self._install(request_id,expected_revision,definition,{
                'previous_installation_id':previous_installation_id,'live_definition_path':live_definition_path})

    def _install(self,request_id,expected_revision,definition,reconciliation):
        s=self.settings
        s.require_runtime()
        if len(definition.data['probes'])>s.raw['max_probes']:raise PoiseError('Too many probes')
        _,project,_=load_config(s.project_config)
        if project['batch']['message_source']!={'id':definition.data['message_source'],'mode':'runtime_event'}:
            raise PoiseError('Project message source must explicitly match the hook event source')
        intent=[request_id,expected_revision,definition.data,str(s.hooks_file)]
        if reconciliation is not None:intent.append(reconciliation)
        packet=digest(intent)
        # External file effects are protected by the same bounded installer lock.
        # Pending intent is committed before replacement, so a retry can reconcile it.
        with self.registry.transaction() as db:
            identity=db.execute('SELECT data FROM installations WHERE id=?',(definition.data['id'],)).fetchone()
            if identity is not None and json.loads(identity[0]).get('migrated_to') is not None:
                raise PoiseError(f"Installation identity migrated to {json.loads(identity[0])['migrated_to']}")
            row=db.execute('SELECT data FROM operations WHERE id=?',(request_id,)).fetchone()
            if row is not None:
                operation=json.loads(row[0])
                if operation['digest']!=packet:raise PoiseError('Request id reused with conflicting hook definition/destination')
            else:
                before=self.revision()
                if before!=expected_revision:raise PoiseError(f'Hook configuration revision changed: {before}')
                old=db.execute('SELECT data FROM installations WHERE id=?',(definition.data['id'],)).fetchone()
                owned=None if old is None else json.loads(old[0])
                if owned is not None and owned.get('migrated_to') is not None:
                    raise PoiseError(f"Installation identity migrated to {owned['migrated_to']}")
                previous=[] if owned is None else owned['groups']
                document=None if before is None else read_document(s.hooks_file)
                proof=None;prior_installation=None
                if reconciliation is None:self._reject_unregistered_groups(document,previous,definition)
                else:
                    previous,proof,prior_installation=self._reconciled_groups(
                        db,document,previous,definition,reconciliation)
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
                if proof is not None:receipt['reconciliation']=proof
                operation={'digest':packet,'phase':'pending','before':before,'candidate':candidate,
                    'groups':desired,'definition':definition.data,'receipt':receipt}
                if proof is not None:operation['prior_installation']=prior_installation
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
            proof=receipt.get('reconciliation')
            if proof is not None:
                prior=db.execute('SELECT data FROM installations WHERE id=?',
                                 (proof['previous_installation_id'],)).fetchone()
                if prior is None or prior[0]!=operation['prior_installation']:
                    raise PoiseError('Previous installation changed since reconciliation planning')
            path=Path(receipt['definition_path']);body=(json.dumps(operation['definition'],ensure_ascii=False,indent=2)+'\n').encode()
            if path.exists() and path.read_bytes()!=body:raise PoiseError('Immutable hook definition was changed')
            if not path.exists():atomic_write(path,body,s.raw['file_mode'])
            if now!=receipt['revision']:
                atomic_write(s.hooks_file,(json.dumps(operation['candidate'],ensure_ascii=False,indent=2,sort_keys=True)+'\n').encode(),s.raw['file_mode'])
            if proof is not None:
                prior=json.loads(operation['prior_installation'])
                prior.update(migrated_to=proof['installation_id'],migration_request_id=request_id,
                             migration_revision=receipt['revision'])
                db.execute('UPDATE installations SET data=? WHERE id=?',
                           (encoded(prior),proof['previous_installation_id']))
            operation['phase']='completed'
            db.execute('UPDATE operations SET data=? WHERE id=?',(encoded(operation),request_id))
            db.execute('INSERT INTO installations VALUES(?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data',
                (operation['definition']['id'],encoded({'groups':operation['groups'],'receipt':receipt})))
            self.registry.event_in(db,None,'install.completed',{'request_id':request_id,'revision':receipt['revision']})
            return receipt


class HookService:
    """Composition boundary. Native events observe/bind; WorkTools alone changes work."""
    def __init__(self,settings_path):
        from .project_resources import ProjectRuntimeFence
        self.settings=HookSettings(settings_path)
        self.project_fence=ProjectRuntimeFence(self.settings.project_config,hook_settings=self.settings)
        self.registry=HookRegistry(self.settings,self.project_fence)
        self.repository=FileHookRepository(self.settings,self.registry)
        self.commands=HookCommands(self.repository)

    def diagnose(self, packet):
        from ..application.hook_diagnostics import HookDiagnostics
        from .hook_diagnostics import HookObservationPort
        return HookDiagnostics(HookObservationPort(self)).invoke(packet)

    def revision(self):return self.repository.revision()
    def install(self,packet):
        self.settings.require_summary()
        with self.project_fence.locked():
            return self.commands.install(packet)
    def reconcile(self,packet):
        self.settings.require_summary()
        with self.project_fence.locked():
            return self.commands.reconcile(packet)

    def reconfigure(self, packet):
        from .hook_reconfiguration import reconfigure
        return reconfigure(self.settings.path, packet)

    def definition(self,path):return _read_definition(self.settings,path)

    def _bind(self,definition_path,event):
        with self.project_fence.locked():
            return self._bind_locked(definition_path,event)

    def _bind_locked(self,definition_path,event):
        self.settings.require_runtime()
        definition=self.definition(definition_path)
        native=parse_native_event(event,definition)
        cwd=Path(native['cwd'])
        accepted = (matches_repository_scope(self.settings, str(cwd))
                    if 'repository_scope' in self.settings.raw else
                    any(cwd.resolve().is_relative_to(Path(r).resolve()) for r in self.settings.raw['cwd_roots']))
        if not cwd.is_absolute() or not accepted:
            raise PoiseError('Native cwd outside configured roots')
        cfg=self.project_fence.hook_manifest()
        d=definition.data
        if cfg['batch']['message_source']!={'id':d['message_source'],'mode':'runtime_event'}:
            raise PoiseError('Do not combine hook and transcript/reported message sources')
        CallerIdentity.native(cfg['project'], native['session_id'])
        identity=RuntimeIdentity.parse(cfg['project'],d['message_source'],
            {'kind':'external','session_id':native['session_id'],'agent_id':d['agent_id']})
        root=descendant(self.settings.bindings,identity.key)
        launcher=descendant(root,self.settings.raw['launcher']);binding=descendant(root,self.settings.raw['binding_file'])
        assigned=native['session_id']
        if binding.exists():
            prior,_=self._binding_record(binding)
            assigned=prior['session_id']
        record={'session_id':assigned,'external_session':native['session_id'],'agent_id':d['agent_id'],
            'project':cfg['project'],'settings':str(self.settings.path),'settings_digest':digest(self.settings.raw),
            'definition_path':str(Path(definition_path).resolve()),'launcher':str(launcher),'binding_path':str(binding)}
        if binding.exists() and binding_context(prior)!=binding_context(record):
            raise PoiseError('Existing hook binding differs from the selected session/settings; '
                             'use its matching settings and definition, then retry SessionStart')
        record=self.registry.bind(record)
        # Both files are deterministic for the binding; no task root or global current file.
        raw=(json.dumps(record,ensure_ascii=False,indent=2)+'\n').encode()
        if not binding.exists():atomic_write(binding,raw,self.settings.raw['file_mode'])
        script=self.settings.launcher_bytes(binding)
        priors=tuple(('#!/bin/sh\nexec '+command+'\n').encode() for command in
            self.settings.prior_launcher_commands('hook-work','--binding',str(binding)))
        if launcher.exists():
            current=launcher.read_bytes()
            if current in priors:atomic_write(launcher,script,self.settings.raw['executable_mode'])
            elif current!=script:raise PoiseError('Launcher was changed externally')
            elif launcher.stat().st_mode & 0o777 != self.settings.raw['executable_mode']:
                atomic_write(launcher,script,self.settings.raw['executable_mode'])
        else:atomic_write(launcher,script,self.settings.raw['executable_mode'])
        return definition,native,record

    def _binding_record(self,path):
        p=Path(path)
        if not p.is_absolute() or p.is_symlink() or not p.resolve().is_relative_to(self.settings.bindings):
            raise PoiseError('Binding outside configured root')
        raw=read_document(p)
        exact_keys(binding_context(raw),{'session_id','external_session','agent_id','project','settings',
            'definition_path','launcher','binding_path'},'hook binding')
        stored,message=self.registry.get(raw['session_id'])
        if binding_context(raw)!=binding_context(stored):
            raise PoiseError('Binding file identifies a different registered actor; restore the matching binding')
        definition=self.definition(stored['definition_path'])
        cfg=self.project_fence.hook_manifest()
        identity=RuntimeIdentity.parse(cfg['project'],definition.data['message_source'],
            {'kind':'external','session_id':stored['external_session'],'agent_id':definition.data['agent_id']})
        root=descendant(self.settings.bindings,identity.key)
        expected={'project':cfg['project'],'settings':str(self.settings.path),
            'agent_id':definition.data['agent_id'],'binding_path':str(p),
            'launcher':str(descendant(root,self.settings.raw['launcher']))}
        if (p!=descendant(root,self.settings.raw['binding_file'])
                or any(stored.get(key)!=value for key,value in expected.items())):
            raise PoiseError('Binding object does not match the selected project/settings; select its matching configuration')
        return stored,message

    def _record(self,path):
        self.settings.require_runtime()
        stored,message=self._binding_record(path)
        self.settings.require_runtime(stored['launcher'])
        return stored,message

    def bound_runtime(self,binding_path):
        from .clock import SystemClock
        record,_=self._record(binding_path)
        caller=CallerIdentity.native(record['project'],record['external_session'])
        return establish_poise(self.settings.project_config,caller,[],SystemClock(),record['session_id']).runtime

    def latest_binding(self,external,agent):return self.registry.find(external,agent)

    def event(self,definition_path,event):
        if isinstance(event, dict) and event.get('hook_event_name') == 'SessionStart':
            self.settings.require_summary()
        with self.project_fence.locked():
            result, definition, native, binding = self._event_locked(definition_path,event)
        if native['event'] == 'SessionStart':
            from .clock import SystemClock
            readiness = self._readiness(binding)
            summary = readiness.refresh(binding['session_id'], native['cwd'],
                                        definition.data['probes'], SystemClock()).to_dict()
            options = self.settings.require_summary()
            lines = [options['header_template'].format(
                ready=summary['ready'], checked_at=summary['checked_at'], workspace=summary['workspace'],
                snapshot_path=str(readiness.store.path))]
            lines.extend(options['item_template'].format(**item) for item in summary['observations'])
            context = result['hookSpecificOutput']['additionalContext'] + '\n' + '\n'.join(lines)
            limit = next(e['context_limit'] for e in definition.data['events'] if e['event'] == 'SessionStart')
            if len(context) > limit:
                raise PoiseError('Capability summary context exceeds configured native context limit')
            result['hookSpecificOutput']['additionalContext'] = context
        return result

    def _event_locked(self,definition_path,event):
        definition,native,binding=self._bind(definition_path,event)
        if native['event'] == 'SessionStart':
            event_id = self.registry.record_event(
                binding['session_id'], native['event'], native['turn_id'], None,
                {'source': event['source'], 'workspace': native['cwd']})
            try:
                result = self._runtime_event(definition, native, binding, event, event_id)
            except Exception as exc:
                # Transport authority/binding already succeeded. Subject runtime
                # recovery cannot take the session's own entry away from it.
                context = definition.data['context_template'].format(
                    launcher=binding['launcher'], state=event['source'])
                result = {'hookSpecificOutput': {'hookEventName': native['event'],
                                                 'additionalContext': context},
                          'systemMessage': str(exc)}
        else:
            result = self._runtime_event(definition, native, binding, event, None)
        if native['event'] in ('SessionStart','UserPromptSubmit') and 'project_launcher' in self.settings.raw:
            result['hookSpecificOutput']['additionalContext'] += '\n' + self.settings.raw['project_launcher']['context_template'].format(launcher=binding['launcher'])
            limit=next(e['context_limit'] for e in definition.data['events'] if e['event']==native['event'])
            if len(result['hookSpecificOutput']['additionalContext'])>limit:
                raise PoiseError('Project selection context exceeds configured native context limit')
        return result, definition, native, binding

    def _runtime_event(self,definition,native,binding,event,event_id):
        from .clock import SystemClock
        caller=CallerIdentity.native(binding['project'],binding['external_session'])
        h=establish_poise(self.settings.project_config,caller,[],SystemClock(),binding['session_id']).runtime
        task=h.current_task()
        active=task if task is not None and task['status'] not in ('completed','cancelled') else None
        message=None
        if native['event']=='UserPromptSubmit':
            message={'conversation_id':native['session_id'],'message_id':native['turn_id'],
                     'occurred_at':None,'reason':None,'subject':None}
            h.interactions.record(h.interactions.prepare([message]),h.session,active)
        if native['event'] != 'SessionStart':
            event_id = self.registry.record_event(h.session,native['event'],native['turn_id'],message)
        recovery = None
        if native['event'] == 'SessionStart' and h.context_reader is not None:
            recovery = h.restore_context(reason=event['source'], event_id=f'hook-event:{event_id}',
                facts=[], cwd=native['cwd'], reads=[])

        state='idle' if task is None else f"{task['id']} / {task['status']} / {task['workflow']['stages'][task['stage_index']]['id']}"
        if native['event'] in ('SessionStart','UserPromptSubmit'):
            context=definition.data['context_template'].format(launcher=binding['launcher'],state=state)
            if recovery is not None:
                # Only a bounded pointer/generation goes into automatic hook context.
                # Full Task/route metadata and selected text use the explicit work operation.
                context += ('\nContext generation: ' + str(recovery['reader']['generation'])
                            + '; event: ' + recovery['reader']['event_id']
                            + '; reason: ' + recovery['reader']['reason']
                            + '. Use restore_context with that identity and explicit reads. '
                            + 'Prior receipts do not prove remembered text.')
                limit = next(e['context_limit'] for e in definition.data['events']
                             if e['event'] == native['event'])
                if native['event'] != 'SessionStart' and len(context) > limit:
                    raise PoiseError('Context recovery exceeds configured native context limit')
            result = {'hookSpecificOutput':{'hookEventName':native['event'],'additionalContext':context}}
        elif native['event']=='Stop':
            result = {'systemMessage':definition.data['stop_template'].format(state=state)}
        else:
            result = {}  # Advisory observation; no Task transition or direct PID signal.
        return result

    def _readiness(self, record):
        options = self.settings.require_summary()
        path = descendant(Path(record['binding_path']).parent, options['file'])
        store = FileCapabilitySummary(path, self.settings.raw['file_mode'],
                                      self.settings.raw['max_input_bytes'], self.project_fence)
        checks = CapabilityChecks(LocalProbeExecutor(self.settings.observations,
            self.settings.raw['file_mode'], self.settings.raw['probe_files']), self.settings.raw['max_probes'])
        return SessionReadiness(checks, store)

    def read_summary(self, packet):
        self.settings.require_summary()
        exact_keys(packet, {'binding_path'}, 'summary packet')
        nonempty(packet['binding_path'], 'binding_path')
        record, _ = self._owned_record(packet['binding_path'])
        return self._readiness(record).read(record['session_id']).to_dict()

    def refresh_summary(self, packet):
        from .clock import SystemClock
        self.settings.require_summary()
        exact_keys(packet, {'binding_path'}, 'refresh packet')
        nonempty(packet['binding_path'], 'binding_path')
        record, _ = self._owned_record(packet['binding_path'])
        workspace = self.registry.session_workspace(record['session_id'])
        definition = self.definition(record['definition_path'])
        return self._readiness(record).refresh(record['session_id'], workspace,
                                               definition.data['probes'], SystemClock()).to_dict()

    def probes(self,definition_path,workspace):
        d=self.definition(definition_path)
        return CapabilityChecks(LocalProbeExecutor(self.settings.observations,self.settings.raw['file_mode'],self.settings.raw['probe_files']),
            self.settings.raw['max_probes']).run(d.data['probes'],workspace)

    def _execute(self,h,record,req,definition):
        from .result_views import preflight_work_result
        preflight_work_result(h, req)
        if req['operation']=='verify':
            h.check_runner.bind_cancellation(self.registry.runner_cancellation_check(h.session))
        def invoke():
            return WorkTools(h).invoke(req)

        gated=req['operation'] in definition['gate_operations']
        result=None;checks=None
        # Bootstrap prepares a worktree before its project-bound capability probe.
        if req['operation']=='bootstrap':result=invoke()
        if gated:
            task=h.current_task()
            active_task=task is not None and task['status'] not in ('completed','cancelled')
            workspace=(task['worktree'] if active_task and task['worktree'] is not None
                       else h.cfg['git']['repository'])
            checks=self.probes(record['definition_path'],workspace)
            if not checks['ready'] and not (req['operation']=='bootstrap' and not active_task):
                h.interactions.record(h.interactions.prepare(req['messages']),h.session,
                    task if task is not None and task['status'] not in ('completed','cancelled') else None)
                return {'status':'capabilities_unavailable','capability_checks':checks,
                        'context':result if result is not None else h.show(),
                        'interaction':h.interactions.summary(task)}
        if result is None:result=invoke()
        return {**result,'capability_checks':checks,'hook_session':record['session_id']}

    def _owned_record(self, binding_path):
        observed = native_session(os.environ, required=True)
        record,message=self._record(binding_path)
        if observed != record['external_session']:
            raise PoiseError('Invoking native session identity does not own this launcher; use its own SessionStart launcher')
        return record, message

    def _selected_work(self, project_id, record, packet):
        from ..application.project_launch import ProjectLaunchCommands
        from .project_launch import FileProjectLaunch
        from ..modules.projects.launch import text
        from ..modules.hook_transport.domain import MissingHookBinding
        from .clock import SystemClock
        from ..modules.work.domain import parse_request, is_observation
        from .result_views import preflight_work_result
        text(project_id,'project_id')
        options=self.settings.raw.get('project_launcher')
        if options is None:raise PoiseError('Explicit project selection requires project_launcher configuration')
        port=FileProjectLaunch()
        selected=replace(port.read_settings(options['settings_path']),project_id=project_id)
        config_path=Path(ProjectLaunchCommands(port).resolve(selected)).resolve()
        if config_path==self.settings.project_config:
            return None
        _,cfg,_=load_config(config_path)
        hooks=cfg['runtime_resources']['hooks']
        if hooks:
            candidates=[]
            for association in hooks:
                target=HookService(association['settings_path'])
                if target.settings.project_config!=config_path:
                    raise PoiseError('Selected hook association does not bind its project manifest')
                try:
                    binding=target.registry.find(record['external_session'],None)
                except MissingHookBinding:
                    continue
                if target.definition(binding['definition_path']).data['id']!=association['installation_id']:
                    raise PoiseError('Selected hook installation does not match its native binding')
                candidates.append((target,binding))
            if len(candidates)!=1:
                raise PoiseError('Selected hooked project requires its own native binding; use its own native launcher')
            target,binding=candidates[0]
            result=target.work(binding['binding_path'],packet)
            self.runtime=target.runtime
            return result
        caller=CallerIdentity.native(cfg['project'],record['external_session'])
        req=parse_request(packet,cfg['batch'])
        observation=is_observation(req)
        h=establish_poise(config_path,caller,[],SystemClock(),read_only=observation).runtime
        self.runtime=h
        if not observation:h.store.ensure_session(h.session)
        preflight_work_result(h,req)
        return WorkTools(h).invoke(req)

    def work(self,binding_path,packet,project_id=None):
        from .clock import SystemClock
        from ..modules.work.domain import parse_request, is_observation
        record,message=self._owned_record(binding_path)
        if project_id is not None:
            selected=self._selected_work(project_id,record,packet)
            if selected is not None:return selected
        caller=CallerIdentity.native(record['project'],record['external_session'])
        _,cfg,_=load_config(self.settings.project_config)
        req=parse_request(packet,cfg['batch'])
        if req['messages']:raise PoiseError('Hooked work derives messages from UserPromptSubmit; do not supply a second source')
        h=establish_poise(
            self.settings.project_config,
            caller,
            [],
            SystemClock(),
            record['session_id'],
            None,
            self.registry.liveness,
            read_only=is_observation(req),
        ).runtime
        self.runtime=h
        req=deepcopy(req);req['messages']=[] if message is None else [message]
        d=self.definition(record['definition_path']).data
        if h.cfg['batch']['message_source']!={'id':d['message_source'],'mode':'runtime_event'}:
            raise PoiseError('Message source changed after hook binding')
        return self._execute(h,record,req,d)


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
    s.require_summary()
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
