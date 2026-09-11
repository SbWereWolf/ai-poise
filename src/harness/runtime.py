from __future__ import annotations
from copy import deepcopy
import fnmatch
import json
import os
import re
import shutil
import subprocess
import time
import uuid
from pathlib import Path
from .common import HarnessError, descendant, configured_root, digest, encoded, exact_keys, load_config, read_json, validate_method, file_digest
from .storage import Store
from .composition import task_tools
from .application.runner import StageRunner
from .application.evidence import EvidenceCommands
from .modules.content_requirements.domain import ArtifactFact
from .artifacts import inspect_paths, check_counts
from .execution import run_command, contains, preview


def resolve_source_under_test(
    method: dict,
    *,
    worktree: Path,
    cwd: Path,
    environment: dict[str, str],
) -> dict:
    """Resolve an explicit verification source contract against this task worktree."""
    source = method.get('source_under_test')
    if source is None:
        raise HarnessError(
            'Нельзя выполнить проверку: для provenance исходников требуется source_under_test'
        )
    if source['kind'] == 'external':
        return deepcopy(source)
    root = worktree.resolve()
    facts = []
    for binding in source['bindings']:
        resolved = (root / binding['path']).resolve()
        if not resolved.is_relative_to(root) or not resolved.is_dir():
            raise HarnessError(
                f"Не удалось подтвердить repository source provenance: {binding['path']}"
            )
        fact = {**binding, 'resolved_path': str(resolved)}
        if binding['kind'] == 'cwd':
            if resolved != cwd.resolve():
                raise HarnessError(
                    'Source provenance не подтверждена: cwd binding не совпадает с cwd команды'
                )
        else:
            if binding['name'] in environment:
                raise HarnessError(
                    f"Source provenance конфликтует с environment: {binding['name']}"
                )
            environment[binding['name']] = str(resolved)
        facts.append(fact)
    return {'kind': 'repository', 'bindings': facts}


