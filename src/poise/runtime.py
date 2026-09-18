from __future__ import annotations
from copy import deepcopy
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
from .common import PoiseError, descendant, configured_root, configured_storage_path, digest, encoded, exact_keys, load_config, read_json, validate_method, file_digest, prohibit_git_push
from .storage import Store
from .composition import task_tools
from .application.runner import StageRunner
from .application.check_attempts import is_check_attempt, receipt_matches
from .application.evidence import EvidenceCommands
from .modules.content_requirements.domain import ArtifactFact
from .modules.evidence.domain import completed_receipts
from .modules.foundation.paths import matches_allowed_path
from .modules.tasks.domain import is_terminal_task_status
from .artifacts import inspect_paths, check_counts
from .execution import RegisteredCheckRunner, RunnerTimeoutPolicy, capture_declared_outputs, run_command, contains, method_passed, preview, timeout_profile
from .infrastructure.task_paths import sprint_root, task_root


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
        raise PoiseError(
            'Нельзя выполнить проверку: для provenance исходников требуется source_under_test'
        )
    if source['kind'] == 'external':
        return deepcopy(source)
    root = worktree.resolve()
    facts = []
    for binding in source['bindings']:
        resolved = (root / binding['path']).resolve()
        if not resolved.is_relative_to(root) or not resolved.is_dir():
            raise PoiseError(
                f"Не удалось подтвердить repository source provenance: {binding['path']}"
            )
        fact = {**binding, 'resolved_path': str(resolved)}
        if binding['kind'] == 'cwd':
            if resolved != cwd.resolve():
                raise PoiseError(
                    'Source provenance не подтверждена: cwd binding не совпадает с cwd команды'
                )
        else:
            if binding['name'] in environment:
                raise PoiseError(
                    f"Source provenance конфликтует с environment: {binding['name']}"
                )
            environment[binding['name']] = str(resolved)
        facts.append(fact)
    return {'kind': 'repository', 'bindings': facts}


