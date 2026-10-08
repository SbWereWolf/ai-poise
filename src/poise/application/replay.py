"""Coordinate accepted Task gates through Task, workspace and check owners."""
import hashlib

from ..modules.foundation.errors import DomainError
from ..modules.tasks.replay import AcceptedVisit, ReplayIntent, ReplayResult, proof_contract_compatible
from ..modules.tasks.ports import ReplayWorkspace


class ReplayCoordinator:
    def __init__(self, commands, workspace: ReplayWorkspace, read_task, check_files, execute_checks, artifact_facts):
        self.commands = commands
        self.workspace = workspace
        self.read_task = read_task
        self.check_files = check_files
        self.execute_checks = execute_checks
        self.artifact_facts = artifact_facts

    def run(self, task_id, actor, request_id, target_stage, context, *, force_duplicate_start=False):
        intent = ReplayIntent(task_id, request_id, target_stage)
        cursor = context['cursor']
        if cursor is None:
            data = self.read_task(task_id)
            # Fresh invalid requests have no journal, ref or Task effects.
            from ..modules.workflow.domain import RouteDefinition
            route = RouteDefinition.from_process(data['process'])
            current = data['process']['stages'][data['stage_index']]['id']
            if target_stage is not None and not route.can_reach(current, target_stage):
                raise DomainError('Progression target is not reachable')
            start = self.workspace.inspect_preservation(context['sources'])
            key = hashlib.sha256((task_id + '\0' + request_id).encode()).hexdigest()
            cursor = {'start_commit': start, 'recovery_ref': f'refs/poise/replay/{key}',
                      'subject_commit': None, 'sources': context['sources'],
                      'passed': [], 'phase': 'preserve'}
            self.commands.start_replay(task_id, actor, request_id, target_stage, cursor)
        if cursor['phase'] == 'preserve':
            self.workspace.ensure_recovery_ref(cursor['recovery_ref'], cursor['start_commit'])
            cursor['phase'] = 'preserved'
            self.commands.save_replay_progress(task_id, actor, request_id, cursor)

        def stop(status, reason, stage, subject):
            projection = ReplayResult(intent, cursor['start_commit'], cursor['recovery_ref'],
                subject, stage, reason, tuple(AcceptedVisit(**visit) for visit in cursor['passed']))
            result = {'status': status, 'replay': projection.to_dict()}
            return self.commands.finish_replay(task_id, actor, request_id, target_stage, result)

        while True:
            data = self.read_task(task_id)
            stage = data['process']['stages'][data['stage_index']]['id']
            source = cursor['sources'].get(stage)
            if source is None:
                if stage == target_stage:
                    return stop('progression_target_reached', 'target_reached', stage, None)
                return stop('progression_work_required', 'work_required', stage, None)
            if source['reason'] is not None:
                return stop('progression_stopped', source['reason'], stage, None)
            if any(visit['visit_id'] == source['visit_id'] for visit in cursor['passed']):
                return stop('progression_work_required', 'work_required', stage, None)
            from ..modules.workflow.domain import RouteDefinition
            route = RouteDefinition.from_process(data['process'])
            try:
                following = route.node(stage).target(source['report']['stage_outcome'])
            except DomainError:
                return stop('progression_stopped', 'route_unavailable', stage, None)
            if following != source['workflow']['next_stage']:
                return stop('progression_stopped', 'route_unavailable', stage, None)
            if not self.workspace.contains(source['commit']):
                return stop('progression_stopped', 'source_unavailable', stage, None)
            if cursor['phase'] == 'preserved':
                cursor['previous_commit'] = cursor['subject_commit'] or cursor['start_commit']
                cursor['checkout_commit'] = source['commit']
                cursor['phase'] = 'checkout'
                self.commands.save_replay_progress(task_id, actor, request_id, cursor)
            if cursor['checkout_commit'] != source['commit']:
                raise DomainError('Saved replay visit does not match current Task stage')
            self.workspace.ensure_recovery_ref(cursor['recovery_ref'], cursor['start_commit'])
            self.workspace.checkout_accepted(source['commit'], source['tree'], cursor['previous_commit'])
            cursor['subject_commit'] = source['commit']
            if cursor['phase'] == 'checkout':
                cursor['phase'] = 'checking'
                self.commands.save_replay_progress(task_id, actor, request_id, cursor)
            if stage == target_stage:
                return stop('progression_target_reached', 'target_reached', stage, source['commit'])
            if source.get('methods_missing'):
                return stop('progression_stopped', 'tests_missing', stage, source['commit'])
            if not proof_contract_compatible(data['contract'], source):
                return stop('progression_stopped', 'proof_contract_changed', stage, source['commit'])
            file_failure = self.check_files(source)
            if file_failure is not None:
                return stop('progression_stopped', file_failure, stage, source['commit'])
            candidate = self.commands.prepare_replay_visit(task_id, actor, request_id, source)
            checked = self.execute_checks(task_id, source, candidate)
            if checked['reason'] is not None:
                return stop('progression_stopped', checked['reason'], stage, source['commit'])
            file_failure = self.check_files(source)
            if file_failure is not None:
                return stop('progression_stopped', file_failure, stage, source['commit'])
            self.commands.confirm_replay_visit(task_id, actor, source['tree'], checked['execution_key'])
            moved = self.commands.advance_progression(
                task_id, actor, request_id, target_stage, source['tree'], self.artifact_facts(data),
                force_duplicate_start=force_duplicate_start, replay_request=request_id)
            cursor = moved['cursor']
            if moved['kind'] == 'task_acceptance_required':
                return stop('progression_stopped', 'task_acceptance_required', stage, source['commit'])
            if moved['kind'] == 'acceptance_required':
                return stop('user_acceptance_required', 'acceptance_required', moved['following'], None)
            if moved['kind'] == 'entry_blocked':
                return stop('progression_stopped', 'entry_blocked', stage, source['commit'])