class Harness:
    """Одна сессия, одна текущая задача; переход этапа только по решению пользователя."""
    def __init__(self, config_path: Path | str, session: str):
        self.config_path = Path(config_path).resolve()
        self.root, self.cfg, self.processes = load_config(self.config_path)
        self.session = self._identifier(session)
        self.state = configured_root(self.root, self.cfg['paths']['state'])
        self.paths = self.cfg['paths']
        self.runtime = descendant(self.state, self.paths['runtime']) / self.session
        if self.runtime.is_symlink():
            raise HarnessError('Корень сессии не может быть symlink')
        self.config_hash = digest(self.cfg)
        limits = self.cfg['limits']
        self.store = Store(descendant(self.state, self.paths['database']),
                           descendant(self.state, self.paths['lock']),
                           limits['lock_seconds'], limits['lock_poll_seconds'])
        self.task_commands, self.task_queries = task_tools(self.store)
        self.runner = StageRunner(self.task_commands)
        self.evidence_commands = EvidenceCommands(self.store.unit_of_work)
        from .infrastructure.sqlite.interactions import InteractionStore
        from .infrastructure.work import WorkResources
        self.interactions=InteractionStore(self.store.database,self.cfg['project'],self.cfg['batch'])
        self.work_resources=WorkResources(self)
        from .application.accounting import AccountingCommands
        from .infrastructure.accounting import RuntimeAccounting
        self.accounting=AccountingCommands(RuntimeAccounting(self))
        from .infrastructure.sprint_work import SprintWork
        self.sprint_tools=SprintWork(self)
        from .infrastructure.actions import RuntimePlanActions
        self.plan_actions=RuntimePlanActions(self)
        from .infrastructure.result_views import ResultViews
        self.result_views=ResultViews(self.cfg['runtime_services']['output'],self._result_event)
        from .infrastructure.handoff import LocalHandoff
        self.handoff_tools=LocalHandoff(self)
        from .application.transfers import TransferCommands
        from .infrastructure.transfers import RuntimeTransfers
        self.transfer_tools=TransferCommands(RuntimeTransfers(self),self.cfg['runtime_services']['transfer'])
        from .application.result_integration import ResultIntegrationCommands
        from .infrastructure.result_integration import RuntimeResultIntegration
        self.integration_tools=ResultIntegrationCommands(RuntimeResultIntegration(self))

    def _result_event(self,event,payload):
        current=self.current_task()
        self.store.event(self.session,None if current is None else current['id'],event,payload)

    def report_task(self,report):
        if isinstance(report.get('task'),str):return self.task_queries.record(report['task'])
        return self.current_task()

    def handoff(self,args):
        result=self.handoff_tools.preserve(args)
        self.accounting.close_cycle()
        return result

    def current_task(self):
        return self.store.current(self.session)

    def validate_stage_result(self,task_id,payload):
        self.plan_actions.validate(self._task(),payload)
        return self.task_commands.validate_submission(task_id,self.session,payload)

    @staticmethod
    def packet_digest(value):
        return digest(value)

    def validate_artifact_paths(self, paths, task):
        roots=self._roots(task)
        owners={'runtime':self.session,'task':task['id']}
        if task['sprint_id'] is not None:owners['sprint']=task['sprint_id']
        return inspect_paths(paths,roots,owners)


    def register_artifact_paths(self,paths,task):
        if task is None:
            roots={'runtime':self.runtime};owners={'runtime':self.session}
            sprint=self.sprint_tools.overview(None)
            if sprint is not None:
                roots['sprint']=descendant(self.state,self.paths['sprints'])/sprint['sprint']
                owners['sprint']=sprint['sprint']
            records=inspect_paths(paths,roots,owners)
            self.store.link_sprint_artifacts([r for r in records if r['scope']=='sprint'])
            return records
        records=self.validate_artifact_paths(paths,task)
        self.store.link_artifacts(task['id'],[r for r in records if r['scope']!='runtime'])
        return records

    @staticmethod
    def _identifier(value: str) -> str:
        if not isinstance(value,str) or not value or value in ('.','..') or '/' in value or '\\' in value or '\x00' in value:
            raise HarnessError('ID должен быть одним непустым компонентом пути')
        return value

    def _git(self, cwd: Path, *args: str, env: dict | None = None) -> str:
        execution_env = dict(os.environ) if env is None else env
        try:
            r = subprocess.run(['git', '-C', str(cwd), *args], env=execution_env,
                               capture_output=True, text=True, timeout=self.cfg['limits']['git_seconds'])
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise HarnessError(f'Git не завершил операцию {args[0]}: {exc}') from exc
        if r.returncode:
            raise HarnessError(f"Git {args[0]}: {r.stderr[-self.cfg['limits']['preview_chars']:]}")
        return r.stdout.strip() if '-z' not in args else r.stdout

    def _tree(self, worktree: Path) -> str:
        """Временный индекс включает HEAD, staged, unstaged, untracked; реальный индекс не меняется."""
        self.runtime.mkdir(parents=True, exist_ok=True)
        index = descendant(self.runtime, self.paths['git_index'])
        if index.exists(): index.unlink()
        env = {**os.environ, 'GIT_INDEX_FILE': str(index)}
        try:
            self._git(worktree, 'read-tree', 'HEAD', env=env)
            self._git(worktree, 'add', '--all', env=env)
            return self._git(worktree, 'write-tree', env=env)
        finally:
            if index.exists(): index.unlink()

    def _changed(self, data: dict, tree: str) -> list[str]:
        result = self._git(Path(data['worktree']), 'diff', '--name-only', '--no-renames', '-z', data['entry_tree'], tree)
        return [s for s in result.split('\0') if s]

    def _task(self) -> dict:
        task = self.store.current(self.session)
        if task is None:
            raise HarnessError('Нет текущей задачи; сначала bootstrap с явной задачей')
        if task['config_hash'] != self.config_hash:
            raise HarnessError('Конфигурация изменена во время задачи; этот срез не меняет её контракт автоматически')
        return task

    def _stage(self, task: dict) -> dict:
        return task['process']['stages'][task['stage_index']]

    def _roots(self, task: dict) -> dict[str,Path]:
        roots = {'runtime': self.runtime,
                 'task': descendant(self.state, self.paths['tasks']) / task['id']}
        if task['sprint_id'] is not None:
            roots['sprint'] = descendant(self.state, self.paths['sprints']) / task['sprint_id']
        for path in roots.values():
            if path.is_symlink():
                raise HarnessError('Корень владельца артефактов не может быть symlink')
            path.mkdir(parents=True, exist_ok=True)
        return roots

    def _context(self, data: dict, prepare: bool) -> dict:
        stage = self._stage(data)
        task_root = descendant(self.state, self.paths['tasks']) / data['id']
        sprint_root = None if data['sprint_id'] is None else descendant(self.state, self.paths['sprints']) / data['sprint_id']
        content = self.task_commands.content_context(data['id'])
        workflow = self.runner.context(data['id'])
        payload = None
        if prepare:
            self._roots(data)
            payload = self.task_queries.latest_submission(data['id'],stage['id'],data['iteration'])
            if payload is None:
                payload = {'sections':stage['sections'].copy(),'artifact_paths':[], 'commit_message':'',
                    'content_additions':{'sections':[],'routes':[],'requirements':[]},'trace':{},'method_additions':[],
                    'stage_work':workflow['stage_work_template'],'evidence_work':{'phase':'prepare','arguments':[],'decisions':[]}}
                for section in content['sections']:
                    if section['writable']:
                        name=section['id']
                        payload['sections'][name]=(content['sections_current'][name] if name in content['sections_current'] else section['template'])
        return {'session': self.session, 'task': data['id'], 'goal': data['goal'], 'status': data['status'],
                'stage': stage['id'], 'iteration': data['iteration'], 'worktree': data['worktree'],
                'instruction': stage['instruction'], 'requirements': data['contract']['requirements'],
                'definition_of_done': data['contract']['definition_of_done'],
                'action':self.plan_actions.snapshot(data), 'content_requirements':content, 'handler':workflow['handler'], 'workflow':workflow,
                'runtime_root': str(self.runtime), 'task_root': str(task_root),
                'sprint_root': None if sprint_root is None else str(sprint_root),
                'result_template': payload,
                'agents_files': [str(p) for p in [Path(data['worktree']) / 'AGENTS.md'] if p.is_file()],
                'next_work': 'заполнить результат этапа и вызвать verify' if prepare else 'доложить; ждать решения пользователя'}

    def _validate_task(self, task: dict, process: dict) -> dict:
        from .modules.tasks.definition import validate_creation
        validate_creation(task,process,self.cfg['automatic_checks'])
        return process

    def _creation_base(self, base_revision=None):
        repository=Path(self.cfg['git']['repository']).resolve(strict=True)
        reference=self.cfg['git']['base_ref'] if base_revision is None else base_revision
        return self._git(repository,'rev-parse','--verify',reference+'^{commit}')

    def _execution_reservation(self, task_id, base):
        branch=self.cfg['git']['branch_template'].format(task_id=task_id,session_id=self.session)
        worktree=descendant(self.state,self.paths['worktrees'])/task_id
        pending={'kind':'worktree_setup','worktree':str(worktree),'branch':branch,'base':base}
        return {'worktree':str(worktree),'branch':branch,'base':base,'attempts':0,
                'publication':None,'pending':pending,'entry_tree':None,'last_report':None}

    def _reconcile_task_worktree(self, data):
        pending=data['pending']
        if not isinstance(pending,dict) or pending.get('kind')!='worktree_setup':
            return
        expected={'kind':'worktree_setup','worktree':data['worktree'],
                  'branch':data['branch'],'base':data['base']}
        if pending!=expected:
            raise HarnessError(f"Task {data['id']} worktree recovery facts conflict")
        repository=Path(self.cfg['git']['repository']).resolve(strict=True)
        worktree=Path(data['worktree'])
        try:
            self._git(repository,'check-ref-format','--branch',data['branch'])
            if worktree.is_symlink() or (worktree.exists() and not worktree.is_dir()):
                raise HarnessError(
                    f"Task {data['id']} worktree recovery conflict; recorded path is not a directory"
                )
            if not worktree.exists():
                worktree.parent.mkdir(parents=True,exist_ok=True)
                self.store.event(self.session,data['id'],'bootstrap.worktree',
                    {'branch':data['branch'],'base':data['base'],'path':str(worktree)})
                try:
                    branch_head=self._git(repository,'rev-parse','--verify',
                                          f"refs/heads/{data['branch']}^{{commit}}")
                except HarnessError:
                    branch_head=None
                if branch_head is None:
                    self._git(repository,'worktree','add','-b',data['branch'],str(worktree),data['base'])
                elif branch_head==data['base']:
                    self._git(repository,'worktree','add',str(worktree),data['branch'])
                else:
                    raise HarnessError(
                        f"Task {data['id']} worktree recovery conflict; recorded branch changed"
                    )
            branch=self._git(worktree,'symbolic-ref','--short','HEAD')
            head=self._git(worktree,'rev-parse','HEAD')
            changed=self._git(worktree,'status','--porcelain')
            if branch!=data['branch'] or head!=data['base'] or changed:
                raise HarnessError(
                    f"Task {data['id']} worktree recovery conflict; recorded external state was changed"
                )
            tree=self._tree(worktree)
        except HarnessError as exc:
            if 'recovery conflict' in str(exc):
                raise
            raise HarnessError(
                f"Task {data['id']} worktree setup remains recoverable; repeat the same creation request: {exc}"
            ) from exc
        with self.store.unit_of_work() as uow:
            uow.execution.patch(data['id'],{'pending':None,'entry_tree':tree})

    def bootstrap(self, task: dict | None = None, decision: str | None = None,
                  feedback: str | None = None, rework_stage: str | None = None) -> dict:
        if rework_stage is not None and decision != 'rework':
            raise HarnessError('rework_stage требует явное решение rework')
        if task is not None and decision is not None:
            raise HarnessError('Выбор задачи и решение по текущему этапу — разные входы')
        current = self.store.current(self.session)
        allocation_receipt = None
        if task is not None and isinstance(task,dict) and set(task)=={'id'}:
            self._identifier(task['id'])
            if self.sprint_tools.known(task['id']):
                return self.sprint_tools.select(task['id'])
            selected=self.task_queries.record(task['id'])
            if selected is None:raise HarnessError('Неизвестный task/sprint ID')
            if selected['status']=='available':return self.sprint_tools.start(task['id'])
            task=deepcopy(selected['contract'])
        if task is not None:
            intent = deepcopy(task)
            automatic = isinstance(intent,dict) and set(intent)=={'request_id','task'}
            contract = deepcopy(intent['task'] if automatic else intent)
            existing = None if automatic or not isinstance(contract.get('id'),str) else self.task_queries.record(contract['id'])
            if existing is not None:
                if contract != existing['contract']:
                    raise HarnessError('Existing task contract is immutable; bootstrap is not an editor')
                if existing['status']=='available':return self.sprint_tools.start(existing['id'])
                selected_process = existing['process']
            else:
                if contract.get('goal_type') not in self.processes:
                    raise HarnessError('Неизвестный goal_type')
                selected_process = self.processes[contract['goal_type']]
            if current and current['status'] not in ('completed','cancelled'):
                same_automatic=(automatic and current.get('creation_request',{}).get('request_id')==intent.get('request_id'))
                if not same_automatic and current['id'] != contract.get('id'):
                    raise HarnessError('Сначала прекратить/передать текущую задачу')
            if existing is not None:
                data = existing
                if data['claimed_by'] not in (None, self.session):
                    raise HarnessError('Задача уже связана с другой сессией')
                if data['config_hash'] != self.config_hash:
                    raise HarnessError('Задача имеет другой контракт конфигурации')
                if data['claimed_by'] is None and data['status'] not in ('completed','cancelled'):
                    self.handoff_tools.resume(data)
                    data=self.task_queries.record(data['id'])
                self._reconcile_task_worktree(data)
                data=self.task_queries.record(data['id'])
            else:
                if contract.get('sprint_id') is not None:
                    raise HarnessError('Sprint task must be published through the Sprint API first')
                if not automatic and self.sprint_tools.known(contract['id']):raise HarnessError('Task/sprint ID collision')
                base=self._creation_base()
                allocation=self.task_commands.create(
                    intent,self.session,selected_process,self.cfg['automatic_checks'],
                    {'config_hash':self.config_hash},
                    lambda task_id:self._execution_reservation(task_id,base),
                    self.cfg.get('task_ids'))
                allocation_receipt=allocation.receipt()
                data=self.task_queries.record(allocation.task_id)
                if data['config_hash']!=self.config_hash:
                    raise HarnessError('Задача имеет другой контракт конфигурации')
                if allocation.replayed and data['claimed_by'] not in (None,self.session):
                    return {**self._context(data,data['status']=='active'),
                            'allocation':allocation_receipt}
                self._reconcile_task_worktree(data)
                data=self.task_queries.record(allocation.task_id)
            self.store.bind(self.session, data['id'])
            current = self.store.current(self.session)
        if decision is None and task is None and (current is None or current['status'] in ('completed','cancelled')):
            sprint=self.sprint_tools.overview(None)
            if sprint is not None:return sprint
        if current is None:
            self.runtime.mkdir(parents=True, exist_ok=True)
            self.store.bind(self.session, None)
            return {'session': self.session, 'status':'read_only', 'project':self.cfg['project'],
                    'runtime_root':str(self.runtime), 'next_work':'передать task object через work bootstrap; сводка — batch show'}
        data = self._task()
        if decision is not None:
            if decision not in ('continue','rework'):
                raise HarnessError('Решение должно быть continue или rework')
            entry_tree = self._tree(Path(data['worktree']))
            if decision == 'continue':
                state = self.runner.accept(data['id'], self.session, True, entry_tree)
                data = self._task()
                if state.status == 'completed':
                    self._cleanup_runtime()
                    return data['last_report']
            else:
                if data['status']=='active' and self._stage(data)['handler'] in ('apply_plan','publish'):
                    self.plan_actions.rework_failed(data,feedback,rework_stage,entry_tree)
                else:
                    self.runner.rework(data['id'], self.session, feedback, entry_tree, rework_stage)
                data = self._task()
            self._cleanup_runtime()
        result=self._context(data, data['status']=='active')
        return result if allocation_receipt is None else {**result,'allocation':allocation_receipt}

    def accept(self) -> dict:
        data = self._task()
        self.runner.accept(data['id'], self.session, False, None)
        data = self._task()
        self.store.event(self.session, data['id'], 'user.accept',
                         {'stage':self._stage(data)['id'],'status':data['status']})
        return data['last_report']

    def _cleanup_runtime(self):
        self.result_views.finish()
        if self.runtime.exists(): shutil.rmtree(self.runtime)

    def _select_checks(self, data: dict, changed: list[str]) -> list[dict]:
        stage_id = self._stage(data)['id']
        ids = list(data['contract']['checks'][stage_id])
        automatic = set()
        for mapping in self.cfg['automatic_checks']:
            if any(fnmatch.fnmatchcase(path,pattern) for path in changed for pattern in mapping['paths']):
                ids.extend(mapping['by_stage'][stage_id])
                automatic.update(mapping['by_stage'][stage_id])
        methods = {m['id']:m for m in data['contract']['methods']}
        missing = set(ids)-methods.keys()
        if missing: raise HarnessError(f'Неизвестные методы: {sorted(missing)}')
        # IDs сохраняют обязательства; идентичные исполнимые контракты запускаются один раз.
        grouped = {}
        subjects = set(data['contract']['evidence_plan'][stage_id]['subject_methods'])
        for method_id in ids:
            method = methods[method_id]
            execution = {k:v for k,v in method.items() if k!='id'}
            key = digest(execution)
            if key not in grouped: grouped[key] = {**method, 'obligations':[], 'guard':False, 'observation_rules':[]}
            if method_id not in grouped[key]['obligations']: grouped[key]['obligations'].append(method_id)
            grouped[key]['guard'] |= method_id not in subjects or method_id in automatic
            if method_id in subjects:
                grouped[key]['observation_rules'].append(data['contract']['evidence_plan'][stage_id]['subject_methods'][method_id])
        return list(grouped.values())

    def _candidate_artifacts(self, data: dict, submitted: list[str], roots: dict[str,Path]):
        owners = {'runtime':self.session, 'task':data['id']}
        if 'sprint' in roots: owners['sprint'] = data['sprint_id']
        records = inspect_paths(submitted, roots, owners)
        previous = self.store.artifact_records(data['id'])
        merged = {(r['scope'],r['path']):r for r in records}
        for prior in previous:
            old = dict(prior)
            if old['scope'] not in roots: continue
            current = inspect_paths([old['path']], roots, owners)[0]
            if current['digest'] != old['digest']:
                raise HarnessError(f"Артефакт изменён после регистрации: {old['path']}; используйте новый путь")
            merged[(old['scope'],old['path'])] = current
        return list(merged.values())

    @staticmethod
    def _artifact_facts(records: list[dict]) -> tuple[ArtifactFact, ...]:
        return tuple(ArtifactFact(r['id'],r['scope'],r['relative_path']) for r in records)

    def _content_gate(self, data: dict, phase: str, artifacts: list[dict]) -> dict:
        assessment = self.task_commands.assess_content(data['id'],phase,self._artifact_facts(artifacts))
        result = assessment.to_dict()
        self.store.event(self.session,data['id'],'content.gate',result)
        return result

    def _content_blocked(self, data: dict, gate: dict, receipts: list[dict]) -> dict:
        return {'status':'content_requirements_failed','task':data['id'], 'stage':self._stage(data)['id'],
                'content_gate':gate,'checks':receipts,'replayed':False,
                'context':self._context(data,True)}

    def verify(self, payload: dict | None) -> dict:
        try:
            result=self._verify(payload)
        finally:
            incidents=self.result_views.finish()
        if incidents:result={**result,'incidents':incidents}
        return result

    def _verify(self, payload: dict | None) -> dict:
        data = self.store.current(self.session)
        if data is None:
            self.store.event(self.session,None,'read_only.finalize',{})
            self._cleanup_runtime()
            return {'status':'read_only_verified','project':self.cfg['project'],'checks':[], 'artifacts':[]}
        data = self._task(); stage = self._stage(data); worktree = Path(data['worktree'])
        if data['claimed_by'] != self.session:
            raise HarnessError('Нет владения текущей работой')
        if self._git(worktree,'symbolic-ref','--short','HEAD') != data['branch']:
            raise HarnessError('В worktree другая ветка')
        if data['pending'] is not None:
            raise HarnessError('Неизвестен исход прерванной проверки; не запускаем повтор вслепую. Смотрите журнал.')
        tree = self._tree(worktree)
        if data['status'] == 'verified':
            if tree != data['last_report']['verified_tree']:
                raise HarnessError('Код изменён после доклада: сначала rework')
            self._cleanup_runtime()
            return {**data['last_report'],'replayed':True}
        if data['status'] != 'active':
            raise HarnessError('Нет активного этапа для verify')
        if payload is None:
            raise HarnessError('Активный verify требует result object; служебный файл не читается')
        payload = deepcopy(payload)
        self.plan_actions.validate(data,payload)
        submitted = self.runner.submit(data['id'], self.session, payload)
        data = self._task()
        changed = self._changed(data, tree)
        if stage['read_only'] and changed:
            raise HarnessError(f'read-only этап изменил репозиторий: {changed}')
        outside = [p for p in changed if not any(fnmatch.fnmatchcase(p,pattern) for pattern in stage['allowed_paths'])]
        if outside: raise HarnessError(f'Изменения вне разрешённой области этапа: {outside}')
        if changed and (not isinstance(payload['commit_message'],str) or not re.fullmatch(self.cfg['git']['commit_pattern'],payload['commit_message'])):
            raise HarnessError('Сообщение коммита не соответствует правилу проекта')
        roots = self._roots(data)
        artifacts = self._candidate_artifacts(data, payload['artifact_paths'], roots)
        check_counts(artifacts, stage['artifact_requirements'])
        if self.runner.context(data['id'])['terminal']:
            check_counts(artifacts, data['contract']['artifact_requirements'])
        gate = self._content_gate(data,'pre',artifacts)
        if not gate['passed']:
            return self._content_blocked(data,gate,[])
        action = None
        if stage['handler']=='apply_plan':
            action=self.plan_actions.apply(data,payload)
            if action['status']!='complete':
                return self._action_incomplete(data,payload,action)
            tree=self._tree(worktree)
            changed=self._changed(data,tree)
            outside=[p for p in changed if not any(fnmatch.fnmatchcase(p,pattern) for pattern in stage['allowed_paths'])]
            if outside:raise HarnessError(f'Plan changed files outside its declared stage scope: {outside}')
            self.plan_actions.commands.record_assessment(data['id'],self.session,tree,action)
            data=self._task()
        checks = self._select_checks(data, changed)
        invocations = self._invocations(checks, worktree, require_source=True)
        execution_key = digest({'stage':stage['id'],'iteration':data['iteration'],'tree':tree,
                                'invocations':invocations})
        # Env values participate only in the digest; they are not persisted in receipts.
        batch = self.task_commands.observation_batch(data['id'],tree,execution_key)
        usable = batch is not None and self._usable_receipts(batch['receipts'])
        if payload['evidence_work']['phase']=='continue' and not usable:
            return {'status':'observations_stale','task':data['id'],'stage':stage['id'],
                    'reason':'Нужен PREPARE: точные входы наблюдения изменились или receipt недоступен.',
                    'context':self._context(data,True)}
        missing = self.task_commands.evidence_precheck(data['id'],self.session,tree,execution_key)
        if missing:
            return {'status':'evidence_requirements_failed','task':data['id'],'stage':stage['id'],
                    'missing':list(missing),'checks':[],'context':self._context(data,True)}
        payload_hash = submitted.digest
        publication = data['publication']
        if (publication is not None and publication['tree']==tree and publication['payload_hash']==payload_hash
                and publication['execution_key']==execution_key and usable):
            return self._publish(data, publication)
        if usable:
            receipts = batch['receipts']
            attempt = data['attempts']
        else:
            if data['attempts'] >= self.cfg['limits']['verify_attempts']:
                raise HarnessError('Достигнут явный лимит verify_attempts; требуется решение пользователя')
            data['attempts'] += 1
            attempt = data['attempts']
            data['pending'] = 'checks'
            data['publication'] = None
            self.store.save(data)
            self.store.event(self.session,data['id'],'verify.start',{'stage':stage['id'],'tree':tree,'attempt':attempt,'methods':[m['id'] for m in checks]})
            try:
                receipts = self._execute_checks(data,stage,tree,checks,invocations,roots)
            except Exception as exc:
                data['pending'] = None
                self.store.save(data)
                self.store.event(self.session,data['id'],'verify.error',{'message':str(exc)})
                raise
            data['pending'] = None
            self.store.save(data)
            self.runner.record_observations(data['id'],self.session,tree,execution_key,receipts)
            data = self._task()
        if not self._usable_receipts(receipts):
            return {'status':'checks_failed','task':data['id'],'stage':stage['id'],'attempt':attempt,'checks':receipts,'replayed':False}
        if self._tree(worktree) != tree:
            raise HarnessError('Наблюдения изменили проверяемое дерево; сначала согласуйте фактическое состояние')
        assessment = self.runner.assess_evidence(data['id'],self.session,tree,execution_key)
        data = self._task()
        if not assessment['ready']:
            if assessment['needs_continuation']:
                payload['evidence_work']['phase']='continue'
            context=self._context(data,True)
            context['result_template']=deepcopy(payload)
            return {'status':'awaiting_continuation' if assessment['needs_continuation'] else 'evidence_requirements_failed',
                    'task':data['id'],'stage':stage['id'],'checks':receipts,'evidence_assessment':assessment,
                    'context':context, 'next_work':'Дополнить evidence_work и вызвать verify в той же итерации.'}
        if self._tree(worktree) != tree:
            raise HarnessError('Проверки изменили дерево; требуется verify фактического нового состояния')
        artifacts = self._candidate_artifacts(data, payload['artifact_paths'], roots)
        gate = self._content_gate(data,'post',artifacts)
        if not gate['passed']:
            return self._content_blocked(data,gate,receipts)
        if stage['handler']=='publish':
            from .modules.workflow.domain import RouteDefinition
            if RouteDefinition.from_process(data['process']).node(stage['id']).target('complete') is None:
                check_counts(artifacts,data['contract']['artifact_requirements'])
            action=self.plan_actions.publish(data,payload)
            if action['status']!='complete':
                return self._action_incomplete(data,payload,action)
            self.plan_actions.commands.record_assessment(data['id'],self.session,tree,action)
            data=self._task()
        publication = {'tree':tree,'payload_hash':payload_hash,'execution_key':execution_key,'checks':receipts,'artifacts':artifacts,
                       'attempt':attempt,'commit_message':payload['commit_message'],'changed':changed,'commit':None}
        if action is not None and 'allocations' in action:
            publication['allocations']=action['allocations']
        data['publication'] = publication; self.store.save(data)
        return self._publish(data, publication)

    def _action_incomplete(self,data,payload,action):
        context=self._context(self._task(),True)
        draft=deepcopy(payload)
        if action['status']=='awaiting_resolution':
            draft['stage_work']['phase']='continue'
        context['result_template']=draft
        status={'awaiting_resolution':'awaiting_action_continuation','failed':'action_failed','blocked':'action_blocked','prepared':'action_blocked','running':'action_blocked'}[action['status']]
        return {'status':status,'task':data['id'],'stage':self._stage(data)['id'],
                'action':action,'context':context,'next_work':'Resolve the reported action state; do not repeat external effects manually.'}

    def _invocations(self, checks, worktree, *, require_source=False):
        invocations=[]
        for method in checks:
            self.result_views.policy.select(method['argv'])
            cwd=(worktree/method['cwd']).resolve()
            if not cwd.is_relative_to(worktree.resolve()) or not cwd.is_dir():
                raise HarnessError('cwd проверки должен находиться в текущем worktree')
            env={}
            for name in self.cfg['environment_names']:
                if name not in os.environ:
                    raise HarnessError(f'Требуемая переменная среды отсутствует: {name}')
                env[name]=os.environ[name]
            env.update(method['environment'])
            invocation={'method':method,'cwd':str(cwd),'environment':env}
            if require_source:
                provenance=resolve_source_under_test(
                    method, worktree=worktree, cwd=cwd, environment=env
                )
                invocation['source_provenance']=provenance
                invocation['provenance_digest']=digest(provenance)
                invocation['expectation_digest']=digest({
                    key:method[key]
                    for key in ('expected_exit_code','stdout_contains','stderr_contains')
                })
            invocations.append(invocation)
        return invocations

    def _usable_receipts(self, receipts):
        for r in receipts:
            if not r['interpretable'] or r['timed_out'] or r['actual_exit_code'] is None or r['actual_exit_code'] < 0 or (r['guard'] and not r['passed']):
                return False
            for name in ('stdout','stderr'):
                path=Path(r[name])
                if not path.is_file() or file_digest(path)!=r[name+'_digest']:
                    return False
        return True

    def _execute_checks(self, data, stage, tree, checks, invocations, roots):
        receipts=[]
        for method,invocation in zip(checks,invocations,strict=True):
            run_id=str(uuid.uuid4())
            run_dir=descendant(roots['task'],self.paths['runs'])/run_id
            result=run_command(method['argv'],Path(invocation['cwd']),invocation['environment'],method['timeout_seconds'],
                               descendant(run_dir,self.paths['stdout']),descendant(run_dir,self.paths['stderr']))
            passed=(not result['timed_out'] and result['actual_exit_code']==method['expected_exit_code'] and
                    all(contains(Path(result['stdout']),t) for t in method['stdout_contains']) and
                    all(contains(Path(result['stderr']),t) for t in method['stderr_contains']))
            interpretable=(not result['timed_out'] and result['actual_exit_code'] is not None and result['actual_exit_code']>=0 and
                           all(result['actual_exit_code'] in rule['exit_codes'] and
                               all(contains(Path(result['stdout']),t) for t in rule['stdout_contains']) and
                               all(contains(Path(result['stderr']),t) for t in rule['stderr_contains'])
                               for rule in method['observation_rules']))
            receipt={**result,'interpretable':interpretable,'id':run_id,'method':method['id'],'obligations':method['obligations'],'guard':method['guard'],
                     'argv':method['argv'],'cwd':invocation['cwd'],'expected_exit_code':method['expected_exit_code'],
                     'passed':passed,'tree':tree,'stdout_digest':file_digest(Path(result['stdout'])),
                     'stderr_digest':file_digest(Path(result['stderr'])),
                     'preview':preview(Path(result['stderr']),self.cfg['limits']['preview_chars'])}
            for field in ('expectation_digest','provenance_digest','source_provenance'):
                receipt[field]=invocation[field]
            presentation=self.result_views.capture(receipt,run_dir)
            receipt['presentation']={k:v for k,v in presentation.items() if k!='status'}
            self.evidence_commands.record_receipt(data['id'],self.session,stage['id'],data['iteration'],receipt)
            receipts.append(receipt)
            self.store.event(self.session,data['id'],'check.finished',{'run':run_id,'passed':passed,'exit':result['actual_exit_code']})
        return receipts

    def _publish(self, data: dict, publication: dict) -> dict:
        worktree = Path(data['worktree']); tree = publication['tree']; stage = self._stage(data)
        if self._tree(worktree) != tree:
            raise HarnessError('Дерево изменилось перед публикацией; старое evidence не принимается')
        current_artifacts = self._candidate_artifacts(data, [r['path'] for r in publication['artifacts']], self._roots(data))
        for phase in ('pre','post'):
            gate = self._content_gate(data,phase,current_artifacts)
            if not gate['passed']:
                return self._content_blocked(data,gate,publication['checks'])
        publication['artifacts'] = current_artifacts
        sha = self._git(worktree,'rev-parse','HEAD')
        pending_merge = self.plan_actions._read_optional_ref(worktree,'MERGE_HEAD') is not None
        if publication['changed'] or pending_merge:
            if self._git(worktree,'rev-parse','HEAD^{tree}') != tree or pending_merge:
                self._git(worktree,'add','--all')
                if self._git(worktree,'write-tree') != tree:
                    raise HarnessError('Индекс не равен проверенному дереву')
                actor = {'GIT_AUTHOR_NAME':self.cfg['git']['author_name'],
                         'GIT_AUTHOR_EMAIL':self.cfg['git']['author_email'],
                         'GIT_COMMITTER_NAME':self.cfg['git']['author_name'],
                         'GIT_COMMITTER_EMAIL':self.cfg['git']['author_email']}
                self._git(worktree,'commit','-m',publication['commit_message'],env={**os.environ,**actor})
                sha = self._git(worktree,'rev-parse','HEAD')
            publication['commit'] = sha; data['publication'] = publication; self.store.save(data)
            if self._git(worktree,'rev-parse','HEAD^{tree}') != tree or self._tree(worktree) != tree:
                self.store.event(self.session,data['id'],'incident.tree_changed_during_commit',{'commit':sha,'tested_tree':tree})
                raise HarnessError('Коммит/hook изменил проверенное дерево; push не выполнен')
            if self.cfg['git']['push_required']:
                self._git(worktree,'push',self.cfg['git']['remote'],f"HEAD:refs/heads/{data['branch']}")
                published = self._git(worktree,'ls-remote',self.cfg['git']['remote'],f"refs/heads/{data['branch']}")
                if not published or published.split()[0] != sha:
                    raise HarnessError('Remote не подтвердил опубликованный commit')
        # Только task/sprint links переживают cleanup. Runtime пути остаются временными.
        permanent = [r for r in publication['artifacts'] if r['scope']!='runtime']
        self.store.link_artifacts(data['id'], permanent)
        workflow = self.runner.context(data['id'])
        if workflow['terminal']:
            check_counts(current_artifacts,data['contract']['artifact_requirements'])
        self.result_views.finish()
        report = {'action':self.plan_actions.snapshot(data), 'evidence':workflow['evidence'], 'handler':workflow['handler'], 'stage_outcome':workflow['outcome'], 'next_stage':workflow['next_stage'],
                  'feedback':workflow['feedback'], 'status':'verified','task':data['id'],'stage':stage['id'],'iteration':data['iteration'],
                  'attempt':publication['attempt'],'commit':sha,'verified_tree':tree,'checks':publication['checks'],
                  'artifacts':[{'id':r['id'],'path':r['path']} for r in permanent],
                  'replayed':False,'next_work':'доложить пользователю; следующий этап не начинать'}
        if 'allocations' in publication:report['allocations']=publication['allocations']
        if self.result_views.incidents:report['incidents']=list(self.result_views.incidents)
        self.runner.verified(data['id'], self.session, publication['payload_hash'], report,
                                         self._artifact_facts(publication['artifacts']))
        self.store.event(self.session,data['id'],'stage.verified',{'commit':sha,'tree':tree})
        self._cleanup_runtime()
        return report

    def cancel(self, reason: str) -> dict:
        if not isinstance(reason,str) or not reason.strip(): raise HarnessError('Нужна инструкция пользователя об отмене')
        data = self._task()
        if data['pending'] is not None: raise HarnessError('Сначала установить исход незавершённой операции')
        self.task_commands.cancel(data['id'], self.session, reason)
        self.store.event(self.session,data['id'],'task.cancelled',{'reason':reason,'worktree_preserved':True})
        self._cleanup_runtime()
        return {'status':'cancelled','task':data['id'],'worktree_preserved':True}

    def show(self) -> dict:
        data = self.store.current(self.session)
        if data is None:
            return {'status':'read_only','project':self.cfg['project'],'tasks':self.task_queries.summary()}
        submissions, evidence = self.store.counts(data['id'])
        return {'task':data['id'],'status':data['status'],'stage':self._stage(data)['id'],
                'iteration':data['iteration'],'attempts':data['attempts'],
                'submission_count':submissions,'evidence_count':evidence,
                'history':self.task_queries.history(data['id']), 'workflow':self.runner.context(data['id']), 'token_usage':'unavailable'}

    def show_section(self, name: str, stage: str | None, submission: int | None) -> dict:
        data = self._task()
        stage_id = self._stage(data)['id'] if stage is None else stage
        return {'status':'read_only', **self.task_queries.section(data['id'],stage_id,name,submission)}

    def show_content(self) -> dict:
        data = self._task()
        return {'status':'read_only','task':data['id'],'stage':self._stage(data)['id'],
                'iteration':data['iteration'], **self.task_queries.content(data['id'])}

    def show_trace(self, route: str, point: str, submission: int | None) -> dict:
        data = self._task()
        return {'status':'read_only', **self.task_queries.trace_point(data['id'],route,point,submission)}

    def show_evidence(self):
        data=self._task()
        return {'status':'read_only','task':data['id'],**self.task_queries.evidence_view(data['id'])}

    def show_output(self,receipt_id,representation,requested):
        from .modules.work.domain import read_range
        data=self._task()
        records=self.evidence_commands.list_for(data['id'])
        receipt=next((x for x in records if x['id']==receipt_id),None)
        if receipt is None or 'presentation' not in receipt:
            raise HarnessError('No command presentation for current task and receipt')
        self.result_views.finish()
        root=self._roots(data)['task'].resolve()
        if receipt['presentation']['manifest'] is None:raise HarnessError('View storage unavailable; original command output remains in receipt')
        manifest=Path(self.task_queries.resolve_path(data['id'],receipt['presentation']['manifest'])).resolve()
        if not manifest.is_relative_to(root):raise HarnessError('Presentation outside current task')
        state=read_json(manifest)
        if state['status']!='ready':raise HarnessError('Presentation is not ready; see recorded parser incident')
        if representation not in state['representations']:raise HarnessError('Unknown representation')
        item=state['representations'][representation]
        path=Path(self.task_queries.resolve_path(data['id'],item['path'])).resolve()
        if not path.is_relative_to(root) or file_digest(path)!=item['digest']:raise HarnessError('Presentation changed')
        # Explicit range selection is bounded by the existing initial read policy.
        from .infrastructure.result_views import read_output_range
        return read_output_range(path,item,requested,self.cfg['batch']['initial_read_lines'])