class Poise:
    """Одна сессия, одна текущая задача; переход этапа только по решению пользователя."""
    def __init__(self, config_path: Path | str, session: str, clock,
                 legacy_process_requirements: dict[str, bool] | None = None,
                 liveness=None):
        self.config_path = Path(config_path).resolve()
        self.root, self.cfg, self.processes = load_config(
            self.config_path, legacy_process_requirements
        )
        self.development_routing = None
        if 'development_routing' in self.cfg:
            from .infrastructure.development_routing import load_development_routing
            self.development_routing = load_development_routing(**{
                name: (self.root / value).resolve()
                for name, value in self.cfg['development_routing'].items()
            })
        self.session = self._identifier(session)
        self.state = configured_root(self.root, self.cfg['paths']['state'])
        self.paths = self.cfg['paths']
        self.runtime = descendant(self.state, self.paths['runtime']) / self.session
        if self.runtime.is_symlink():
            raise PoiseError('Корень сессии не может быть symlink')
        self.context_reader = None
        if 'source_reader' in self.cfg:
            from .infrastructure.source_reader import FileSourceReader
            reader_config = self.cfg['source_reader']
            self.context_reader = FileSourceReader(
                descendant(self.runtime, reader_config['receipt_file']),
                read_json((self.root / reader_config['policy']).resolve()),
            )
        self.config_hash = digest(self.cfg)
        limits = self.cfg['limits']
        self.store = Store(descendant(self.state, self.paths['database']),
                           descendant(self.state, self.paths['lock']),
                           limits['lock_seconds'], limits['lock_poll_seconds'], self.processes)
        from .infrastructure.repository_tree import GitRepositoryTree
        repository_tree = GitRepositoryTree(
            self.cfg['git']['repository'], limits['git_seconds'], limits['preview_chars']
        )
        from .modules.requirements_registry.service import TaskRequirementsGate
        from .application.requirements_registry import RequirementsCommands
        from .infrastructure.requirements_registry import (
            RequirementsStore,
            TaskRequirementsMetadataStore,
        )
        self.requirements_store = RequirementsStore(
            configured_storage_path(self.state, self.paths['requirements_database']),
            configured_storage_path(self.state, self.paths['requirements_lock']),
            limits['lock_seconds'],
            limits['lock_poll_seconds'],
        )
        self.requirements_commands = RequirementsCommands(
            self.requirements_store,
            self.cfg['batch']['max_items'],
        )
        requirements_gate = TaskRequirementsGate.enabled(
            self.requirements_store.registry,
            TaskRequirementsMetadataStore(self.store.unit_of_work),
        )
        self.task_commands, self.task_queries = task_tools(
            self.store,
            repository_tree,
            requirements_gate,
        )
        from .application.ownership import BoundOwnership, OwnershipCommands
        from .modules.ownership.domain import UnobservedSessionLiveness
        ownership_commands = OwnershipCommands(
            self.store.unit_of_work,
            UnobservedSessionLiveness() if liveness is None else liveness,
            lambda observed: None,
        )
        self.ownership = BoundOwnership(ownership_commands, self.session)
        self.runner = StageRunner(self.task_commands)
        self.evidence_commands = EvidenceCommands(self.store.unit_of_work)
        from .infrastructure.sqlite.interactions import InteractionStore
        from .infrastructure.work import WorkResources
        self.interactions=InteractionStore(self.store.database,self.cfg['project'],self.cfg['batch'])
        self.work_resources=WorkResources(self)
        from .application.accounting import AccountingCommands
        from .infrastructure.accounting import RuntimeAccounting
        from .infrastructure.telemetry import TelemetryDatabase
        telemetry_storage=self.cfg['accounting']['storage']
        telemetry_database=TelemetryDatabase(
            descendant(self.state,telemetry_storage['database']),
            descendant(self.state,telemetry_storage['lock']),
            limits['lock_seconds'],
            limits['lock_poll_seconds'],
        )
        self.accounting=AccountingCommands(RuntimeAccounting(self,telemetry_database))
        from .application.telemetry import OptionalTelemetry
        from .infrastructure.telemetry import AsyncTelemetryDispatcher,DetachedTelemetryProcessor
        self.telemetry_delivery=None
        process_telemetry=self.accounting.port.process
        if 'telemetry_delivery' in self.cfg:
            from .infrastructure.telemetry_spool import TelemetrySpool
            delivery=self.cfg['telemetry_delivery']
            self.telemetry_delivery=TelemetrySpool(
                self.state / delivery['directory'], delivery['policy'], process_telemetry)
            process_telemetry=self.telemetry_delivery
        self.telemetry=OptionalTelemetry(
            clock,
            AsyncTelemetryDispatcher(
                DetachedTelemetryProcessor(process_telemetry),
                max_pending=self.cfg['batch']['max_items'],
            ),
            self.session,
        )
        from .infrastructure.sprint_work import SprintWork
        self.sprint_tools=SprintWork(self)
        from .infrastructure.actions import RuntimePlanActions
        self.plan_actions=RuntimePlanActions(self)
        from .infrastructure.result_views import ResultViews
        self.result_views=ResultViews(self.cfg['runtime_services']['output'],self._result_event)
        self.check_runner=RegisteredCheckRunner(limits['preview_chars'])
        self.check_timeout_policy=RunnerTimeoutPolicy.parse(self.cfg['runtime_services']['check_runner'])
        from .infrastructure.handoff import LocalHandoff
        self.handoff_tools=LocalHandoff(self)
        from .application.transfers import TransferCommands
        from .infrastructure.transfers import RuntimeTransfers
        self.transfer_tools=TransferCommands(RuntimeTransfers(self),self.cfg['runtime_services']['transfer'])
        from .application.result_integration import ResultIntegrationCommands
        from .infrastructure.result_integration import RuntimeResultIntegration
        self.integration_tools=ResultIntegrationCommands(RuntimeResultIntegration(self))
        from .application.task_cleanup import TaskResourceCleanup
        from .infrastructure.task_cleanup import RuntimeTaskResourceCleanup
        self.cleanup_tools=TaskResourceCleanup(RuntimeTaskResourceCleanup(self))

    def _result_event(self,event,payload):
        current=self.current_task()
        self.store.event(self.session,None if current is None else current['id'],event,payload)

    def report_task(self,report):
        if isinstance(report.get('task'),str):return self.task_queries.record(report['task'])
        return self.current_task()

    def handoff(self,args):
        return self.handoff_tools.preserve(args)

    def initialize_stage_contracts(
        self, task_id, expected_version, request_id, contracts, reason, authorization
    ):
        return self.task_commands.initialize_stage_contracts(
            task_id,
            self.session,
            expected_version,
            request_id,
            contracts,
            reason,
            authorization,
        )

    def revise_stage_contract(
        self, task_id, expected_version, request_id, stage_id, contract, reason,
        authorization
    ):
        return self.task_commands.revise_stage_contract(
            task_id,
            self.session,
            expected_version,
            request_id,
            stage_id,
            contract,
            reason,
            authorization,
        )

    def stage_contract_context(self, task_id):
        return self.task_commands.stage_contract_context(task_id)

    def task_action(self, args):
        action = args['action']
        self._identifier(args['request_id'])
        self._identifier(args['task_id'])
        if action == 'create':
            if args['sprint_id'] is not None:
                self._identifier(args['sprint_id'])
            return self.task_commands.create_newborn(
                args['task_id'], args['sprint_id'], self.session, self.config_hash,
                args['request_id'],
            )
        if action == 'edit':
            return self.task_commands.edit_newborn(
                args['task_id'], self.session, args['expected_revision'], args['patch'],
                args['remove'], self.processes, self.config_hash, args['request_id'],
            )
        if action == 'ready':
            return self.task_commands.ready_newborn(
                args['task_id'], self.session, args['expected_revision'],
                self.cfg['automatic_checks'], self.cfg['task_decomposition'],
                self.config_hash, args['request_id'],
                self._creation_base,
            )
        if action == 'recover_cancelled':
            return self.task_commands.recover_cancelled(
                args['task_id'], self.session, args['expected_version'],
                args['request_id'], args['reason'], args['authorization'],
                lambda execution: self._validate_cancelled_worktree(args['task_id'], execution),
            )
        if action == 'restart':
            return self.task_commands.restart_newborn(
                args['task_id'], self.session, args['expected_version'],
                args['request_id'], args['reason'], args['authorization'],
            )
        raise PoiseError('Unknown Task action')

    def _validate_cancelled_worktree(self, task_id: str, execution: dict) -> None:
        """Read exact existing Git ownership; never checkout, reset or recreate WIP."""
        path, branch = execution['worktree'], execution['branch']
        if path is None and branch is None:
            return
        if not isinstance(path, str) or not isinstance(branch, str) or not branch:
            raise PoiseError('Cancelled Task worktree/branch identity is incomplete')
        worktree = Path(path)
        expected_root = descendant(self.state, self.paths['worktrees']) / task_id
        if worktree.resolve() != expected_root.resolve():
            raise PoiseError('Cancelled Task worktree is outside its exact registered root')
        if worktree.is_symlink() or not worktree.is_dir():
            raise PoiseError('Cancelled Task worktree is missing or ambiguous')
        repository = Path(self.cfg['git']['repository']).resolve(strict=True)
        observed = self._git(worktree, 'symbolic-ref', '--quiet', '--short', 'HEAD')
        expected = branch.removeprefix('refs/heads/')
        if observed != expected:
            raise PoiseError('Cancelled Task worktree is on a different branch')
        common = self._git(worktree, 'rev-parse', '--path-format=absolute', '--git-common-dir')
        owner = self._git(repository, 'rev-parse', '--path-format=absolute', '--git-common-dir')
        if Path(common).resolve() != Path(owner).resolve():
            raise PoiseError('Cancelled Task worktree belongs to a different repository')
        registered = self._git(repository, 'worktree', 'list', '--porcelain')
        entries = [item.splitlines() for item in registered.split('\n\n') if item]
        matches = [item for item in entries if item[0] == f'worktree {worktree.resolve()}']
        if len(matches) != 1 or f'branch refs/heads/{expected}' not in matches[0]:
            raise PoiseError('Cancelled Task worktree registration is ambiguous')

    def recover_empty_rework(self, task_id: str, reason: str) -> dict:
        return self._recover_empty_transition(task_id, reason, "rework")

    def recover_empty_advance(self, task_id: str, reason: str) -> dict:
        return self._recover_empty_transition(task_id, reason, "advance")

    def advance(self, request_id: str, task_id: str, target_stage: str) -> dict:
        self._identifier(task_id)
        data = self.task_queries.record(task_id)
        if data is None:
            raise PoiseError("Unknown progression Task")
        self.task_commands.reviewer_preflight(task_id, self.session)
        entry_tree = self._current_tree(data)
        outcome = self.task_commands.advance_progression(
            task_id,
            self.session,
            request_id,
            target_stage,
            entry_tree,
            self._artifact_facts(self._existing_artifacts(data)),
        )
        progression = outcome["progression"]
        if outcome["kind"] == "entry_blocked":
            step = outcome["step"]
            return self._entry_blocked(
                data,
                outcome["gate"],
                stage_id=step.next_stage,
                blocked_transition={
                    "from_stage": step.current_stage,
                    "to_stage": step.next_stage,
                },
                progression=progression,
            )
        current = self._task()
        if outcome["kind"] == "role_handoff_required":
            step = outcome["step"]
            return {
                **self._context(current, False),
                "status": "role_handoff_required",
                "progression": progression,
                "next_stage": step.next_stage,
                "from_role": step.from_role,
                "to_role": step.to_role,
                "review_identity": outcome["review_identity"],
                "next_work": (
                    "Save the result, complete public handoff, then directly notify "
                    "the named counterpart with this progression target."
                ),
            }
        if outcome["kind"] == "user_acceptance_required":
            step = outcome["step"]
            return {
                **self._context(current, False),
                "status": "user_acceptance_required",
                "progression": progression,
                "next_stage": step.next_stage,
                "next_work": (
                    "Obtain the separately controlled user acceptance before entering publish."
                ),
            }
        status = (
            "progression_target_reached"
            if outcome["kind"] == "target_reached"
            else "progression_work_required"
        )
        return {
            **self._context(current, current["status"] == "active"),
            "status": status,
            "progression": progression,
            "target_stage": target_stage,
            "next_work": (
                "Work on the current stage, verify it, then replay this exact advance request."
                if status == "progression_work_required"
                else "The requested stage is current; perform its work."
            ),
        }

    def recover_missing_worktree(self, task_id: str, reason: str) -> dict:
        self._identifier(task_id)
        if not isinstance(reason, str) or not reason.strip():
            raise PoiseError('Missing worktree recovery reason is required')
        current = self.current_task()
        if current is not None and current['id'] != task_id:
            raise PoiseError('Missing worktree recovery requires an idle or owning session')
        data = self.task_queries.record(task_id)
        if data is None:
            raise PoiseError('Unknown Task for missing worktree recovery')
        if is_terminal_task_status(data['status']):
            raise PoiseError('Terminal Task worktree must not be recovered')
        if data['status'] not in ('verified', 'accepted'):
            raise PoiseError('Only an unchanged verified Task worktree can be recovered')
        expected_worktree = descendant(self.state, self.paths['worktrees']) / task_id
        expected_branch = data['branch']
        if (
            data['worktree'] != str(expected_worktree)
            or not isinstance(expected_branch, str)
            or not expected_branch
        ):
            raise PoiseError('Stored Task worktree recovery identity is incompatible')
        if expected_worktree.is_symlink():
            raise PoiseError('Stored Task worktree recovery path must not be a symlink')
        report = data.get('last_report')
        commit = None if report is None else report.get('commit')
        verified_tree = None if report is None else report.get('verified_tree')
        if not isinstance(commit, str) or not isinstance(verified_tree, str):
            raise PoiseError('Stored Task verified commit/tree is missing')
        repository = Path(self.cfg['git']['repository']).resolve(strict=True)
        try:
            self._git(repository, 'check-ref-format', '--branch', expected_branch)
            resolved = self._git(repository, 'rev-parse', '--verify', f'{commit}^{{commit}}')
            commit_tree = self._git(repository, 'show', '-s', '--format=%T', commit)
            self._git(
                repository, 'merge-base', '--is-ancestor',
                commit, self.cfg['git']['base_ref'],
            )
        except PoiseError as exc:
            raise PoiseError(
                'Missing Task worktree commit is not integrated into the configured base'
            ) from exc
        if resolved != commit or commit_tree != verified_tree:
            raise PoiseError('Missing Task worktree commit does not match the verified tree')
        try:
            branch_head = self._git(
                repository, 'rev-parse', '--verify', f'refs/heads/{expected_branch}^{{commit}}'
            )
        except PoiseError:
            branch_head = None
        if branch_head not in (None, commit):
            raise PoiseError('Missing Task worktree branch points to another commit')
        replayed = expected_worktree.exists()
        created_worktree = False
        created_branch = False
        if replayed:
            if not expected_worktree.is_dir():
                raise PoiseError('Stored Task worktree recovery path is not a directory')
        try:
            if not replayed:
                expected_worktree.parent.mkdir(parents=True, exist_ok=True)
                if branch_head is None:
                    self._git(
                        repository, 'worktree', 'add', '-b', expected_branch,
                        str(expected_worktree), commit,
                    )
                    created_branch = True
                else:
                    self._git(
                        repository, 'worktree', 'add', str(expected_worktree), expected_branch
                    )
                created_worktree = True
            repository_common = self._git(repository, 'rev-parse', '--git-common-dir')
            worktree_common = self._git(expected_worktree, 'rev-parse', '--git-common-dir')
            repository_common = (
                Path(repository_common) if Path(repository_common).is_absolute()
                else repository / repository_common
            ).resolve(strict=True)
            worktree_common = (
                Path(worktree_common) if Path(worktree_common).is_absolute()
                else expected_worktree / worktree_common
            ).resolve(strict=True)
            registered = self._git(repository, 'worktree', 'list', '--porcelain')
            registered_paths = {
                Path(line.removeprefix('worktree ')).resolve(strict=True)
                for line in registered.splitlines() if line.startswith('worktree ')
            }
            if repository_common != worktree_common or expected_worktree not in registered_paths:
                raise PoiseError('Recovered path is not a worktree of the configured repository')
            if (
                self._git(expected_worktree, 'symbolic-ref', '--short', 'HEAD') != expected_branch
                or self._git(expected_worktree, 'rev-parse', 'HEAD') != commit
                or self._git(expected_worktree, 'status', '--porcelain')
                or self._tree(expected_worktree) != verified_tree
            ):
                raise PoiseError('Recovered Task worktree does not match the verified source')
            if not replayed:
                self.store.event(self.session, task_id, 'worktree.recovered', {
                    'reason': reason,
                    'branch': expected_branch,
                    'worktree': str(expected_worktree),
                    'commit': commit,
                    'tree': verified_tree,
                })
        except BaseException as exc:
            cleanup_errors = []
            if created_worktree:
                try:
                    self._git(repository, 'worktree', 'remove', '--force', str(expected_worktree))
                except BaseException as cleanup_exc:
                    cleanup_errors.append(str(cleanup_exc))
            if created_branch:
                try:
                    self._git(repository, 'branch', '-D', expected_branch)
                except BaseException as cleanup_exc:
                    cleanup_errors.append(str(cleanup_exc))
            if cleanup_errors:
                raise PoiseError(
                    'Missing worktree recovery failed and scoped cleanup was incomplete: '
                    + '; '.join(cleanup_errors)
                ) from exc
            raise
        return {
            'status': 'recovered',
            'task': task_id,
            'worktree': str(expected_worktree),
            'branch': expected_branch,
            'commit': commit,
            'tree': verified_tree,
            'replayed': replayed,
        }

    def _recover_empty_transition(
        self, task_id: str, reason: str, transition: str
    ) -> dict:
        self._identifier(task_id)
        if not isinstance(reason, str) or not reason.strip():
            raise PoiseError(f'Empty {transition} recovery reason is required')
        if self.current_task() is not None:
            raise PoiseError(f'Empty {transition} recovery requires an idle session')
        data = self.task_queries.record(task_id)
        if data is None:
            raise PoiseError(f'Unknown Task for empty {transition} recovery')
        if data['claimed_by'] is not None:
            raise PoiseError(f'Empty {transition} recovery requires released work')
        tree = self._empty_transition_recovery_tree(data)
        command = (
            self.task_commands.recover_empty_rework
            if transition == "rework"
            else self.task_commands.recover_empty_advance
        )
        return command(task_id, reason, tree)

    def _empty_transition_recovery_tree(self, data: dict) -> str:
        worktree = data['worktree']
        if worktree is None:
            return data['entry_tree']
        path = Path(worktree)
        if path.exists():
            return self._tree(path)

        report = data.get('last_report')
        commit = None if report is None else report.get('commit')
        verified_tree = None if report is None else report.get('verified_tree')
        if not isinstance(commit, str) or not isinstance(verified_tree, str):
            raise PoiseError(
                'Worktree восстановления отсутствует, а verified commit/tree не сохранены'
            )
        repository = Path(self.cfg['git']['repository'])
        try:
            resolved = self._git(
                repository, 'rev-parse', '--verify', f'{commit}^{{commit}}'
            )
            commit_tree = self._git(repository, 'show', '-s', '--format=%T', commit)
            self._git(
                repository,
                'merge-base',
                '--is-ancestor',
                commit,
                self.cfg['git']['base_ref'],
            )
        except PoiseError as exc:
            raise PoiseError(
                'Worktree восстановления отсутствует, а verified commit не подтверждён '
                'в текущем base'
            ) from exc
        if resolved != commit or commit_tree != verified_tree:
            raise PoiseError(
                'Worktree восстановления отсутствует, а verified commit не соответствует '
                'сохранённому tree'
            )
        return verified_tree

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


    def validate_verification_artifacts(self, paths, task):
        """Read-only preflight shared by public and direct verification."""
        return self._candidate_artifacts(task, paths, self._roots(task))

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
            raise PoiseError('ID должен быть одним непустым компонентом пути')
        return value

    def _git(self, cwd: Path, *args: str, env: dict | None = None) -> str:
        prohibit_git_push(['git', *args])
        execution_env = dict(os.environ) if env is None else env
        try:
            r = subprocess.run(['git', '-C', str(cwd), *args], env=execution_env,
                               capture_output=True, text=True, timeout=self.cfg['limits']['git_seconds'])
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise PoiseError(f'Git не завершил операцию {args[0]}: {exc}') from exc
        if r.returncode:
            raise PoiseError(f"Git {args[0]}: {r.stderr[-self.cfg['limits']['preview_chars']:]}")
        return r.stdout.strip() if '-z' not in args else r.stdout

    def _tree(self, worktree: Path) -> str:
        """Временный индекс включает HEAD, staged, unstaged, untracked; реальный индекс не меняется."""
        configured_index = descendant(self.runtime, self.paths['git_index'])
        # Session cleanup may overlap another invocation of this same session.
        # Its temporary index has an independent lifetime, outside that cache.
        self.runtime.parent.mkdir(parents=True, exist_ok=True)
        invocation = Path(tempfile.mkdtemp(
            prefix=f'{self.session}.{configured_index.name}.',
            dir=self.runtime.parent,
        ))
        index = invocation / 'index'
        env = {**os.environ, 'GIT_INDEX_FILE': str(index)}
        try:
            self._git(worktree, 'read-tree', 'HEAD', env=env)
            self._git(worktree, 'add', '--all', env=env)
            return self._git(worktree, 'write-tree', env=env)
        finally:
            try:
                shutil.rmtree(invocation)
            except FileNotFoundError:
                pass  # Only absence is idempotent; real I/O/lock errors propagate.

    def _changed(self, data: dict, tree: str) -> list[str]:
        if data['worktree'] is None:
            return []
        result = self._git(Path(data['worktree']), 'diff', '--name-only', '--no-renames', '-z', data['entry_tree'], tree)
        return [s for s in result.split('\0') if s]

    def _current_tree(self, data: dict) -> str:
        return data['entry_tree'] if data['worktree'] is None else self._tree(Path(data['worktree']))

    def _verification_workspace(self, data: dict) -> Path:
        return (Path(self.cfg['git']['repository']) if data['worktree'] is None
                else Path(data['worktree']))

    def _task(self) -> dict:
        task = self.store.current(self.session)
        if task is None:
            raise PoiseError('Нет текущей задачи; сначала bootstrap с явной задачей')
        return task

    def _stage(self, task: dict) -> dict:
        return task['process']['stages'][task['stage_index']]

    def _roots(self, task: dict) -> dict[str,Path]:
        roots = {'runtime': self.runtime,
                 'task': task_root(self.state, self.paths, task['id'], task['sprint_id'])}
        if task['sprint_id'] is not None:
            roots['sprint'] = sprint_root(self.state, self.paths, task['sprint_id'])
        for path in roots.values():
            if path.is_symlink():
                raise PoiseError('Корень владельца артефактов не может быть symlink')
            path.mkdir(parents=True, exist_ok=True)
        return roots

    def _context(self, data: dict, prepare: bool) -> dict:
        stage = self._stage(data)
        current_task_root = task_root(self.state, self.paths, data['id'], data['sprint_id'])
        current_sprint_root = None if data['sprint_id'] is None else sprint_root(self.state, self.paths, data['sprint_id'])
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
        context = {'session': self.session, 'task': data['id'], 'goal': data['goal'], 'status': data['status'],
                'version': data['version'],
                'stage': stage['id'], 'iteration': data['iteration'], 'worktree': data['worktree'],
                'instruction': stage['instruction'], 'requirements': data['contract']['requirements'],
                'definition_of_done': data['contract']['definition_of_done'],
                'action':self.plan_actions.snapshot(data), 'content_requirements':content, 'handler':workflow['handler'], 'workflow':workflow,
                'runtime_root': str(self.runtime), 'task_root': str(current_task_root),
                'sprint_root': None if current_sprint_root is None else str(current_sprint_root),
                'result_template': payload,
                'progression': data.get('progression'),
                'agents_files': [] if data['worktree'] is None else
                    [str(p) for p in [Path(data['worktree']) / 'AGENTS.md'] if p.is_file()],
                'next_work': 'заполнить результат этапа и вызвать verify' if prepare else 'доложить; ждать решения пользователя'}

        if self.development_routing is not None:
            context['development_route'] = self._development_route(data, [], persist=prepare)
        return context

    def _development_changes(self, data):
        directory = self._verification_workspace(data)
        revision = data['base']
        return sorted(set(
            p for p in (self._git(directory, 'diff', '--name-only', '--no-renames', '-z', revision, '--')
                        + self._git(directory, 'ls-files', '--others', '--exclude-standard', '-z')).split('\0')
            if p
        ))

    def _development_route(self, data, facts, *, persist):
        if self.development_routing is None:
            raise PoiseError('AI-poise development routing is not configured')
        directory = self._verification_workspace(data)
        changed = self._development_changes(data)
        stage = self._stage(data)
        selected = self._select_checks(data, changed)
        required = list(dict.fromkeys(m for check in selected for m in check['obligations']))
        request = {
            'checkout': str(directory), 'stage': stage['id'], 'handler': stage['handler'],
            'scope_paths': data['contract']['stage_contracts'][data['stage_index']]['allowed_paths'],
            'changed_paths': changed, 'facts': facts, 'required_methods': required,
            'registered_methods': [m['id'] for m in data['contract']['methods']],
            'result_contract': {'task': data['id'], 'stage': stage['id'], 'iteration': data['iteration'],
                                'required_sections': stage['required_sections'],
                                'sections': sorted(stage['sections'])},
        }
        # One replaceable derived route in the existing session directory, not a ledger.
        return self.development_routing.route(
            request, snapshot=self.runtime / 'development-routing.json' if persist else None
        )

    def refresh_development_route(self, facts):
        from .modules.skills.selection import strings
        strings(facts, 'route facts')
        if self.development_routing is None:
            raise PoiseError('AI-poise development routing is not configured')
        data = self._task()
        return {'status': 'read_only', 'development_route': self._development_route(data, facts, persist=True)}

    def context_metadata(self, facts):
        """Current read projection, never a second persisted Task/session snapshot."""
        task = self.current_task()
        if task is None:
            return {'session': self.session, 'task': None,
                    'development_route': {'status': 'not_applicable', 'reason': 'taskless'}}
        stage = None if task['status'] == 'newborn' else self._stage(task)
        context = {key: deepcopy(task.get(key)) for key in
                   ('id', 'version', 'status', 'goal', 'iteration', 'worktree', 'pending', 'progression')}
        context['stage'] = None if stage is None else stage['id']
        context['handler'] = None if stage is None else stage['handler']
        contract = task.get('contract') or {}
        for name in ('requirements', 'definition_of_done'):
            context[name] = deepcopy(contract.get(name, []))
        context['required_sections'] = [] if stage is None else stage['required_sections']
        route = {'status': 'not_configured'}
        if stage is None or is_terminal_task_status(task['status']):
            route = {'status': 'not_applicable', 'reason': task['status']}
        elif self.development_routing is not None:
            route = self._development_route(task, facts, persist=True)
        return {'session': self.session, 'task': context, 'development_route': route}

    def restore_context(self, **request):
        from .application.context_recovery import ContextRecovery
        return ContextRecovery(self, self.context_reader).restore(request)

    def _terminal_context(self, data: dict) -> dict:
        snapshot = self.task_queries.terminal_snapshot(data['id'])
        sid = snapshot['metadata'].get('sprint_id')
        return {**snapshot, 'session': self.session,
                'goal': snapshot['metadata'].get('goal'),
                'runtime_root': str(self.runtime),
                'task_root': str(task_root(self.state, self.paths, data['id'], sid)),
                'sprint_root': None if sid is None else str(sprint_root(self.state, self.paths, sid)),
                'next_work': 'Read historical records; no current-task binding or execution'}

    def _validate_task(self, task: dict, process: dict) -> dict:
        from .modules.tasks.definition import validate_creation
        validate_creation(
            task, process, self.cfg['automatic_checks'], self.cfg['task_decomposition']
        )
        return process

    def _creation_base(self):
        repository=Path(self.cfg['git']['repository']).resolve(strict=True)
        return self._git(repository,'rev-parse','--verify',self.cfg['git']['base_ref']+'^{commit}')

    def _execution_reservation(self, task_id, base, worktree_required=True):
        if not worktree_required:
            return {'worktree':None,'branch':None,'base':base,'attempts':0,
                    'publication':None,'pending':None,'entry_tree':base,'last_report':None}
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
            raise PoiseError(f"Task {data['id']} worktree recovery facts conflict")
        repository=Path(self.cfg['git']['repository']).resolve(strict=True)
        worktree=Path(data['worktree'])
        try:
            self._git(repository,'check-ref-format','--branch',data['branch'])
            if worktree.is_symlink() or (worktree.exists() and not worktree.is_dir()):
                raise PoiseError(
                    f"Task {data['id']} worktree recovery conflict; recorded path is not a directory"
                )
            if not worktree.exists():
                worktree.parent.mkdir(parents=True,exist_ok=True)
                self.store.event(self.session,data['id'],'bootstrap.worktree',
                    {'branch':data['branch'],'base':data['base'],'path':str(worktree)})
                try:
                    branch_head=self._git(repository,'rev-parse','--verify',
                                          f"refs/heads/{data['branch']}^{{commit}}")
                except PoiseError:
                    branch_head=None
                if branch_head is None:
                    self._git(repository,'worktree','add','-b',data['branch'],str(worktree),data['base'])
                elif branch_head==data['base']:
                    self._git(repository,'worktree','add',str(worktree),data['branch'])
                else:
                    raise PoiseError(
                        f"Task {data['id']} worktree recovery conflict; recorded branch changed"
                    )
            branch=self._git(worktree,'symbolic-ref','--short','HEAD')
            head=self._git(worktree,'rev-parse','HEAD')
            changed=self._git(worktree,'status','--porcelain')
            if branch!=data['branch'] or head!=data['base'] or changed:
                raise PoiseError(
                    f"Task {data['id']} worktree recovery conflict; recorded external state was changed"
                )
            tree=self._tree(worktree)
        except PoiseError as exc:
            if 'recovery conflict' in str(exc):
                raise
            raise PoiseError(
                f"Task {data['id']} worktree setup remains recoverable; repeat the same creation request: {exc}"
            ) from exc
        with self.store.unit_of_work() as uow:
            uow.execution.patch(data['id'],{'pending':None,'entry_tree':tree})

    def bootstrap(self, task: dict | None = None, decision: str | None = None,
                  feedback: str | None = None, rework_stage: str | None = None) -> dict:
        if rework_stage is not None and decision != 'rework':
            raise PoiseError('rework_stage требует явное решение rework')
        if task is not None and decision is not None:
            raise PoiseError('Выбор задачи и решение по текущему этапу — разные входы')
        current = self.store.current(self.session)
        allocation_receipt = None
        if task is not None and isinstance(task,dict) and set(task)=={'id'}:
            self._identifier(task['id'])
            if self.sprint_tools.known(task['id']):
                return self.sprint_tools.select(task['id'])
            selected=self.task_queries.record(task['id'])
            if selected is None:raise PoiseError('Неизвестный task/sprint ID')
            if selected['status']=='newborn':
                if selected['claimed_by'] is None and self.handoff_tools.commands.latest(selected['id']) is not None:
                    self.handoff_tools.resume(selected)
                self.ownership.acquire_task(selected['id'])
                return self.task_queries.record(selected['id'])
            if selected['status']=='available':
                blocked = self._require_entry(selected)
                return blocked if blocked is not None else self.sprint_tools.start(task['id'])
            if is_terminal_task_status(selected['status']):
                if current and not is_terminal_task_status(current['status']):
                    raise PoiseError('Сначала прекратить/передать текущую задачу')
                result = self._terminal_context(selected)
                return result
            task=deepcopy(selected['contract'])
        if task is not None:
            intent = deepcopy(task)
            automatic = isinstance(intent,dict) and set(intent)=={'request_id','task'}
            contract = deepcopy(intent['task'] if automatic else intent)
            existing = None if automatic or not isinstance(contract.get('id'),str) else self.task_queries.record(contract['id'])
            if existing is not None:
                self.task_commands.require_bootstrap_contract(existing['id'], contract)
                if existing['status']=='available':
                    blocked = self._require_entry(existing)
                    return blocked if blocked is not None else self.sprint_tools.start(existing['id'])
                selected_process = existing['process']
            else:
                if contract.get('goal_type') not in self.processes:
                    raise PoiseError('Неизвестный goal_type')
                selected_process = self.processes[contract['goal_type']]
            if existing is not None:
                data = existing
                self.task_commands.reviewer_preflight(data['id'], self.session, acquiring=True)
                blocked = self._require_entry(data)
                if blocked is not None:
                    return blocked
                if data['claimed_by'] is None and not is_terminal_task_status(data['status']):
                    released = self.handoff_tools.commands.latest(data['id'])
                    if released is not None:
                        self.handoff_tools.resume(data)
                        data=self.task_queries.record(data['id'])
                self._reconcile_task_worktree(data)
                data=self.task_queries.record(data['id'])
            else:
                if contract.get('sprint_id') is not None:
                    raise PoiseError('Sprint task must be published through the Sprint API first')
                if not automatic and self.sprint_tools.known(contract['id']):raise PoiseError('Task/sprint ID collision')
                base=self._creation_base()
                allocation=self.task_commands.create(
                intent,self.session,selected_process,self.cfg['automatic_checks'],
                    {'config_hash':self.config_hash},
                    lambda task_id:self._execution_reservation(
                        task_id,base,selected_process['worktree_required']
                    ),
                self.cfg.get('task_ids'),base,self.cfg['task_decomposition'])
                allocation_receipt=allocation.receipt()
                if allocation.replayed:
                    data=self.task_queries.record(allocation.task_id)
                    if data['claimed_by'] not in (None,self.session):
                        return {**self._context(data,data['status']=='active'),
                                'allocation':allocation_receipt}
                    self.task_commands.reviewer_preflight(data['id'], self.session, acquiring=True)
                    self._reconcile_task_worktree(data)
                    data=self.task_queries.record(allocation.task_id)
                    blocked = self._require_entry(data)
                    if blocked is not None:
                        return {**blocked, 'allocation':allocation_receipt}
                    self.ownership.acquire_task(data['id'])
                    current=self.store.current(self.session)
                    result=self._context(current,current['status']=='active')
                    return {**result,'allocation':allocation_receipt}
                data = self.task_queries.record(allocation.task_id)
                blocked = self._require_entry(data)
                if blocked is not None:
                    return {**blocked, **({'allocation': allocation_receipt}
                                          if allocation_receipt is not None else {})}
                started = self.sprint_tools.start(allocation.task_id)
                return {**started, **({'allocation':allocation_receipt}
                                      if allocation_receipt is not None else {})}
            blocked = self._require_entry(data)
            if blocked is not None:
                return blocked
            self.ownership.acquire_task(data['id'])
            current = self.store.current(self.session)
        if task is None and decision is None and current is not None and is_terminal_task_status(current['status']):
            current = None
        if decision is None and task is None and current is None:
            sprint=self.sprint_tools.overview(None)
            if sprint is not None:return sprint
        if current is None:
            self.runtime.mkdir(parents=True, exist_ok=True)
            return {'session': self.session, 'status':'read_only', 'project':self.cfg['project'],
                    'task':None,'result_template':None,'runtime_root':str(self.runtime),
                    'next_work':'передать task object через work bootstrap; сводка — batch show'}
        data = self._task()
        if decision is not None:
            if decision not in ('continue','rework'):
                raise PoiseError('Решение должно быть continue или rework')
            entry_tree = self._current_tree(data)
            if decision == 'continue':
                state = self.runner.accept(data['id'], self.session, True, entry_tree)
                data = self.task_queries.record(data['id'])
                if state.status == 'completed':
                    self._cleanup_runtime()
                    return {**data['last_report'], 'version': data['version']}
            else:
                if data['status']=='active' and self._stage(data)['handler'] in ('apply_plan','publish'):
                    self.plan_actions.rework_failed(data,feedback,rework_stage,entry_tree)
                elif (data['status'] == 'active' and
                      self.task_commands.inspection_registry_rework_available(data['id'], rework_stage)):
                    self.runner.rework(data['id'], self.session, feedback, entry_tree, rework_stage)
                elif data['status']=='active':
                    if data['pending'] is not None:
                        raise PoiseError('Неизвестен исход прерванной проверки; rework запрещён')
                    worktree=self._verification_workspace(data)
                    checks=self._select_checks(data,self._changed(data,entry_tree))
                    invocations,execution_key=self._verification_execution(
                        data,entry_tree,checks,worktree)
                    batch=self.task_commands.failed_observation_batch(data['id'],entry_tree,execution_key)
                    if batch is None or not self._intact_receipts(
                        data['id'], batch['receipts'], invocations, entry_tree
                    ):
                        raise PoiseError('Нет точного доступного failed check batch текущего результата')
                    self.runner.rework_failed(
                        data['id'],self.session,feedback,entry_tree,execution_key,rework_stage)
                else:
                    self.runner.rework(data['id'], self.session, feedback, entry_tree, rework_stage)
                data = self._task()
            self._cleanup_runtime()
        blocked = self._require_entry(data)
        if blocked is not None:
            return blocked
        result=self._context(data, data['status']=='active')
        return result if allocation_receipt is None else {**result,'allocation':allocation_receipt}

    def accept(self) -> dict:
        data = self._task()
        self.runner.accept(data['id'], self.session, False, None)
        data = self.task_queries.record(data['id'])
        self.store.event(self.session, data['id'], 'user.accept',
                         {'stage':self._stage(data)['id'],'status':data['status']})
        return data['last_report']

    def _cleanup_runtime(self):
        self.result_views.finish()
        try:
            shutil.rmtree(self.runtime)
        except FileNotFoundError:
            pass

    def _select_checks(self, data: dict, changed: list[str]) -> list[dict]:
        stage_id = self._stage(data)['id']
        ids = list(data['contract']['checks'][stage_id])
        automatic = set()
        for mapping in self.cfg['automatic_checks']:
            if any(
                matches_allowed_path(path, pattern)
                for path in changed
                for pattern in mapping['paths']
            ):
                ids.extend(mapping['by_stage'][stage_id])
                automatic.update(mapping['by_stage'][stage_id])
        methods = {m['id']:m for m in data['contract']['methods']}
        missing = set(ids)-methods.keys()
        if missing: raise PoiseError(f'Неизвестные методы: {sorted(missing)}')
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
            if old['scope'] not in roots:
                raise PoiseError('Registered artifact scope has no current owner root; use explicit recover_artifacts')
            current = inspect_paths([old['path']], roots, owners)[0]
            if any(current[key] != old[key] for key in ('id', 'owner', 'scope')):
                raise PoiseError('Registered artifact identity/owner mismatch; inspect ownership before recovery')
            if current['digest'] != old['digest']:
                raise PoiseError(f"Артефакт изменён после регистрации: {old['path']}; используйте новый путь")
            merged[(old['scope'],old['path'])] = current
        return list(merged.values())

    def _artifact_facts(self, records: list[dict]) -> tuple[ArtifactFact, ...]:
        facts = []
        for record in records:
            relative = record['relative_path']
            prefix = self.cfg['batch']['artifact_directories'][record['scope']].rstrip('/') + '/'
            if relative.startswith(prefix):
                relative = relative[len(prefix):]
            facts.append(ArtifactFact(record['id'], record['scope'], relative))
        return tuple(facts)

    def _content_gate(self, data: dict, phase: str, artifacts: list[dict]) -> dict:
        assessment = self.task_commands.assess_content(data['id'],phase,self._artifact_facts(artifacts))
        result = assessment.to_dict()
        self.store.event(self.session,data['id'],'content.gate',result)
        return result

    def _existing_artifacts(self, data: dict) -> list[dict]:
        roots = {
            'runtime': self.runtime,
            'task': task_root(self.state, self.paths, data['id'], data['sprint_id']),
        }
        owners = {'runtime': self.session, 'task': data['id']}
        if data['sprint_id'] is not None:
            roots['sprint'] = sprint_root(self.state, self.paths, data['sprint_id'])
            owners['sprint'] = data['sprint_id']
        records = []
        for prior in self.store.artifact_records(data['id']):
            if prior['scope'] not in roots:
                continue
            try:
                current = inspect_paths([prior['path']], roots, owners)[0]
            except PoiseError:
                continue
            if current['digest'] != prior['digest']:
                continue
            records.append(current)
        return records

    def validate_stage_entry(self, data: dict | None = None) -> dict:
        current = self.task_queries.record(data['id']) if data is not None else self._task()
        assessment = self.task_commands.assess_content(
            current['id'], 'pre', self._artifact_facts(self._existing_artifacts(current))
        )
        return assessment.to_dict()

    def _entry_blocked(
        self,
        data: dict,
        gate: dict,
        stage_id: str | None = None,
        blocked_transition: dict | None = None,
        progression: dict | None = None,
    ) -> dict:
        result = {
            'status': 'broken',
            'task': data['id'],
            'version': data['version'],
            'stage': self._stage(data)['id'] if stage_id is None else stage_id,
            'failure': {
                'kind': 'content_requirements_failed',
                'phase': gate['phase'],
                'content_requirements': gate,
            },
            'checks': [],
            'replayed': False,
            'recovery': [
                {
                    'action': 'repair_stage_contract',
                    'operation': 'revise_stage_contract',
                    'when': 'A reviewer can correct only the defective current gate or scope.',
                },
                {
                    'action': 'restart_task',
                    'operation': 'task',
                    'when': 'The immutable Task contract must be edited from newborn state.',
                },
            ],
        }
        if blocked_transition is not None:
            result['blocked_transition'] = blocked_transition
        if progression is not None:
            result['progression'] = progression
        return result

    def _require_entry(self, data: dict) -> dict | None:
        gate = self.validate_stage_entry(data)
        return None if gate['passed'] else self._entry_blocked(data, gate)

    def validate_stage_scope(self, data: dict | None = None) -> dict:
        current = self.task_queries.record(data['id']) if data is not None else self._task()
        tree = self._current_tree(current)
        changed = self._changed(current, tree)
        stage = self._stage(current)
        if stage['read_only'] and changed:
            raise PoiseError(f'read-only этап изменил репозиторий: {changed}')
        scope = current['contract']['stage_contracts'][current['stage_index']]['allowed_paths']
        outside = [
            path for path in changed
            if not any(matches_allowed_path(path, pattern) for pattern in scope)
        ]
        if outside:
            raise PoiseError(f'Изменения вне stage contract allowed_paths: {outside}')
        return {'tree': tree, 'changed': changed, 'allowed_paths': scope}

    def _content_blocked(self, data: dict, gate: dict, receipts: list[dict]) -> dict:
        return {'status':'content_requirements_failed','task':data['id'], 'stage':self._stage(data)['id'],
                'content_gate':gate,'checks':receipts,'replayed':False,
                'context':self._context(data,True)}

    def verify(self, payload: dict | None, *, packet_digest: str | None) -> dict:
        try:
            result=self._verify(payload, packet_digest=packet_digest)
        finally:
            incidents=self.result_views.finish()
        if incidents:result={**result,'incidents':incidents}
        return result

    def _verify(self, payload: dict | None, *, packet_digest: str | None) -> dict:
        data = self.store.current(self.session)
        if data is None:
            self.store.event(self.session,None,'read_only.finalize',{})
            self._cleanup_runtime()
            return {'status':'read_only_verified','project':self.cfg['project'],'checks':[], 'artifacts':[]}
        if payload is not None and (not isinstance(packet_digest,str) or len(packet_digest)!=64
                or any(c not in '0123456789abcdef' for c in packet_digest)):
            raise PoiseError('Explicit SHA-256 work packet identity required')
        data = self._task(); stage = self._stage(data)
        self.task_commands.reviewer_preflight(data['id'], self.session)
        worktree = self._verification_workspace(data)
        if data['claimed_by'] != self.session:
            raise PoiseError('Нет владения текущей работой')
        if (data['worktree'] is not None
                and self._git(worktree,'symbolic-ref','--short','HEAD') != data['branch']):
            raise PoiseError('В worktree другая ветка')
        pending_checks = data['pending'] == 'checks'
        pending_attempt = is_check_attempt(data['pending'])
        if data['pending'] is not None and not (pending_checks or pending_attempt):
            raise PoiseError('Неизвестен исход прерванной проверки; не запускаем повтор вслепую. Смотрите журнал.')
        tree = self._current_tree(data)
        if data['status'] == 'verified':
            if packet_digest is not None and self.work_resources.packet(data)!=packet_digest:
                raise PoiseError('Different work packet after delivery requires rework')
            if tree != data['last_report']['verified_tree']:
                raise PoiseError('Код изменён после доклада: сначала rework')
            self._cleanup_runtime()
            return {**data['last_report'],'replayed':True}
        if data['status'] != 'active':
            raise PoiseError('Нет активного этапа для verify')
        if payload is None:
            raise PoiseError('Активный verify требует result object; служебный файл не читается')
        payload = deepcopy(payload)
        self.plan_actions.validate(data,payload)
        gate = self.validate_stage_entry(data)
        if not gate['passed']:
            raise PoiseError('stage entry requirements are no longer satisfied')
        scope_state = self.validate_stage_scope(data)
        tree = scope_state['tree']
        changed = scope_state['changed']
        scope = scope_state['allowed_paths']
        if self.development_routing is not None:
            boundary_paths = self._development_changes(data)
            removed = [name for name in self._git(
                worktree, 'diff', '--name-only', '--no-renames', '--diff-filter=D',
                '-z', data['base'], '--'
            ).split('\0') if name]
            architecture = self.development_routing.check_authoring_boundaries(
                str(worktree), stage['handler'], boundary_paths, removed
            )
            if architecture['passed'] is False:
                # No submission or Task mutation: repair and retry in this same authoring stage.
                return {'status': 'architecture_boundaries_failed', 'task': data['id'],
                        'stage': stage['id'], 'architecture': architecture, 'checks': [], 'replayed': False}
        # Reject pre-existing path/identity failures before a submission can mutate
        # the current registry, content layers, evidence or Task history.
        self.validate_verification_artifacts(payload['artifact_paths'], data)
        if pending_checks or pending_attempt:
            submitted_digest = self.runner.matching_submission_digest(
                data['id'], self.session, payload
            )
            if submitted_digest is None:
                raise PoiseError(
                    'Восстановление pending=checks требует точного повтора '
                    'текущего submitted результата'
                )
            submitted = None
        else:
            submitted = self.runner.submit(data['id'], self.session, payload)
            data = self._task()
        if changed and (not isinstance(payload['commit_message'],str) or not re.fullmatch(self.cfg['git']['commit_pattern'],payload['commit_message'])):
            raise PoiseError('Сообщение коммита не соответствует правилу проекта')
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
            scope_state = self.validate_stage_scope(data)
            tree = scope_state['tree']
            changed = scope_state['changed']
            self.plan_actions.commands.record_assessment(data['id'],self.session,tree,action)
            data=self._task()
        checks = self._select_checks(data, changed)
        invocations, execution_key = self._verification_execution(
            data, tree, checks, worktree
        )
        # Env values participate only in the digest; they are not persisted in receipts.
        current_submission_digest = (
            submitted_digest if submitted is None else submitted.digest
        )
        if pending_attempt:
            data['pending'] = self.runner.current_check_attempt(
                data['id'], self.session, tree, execution_key, [m['id'] for m in checks]
            )
        if pending_checks:
            batch = self.task_commands.submission_observation_batch(
                data['id'],current_submission_digest,tree,execution_key
            )
        else:
            batch = self.task_commands.observation_batch(
                data['id'],tree,execution_key
            )
        intact = not pending_attempt and batch is not None and self._intact_receipts(
            data['id'], batch['receipts'], invocations, tree
        )
        usable = intact and self._usable_receipts(
            data['id'], batch['receipts'], invocations, tree
        )
        if pending_checks:
            if not intact:
                raise PoiseError(
                    'Неизвестен исход прерванной проверки; не запускаем '
                    'повтор вслепую. Смотрите журнал.'
                )
            self.runner.recover_pending_checks(
                data['id'], self.session, submitted_digest, tree,
                execution_key, batch['receipts']
            )
            data = self._task()
        if payload['evidence_work']['phase']=='continue' and not usable:
            return {'status':'observations_stale','task':data['id'],'stage':stage['id'],
                    'reason':'Нужен PREPARE: точные входы наблюдения изменились или receipt недоступен.',
                    'context':self._context(data,True)}
        missing = self.task_commands.evidence_precheck(data['id'],self.session,tree,execution_key)
        if missing:
            return {'status':'evidence_requirements_failed','task':data['id'],'stage':stage['id'],
                    'missing':list(missing),'checks':[],'context':self._context(data,True)}
        payload_hash = current_submission_digest
        publication = data['publication']
        if (publication is not None and publication['tree']==tree and publication['payload_hash']==payload_hash
                and publication['execution_key']==execution_key and usable):
            return self._publish(data, publication, packet_digest=packet_digest)
        if intact:
            receipts = batch['receipts']
            attempt = data['attempts']
        else:
            if not pending_attempt:
                self.runner.begin_check_attempt(
                    data['id'], self.session, tree, execution_key,
                    [m['id'] for m in checks], self.cfg['limits']['verify_attempts'],
                    data['_version'], current_submission_digest
                )
                data = self._task()
            attempt = data['attempts']
            self.store.event(self.session,data['id'],'verify.start',{'stage':stage['id'],'tree':tree,'attempt':attempt,'methods':[m['id'] for m in checks]})
            try:
                receipts = self._execute_checks(data,stage,tree,checks,invocations,roots)
            except Exception as exc:
                # Preserve durable intent even if spawn/receipt persistence failed.
                self.store.event(self.session,data['id'],'verify.error',{'message':str(exc)})
                raise
            if not self._intact_receipts(data['id'], receipts, invocations, tree):
                raise PoiseError('Unknown check outcome: immutable receipt files changed before finalization')
            self.runner.record_observations(data['id'],self.session,tree,execution_key,receipts)
            data = self._task()
        if not self._usable_receipts(data['id'], receipts, invocations, tree):
            return {'status':'checks_failed','task':data['id'],'stage':stage['id'],'attempt':attempt,'checks':receipts,'replayed':False}
        if self._current_tree(data) != tree:
            raise PoiseError('Наблюдения изменили проверяемое дерево; сначала согласуйте фактическое состояние')
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
        if self._current_tree(data) != tree:
            raise PoiseError('Проверки изменили дерево; требуется verify фактического нового состояния')
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
        return self._publish(data, publication, packet_digest=packet_digest)

    def _action_incomplete(self,data,payload,action):
        context=self._context(self._task(),True)
        draft=deepcopy(payload)
        if action['status']=='awaiting_resolution':
            draft['stage_work']['phase']='continue'
        context['result_template']=draft
        status={'awaiting_resolution':'awaiting_action_continuation','failed':'action_failed','blocked':'action_blocked','prepared':'action_blocked','running':'action_blocked'}[action['status']]
        return {'status':status,'task':data['id'],'stage':self._stage(data)['id'],
                'action':action,'context':context,'next_work':'Resolve the reported action state; do not repeat external effects manually.'}

    def _invocations(self, checks, worktree):
        invocations=[]
        for method in checks:
            self.result_views.policy.select(method['argv'])
            cwd=(worktree/method['cwd']).resolve()
            if not cwd.is_relative_to(worktree.resolve()) or not cwd.is_dir():
                raise PoiseError('cwd проверки должен находиться в текущем worktree')
            env={}
            for name in self.cfg['environment_names']:
                if name not in os.environ:
                    raise PoiseError(f'Требуемая переменная среды отсутствует: {name}')
                env[name]=os.environ[name]
            env.update(method['environment'])
            invocation={'method':method,'cwd':str(cwd),'environment':env}
            provenance=resolve_source_under_test(
                method, worktree=worktree, cwd=cwd, environment=env
            )
            invocation['source_provenance']=provenance
            invocation['provenance_digest']=digest(provenance)
            invocation['expectation_digest']=digest({
                'expected_exit_code': method['expected_exit_code'],
                'stdout_contains': method['stdout_contains'],
                'stderr_contains': method['stderr_contains'],
                'red_failure': method.get('verification_plan', {}).get('red_failure'),
                'outputs': method.get('outputs', []),
            })
            invocations.append(invocation)
        return invocations

    def _verification_execution(self, data, tree, checks, worktree):
        invocations = self._invocations(checks, worktree)
        execution_key = digest({
            'stage': self._stage(data)['id'],
            'iteration': data['iteration'],
            'tree': tree,
            'invocations': invocations,
        })
        return invocations, execution_key

    def _intact_receipts(self, task_id, receipts, invocations, tree):
        if (
            not completed_receipts(receipts)
            or not isinstance(invocations, list)
            or len(receipts) != len(invocations)
        ):
            return False
        recorded={
            receipt['id']: receipt
            for receipt in self.evidence_commands.list_for(task_id)
            if isinstance(receipt,dict) and isinstance(receipt.get('id'),str)
        }
        required = {
            'stderr',
            'stderr_digest',
            'stdout',
            'stdout_digest',
        }
        for r, invocation in zip(receipts, invocations, strict=True):
            if (
                not isinstance(r, dict)
                or not required <= set(r)
                or recorded.get(r['id']) != r
            ):
                return False
            method=invocation['method']
            expected={
                'argv': method['argv'],
                'cwd': invocation['cwd'],
                'expectation_digest': invocation['expectation_digest'],
                'expected_exit_code': method['expected_exit_code'],
                'guard': method['guard'],
                'method': method['id'],
                'obligations': method['obligations'],
                'provenance_digest': invocation['provenance_digest'],
                'source_provenance': invocation['source_provenance'],
                'tree': tree,
            }
            if any(r.get(field) != value for field,value in expected.items()):
                return False
            for name in ('stdout','stderr'):
                path_value = r[name]
                digest_value = r[name + '_digest']
                if not isinstance(path_value, str) or not path_value:
                    return False
                if not isinstance(digest_value, str) or not digest_value:
                    return False
                path=Path(path_value)
                if not path.is_file() or file_digest(path)!=digest_value:
                    return False
            maintenance=r.get('timeout_maintenance')
            if maintenance is not None:
                if not isinstance(maintenance,dict) or set(maintenance)!={'path','digest'}:
                    return False
                maintenance_path=Path(maintenance['path'])
                if not maintenance_path.is_file() or file_digest(maintenance_path)!=maintenance['digest']:
                    return False
            declarations = method.get('outputs', [])
            outputs = r.get('outputs')
            if not isinstance(outputs, list) or len(outputs) != len(declarations):
                return False
            for declaration, output in zip(declarations, outputs, strict=True):
                expected_output = {
                    'id': declaration['id'],
                    'declared_path': declaration['path'],
                    'required': declaration['required'],
                }
                if any(output.get(key) != value for key, value in expected_output.items()):
                    return False
                if output.get('status') == 'captured':
                    captured = Path(output.get('path', ''))
                    if (not captured.is_file() or captured.is_symlink()
                            or output.get('digest') != file_digest(captured)
                            or output.get('size') != captured.stat().st_size):
                        return False
                elif output.get('status') not in ('missing', 'invalid'):
                    return False
        return True

    def _usable_receipts(self, task_id, receipts, invocations, tree):
        return self._intact_receipts(task_id, receipts, invocations, tree) and all(
            r['interpretable'] and (not r['guard'] or r['passed'])
            for r in receipts
        )

    def _execute_checks(self, data, stage, tree, checks, invocations, roots):
        receipts=[]
        attempt = data['pending']
        recorded = {r['id']: r for r in self.evidence_commands.list_for(data['id'])}
        # Inspect the whole finished prefix before permitting any new tail effect.
        for run, invocation in zip(attempt['runs'], invocations, strict=True):
            receipt = recorded.get(run['run_id'])
            if run['started']:
                if (not receipt_matches(attempt, run, receipt)
                        or not self._intact_receipts(data['id'], [receipt], [invocation], tree)):
                    raise PoiseError(
                        f"Unknown check outcome: attempt={attempt['attempt_id']} run={run['run_id']}; "
                        'restore the original immutable receipt and files, do not retry blindly'
                    )
            elif receipt is not None:
                raise PoiseError('Unexpected receipt for a check run without start permission')
        for index, (method,invocation) in enumerate(zip(checks,invocations,strict=True)):
            run = attempt['runs'][index]
            run_id = run['run_id']
            if run['started']:
                receipts.append(recorded[run_id])
                continue
            run_dir=descendant(roots['task'],self.paths['runs'])/run_id
            declared_output_dir = run_dir / 'declared-outputs'
            declared_output_dir.mkdir(parents=True, exist_ok=True)
            run_environment = {
                **invocation['environment'],
                'POISE_RUN_OUTPUT_DIR': str(declared_output_dir),
            }
            profile=timeout_profile(method['argv'],method['id'],run_environment)
            history=self.evidence_commands.timeout_history(profile)
            timeout_selection=self.check_timeout_policy.select(
                history['durations'],None,history['evidence_ids']
            )
            timeout_snapshot={
                'profile':profile,
                'selection':timeout_selection,
                'history_evidence_ids':history['evidence_ids'],
            }
            timeout_snapshot_path=run_dir/'timeout-policy.json'
            timeout_snapshot_path.write_text(
                json.dumps(timeout_snapshot,sort_keys=True,ensure_ascii=False,indent=2)+'\n',
                encoding='utf-8',
            )
            attempt = self.runner.start_check_run(data['id'], self.session, attempt, run_id)
            result=self.check_runner.run(
                run_id, method['argv'], Path(invocation['cwd']), run_environment, timeout_selection['seconds'],
                descendant(run_dir,self.paths['stdout']), descendant(run_dir,self.paths['stderr']),
                progress_gap_seconds=timeout_selection['progress_gap_seconds'],
                poll_seconds=timeout_selection['poll_seconds'],
            )
            outputs, outputs_complete = capture_declared_outputs(
                method.get('outputs', []), declared_output_dir, run_dir / 'outputs'
            )
            passed=method_passed(method, result) and outputs_complete
            interpretable=(result.get('capture_complete', True) is True and not result['timed_out'] and result['actual_exit_code'] is not None and result['actual_exit_code']>=0 and
                           all(result['actual_exit_code'] in rule['exit_codes'] and
                               all(contains(Path(result['stdout']),t) for t in rule['stdout_contains']) and
                               all(contains(Path(result['stderr']),t) for t in rule['stderr_contains'])
                               for rule in method['observation_rules']))
            receipt={**result,'attempt_id':attempt['attempt_id'],
                     'submission_digest':attempt['submission_digest'], 'execution_key':attempt['execution_key'],
                     'interpretable':interpretable,'id':run_id,'method':method['id'],'obligations':method['obligations'],'guard':method['guard'],
                     'argv':method['argv'],'cwd':invocation['cwd'],'expected_exit_code':method['expected_exit_code'],
                     'passed':passed,'tree':tree,'stdout_digest':file_digest(Path(result['stdout'])),
                     'stderr_digest':file_digest(Path(result['stderr'])),
                     'outputs':outputs,
                     'timeout_profile':profile,
                     'timeout_selection':timeout_selection,
                     'timeout_maintenance':{
                         'path':str(timeout_snapshot_path),
                         'digest':file_digest(timeout_snapshot_path),
                     },
                     'preview':preview(Path(result['stderr']),self.cfg['limits']['preview_chars'])}
            for field in ('expectation_digest','provenance_digest','source_provenance'):
                receipt[field]=invocation[field]
            presentation=self.result_views.capture(receipt,run_dir)
            receipt['presentation']={k:v for k,v in presentation.items() if k!='status'}
            self.evidence_commands.record_receipt(data['id'],self.session,stage['id'],data['iteration'],receipt)
            receipts.append(receipt)
            self.store.event(self.session,data['id'],'check.finished',{'run':run_id,'passed':passed,'exit':result['actual_exit_code']})
        return receipts

    def _publish(self, data: dict, publication: dict, *, packet_digest: str) -> dict:
        worktree = self._verification_workspace(data)
        tree = publication['tree']; stage = self._stage(data)
        if self._current_tree(data) != tree:
            raise PoiseError('Дерево изменилось перед публикацией; старое evidence не принимается')
        current_artifacts = self._candidate_artifacts(data, [r['path'] for r in publication['artifacts']], self._roots(data))
        for phase in ('pre','post'):
            gate = self._content_gate(data,phase,current_artifacts)
            if not gate['passed']:
                return self._content_blocked(data,gate,publication['checks'])
        publication['artifacts'] = current_artifacts
        sha = data['base'] if data['worktree'] is None else self._git(worktree,'rev-parse','HEAD')
        pending_merge = (False if data['worktree'] is None else
                         self.plan_actions._read_optional_ref(worktree,'MERGE_HEAD') is not None)
        if data['worktree'] is None and publication['changed']:
            raise PoiseError('Worktree-free Task cannot publish repository changes')
        if publication['changed'] or pending_merge:
            if self._git(worktree,'rev-parse','HEAD^{tree}') != tree or pending_merge:
                self._git(worktree,'add','--all')
                if self._git(worktree,'write-tree') != tree:
                    raise PoiseError('Индекс не равен проверенному дереву')
                actor = {'GIT_AUTHOR_NAME':self.cfg['git']['author_name'],
                         'GIT_AUTHOR_EMAIL':self.cfg['git']['author_email'],
                         'GIT_COMMITTER_NAME':self.cfg['git']['author_name'],
                         'GIT_COMMITTER_EMAIL':self.cfg['git']['author_email']}
                self._git(worktree,'commit','-m',publication['commit_message'],env={**os.environ,**actor})
                sha = self._git(worktree,'rev-parse','HEAD')
            publication['commit'] = sha; data['publication'] = publication; self.store.save(data)
            if self._git(worktree,'rev-parse','HEAD^{tree}') != tree or self._tree(worktree) != tree:
                self.store.event(self.session,data['id'],'incident.tree_changed_during_commit',{'commit':sha,'tested_tree':tree})
                raise PoiseError('Коммит/hook изменил проверенное дерево; remote publication не выполнялась')
        # Только task/sprint links переживают cleanup. Runtime пути остаются временными.
        permanent = [r for r in publication['artifacts'] if r['scope']!='runtime']
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
                             self._artifact_facts(publication['artifacts']),
                             packet_digest=packet_digest, permanent_artifacts=permanent)
        self._cleanup_runtime()
        return report

    def cancel(self, reason: str) -> dict:
        if not isinstance(reason,str) or not reason.strip(): raise PoiseError('Нужна инструкция пользователя об отмене')
        data = self._task()
        if data['pending'] is not None: raise PoiseError('Сначала установить исход незавершённой операции')
        run=self.cleanup_tools.prepare_terminal(data['id'],f"cancel-{data['_version']}",reason)
        self.task_commands.cancel(data['id'], self.session, reason,self.cleanup_tools.pending(run))
        self.store.event(self.session,data['id'],'task.cancelled',{'reason':reason,'worktree_preserved':True})
        self._cleanup_runtime()
        return {'status':'cancelled','task':data['id'],'worktree_preserved':True,
                'cleanup':self.cleanup_tools.terminal_result(run)}

    def show(self) -> dict:
        data = self.store.current(self.session)
        if data is None:
            return {'status':'read_only','project':self.cfg['project'],'tasks':self.task_queries.summary()}
        if data['status']=='newborn':
            return {'task':data['id'],'status':'newborn','revision':data['revision'],
                    'sprint':data['sprint_id'],'claimed_by':data['claimed_by'],
                    'goal_type':data['goal_type'],'route_entry':data['route_entry'],
                    'ready':data['ready'],'draft':deepcopy(data['draft']),
                    'history':self.task_queries.history(data['id']),
                    'token_usage':'unavailable'}
        submissions, evidence = self.store.counts(data['id'])
        return {'task':data['id'],'status':data['status'],'version':data['version'],
                'stage':self._stage(data)['id'],
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

    def show_verification_registry(self):
        data = self._task()
        return self.task_queries.verification_registry(data['id'])

    def show_output(self,receipt_id,representation,requested):
        from .modules.work.domain import read_range
        data=self._task()
        records=self.evidence_commands.list_for(data['id'])
        receipt=next((x for x in records if x['id']==receipt_id),None)
        if receipt is None or 'presentation' not in receipt:
            raise PoiseError('No command presentation for current task and receipt')
        self.result_views.finish()
        root=self._roots(data)['task'].resolve()
        if receipt['presentation']['manifest'] is None:raise PoiseError('View storage unavailable; original command output remains in receipt')
        manifest=Path(self.task_queries.resolve_path(data['id'],receipt['presentation']['manifest'])).resolve()
        if not manifest.is_relative_to(root):raise PoiseError('Presentation outside current task')
        state=read_json(manifest)
        if state['status']!='ready':raise PoiseError('Presentation is not ready; see recorded parser incident')
        if representation not in state['representations']:raise PoiseError('Unknown representation')
        item=state['representations'][representation]
        path=Path(self.task_queries.resolve_path(data['id'],item['path'])).resolve()
        if not path.is_relative_to(root) or file_digest(path)!=item['digest']:raise PoiseError('Presentation changed')
        # Explicit range selection is bounded by the existing initial read policy.
        from .infrastructure.result_views import read_output_range
        return read_output_range(path,item,requested,self.cfg['batch']['initial_read_lines'])
