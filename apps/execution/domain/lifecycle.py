"""Existing revision/rework decisions at the independent process owner."""
from apps.execution.domain.records import RecordsError, integer
from apps.execution.domain.process.definition import GoalTypeDefinition


class LifecyclePolicy:
    @staticmethod
    def validate_process(process, contract, error_type):
        GoalTypeDefinition.parse(process)
        if process['worktree_required'] and contract['workspace_id'] is None:
            raise error_type('Selected process requires workspace_id')
        stages = {stage['id'] for stage in process['stages']}
        for expected in contract['stage_expectations']:
            if not isinstance(expected, dict):
                raise error_type('stage_expectations: required stage object')
            stage_id = expected.get('stage_id')
            if not isinstance(stage_id, str) or not stage_id or '\0' in stage_id:
                raise error_type('stage_expectations.stage_id: required nonempty string')
            if stage_id not in stages:
                raise error_type('stage_expectations.stage_id must reference selected process stages')
        if set(contract['evidence_plan']) - stages:
            raise error_type('evidence_plan must reference selected process stages')

    @staticmethod
    def actor(settings, actor_id):
        if settings is None or actor_id not in settings['actors']:
            raise RecordsError('Select a configured lifecycle actor')
        return settings['actors'][actor_id]

    @staticmethod
    def require_state(execution, allowed):
        if execution['state'] not in allowed:
            raise RecordsError(f'Lifecycle state {execution["state"]} does not permit this action')

    @staticmethod
    def validate_snapshot(snapshot, contract, registered_goal):
        if not isinstance(snapshot, dict) or snapshot.get('task_id') != contract['task_id']:
            raise RecordsError('Requirements snapshot task_id must match the selected Task')
        integer(snapshot.get('snapshot_revision'), 'Requirements snapshot_revision', 1)
        if snapshot.get('snapshot_revision') != contract['requirements_snapshot_revision']:
            raise RecordsError('Select the contract Requirements snapshot revision')
        statement = snapshot.get('statement')
        integer(snapshot.get('statement_revision'), 'Requirements snapshot statement_revision', 1)
        if isinstance(statement, dict):
            integer(statement.get('statement_revision'), 'Published statement_revision', 1)
        if (not isinstance(statement, dict) or statement.get('task_id') != contract['task_id']
                or statement.get('statement_revision') != snapshot.get('statement_revision')):
            raise RecordsError('Requirements snapshot needs its matching published statement')
        goal = statement.get('goal')
        if not isinstance(goal, str) or not goal.strip():
            raise RecordsError('Requirements snapshot needs the registered goal')
        if registered_goal is not None and goal != registered_goal:
            raise RecordsError('Preserve the registered goal across restart')
        return goal

    @staticmethod
    def revision(before, desired, fields):
        for key in ('task_id', 'workspace_id', 'admission_id', 'restart_policy_id'):
            if desired[key] != before[key]:
                raise RecordsError(f'Preserve the existing {key} binding')
        changed = sorted(key for key in before if before[key] != desired[key])
        denied = set(changed) - set(fields)
        if denied:
            raise RecordsError(f'Explicit revision_fields do not permit {sorted(denied)}')
        return changed

    @staticmethod
    def approval(proposal, actor_id, role, policy):
        if role not in policy['approving_roles']:
            raise RecordsError('The configured approving role is required')
        if actor_id in (proposal['performer_actor_id'], proposal['proposer_actor_id']):
            raise RecordsError('A distinct performer/proposer and independent approver are required')

    @staticmethod
    def entry(process, role):
        entry = process['route']['entry']
        stage = next(stage for stage in process['stages'] if stage['id'] == entry)
        if role != stage['role']:
            raise RecordsError('The route entry role must match the configured actor role')
        return entry

    @staticmethod
    def presented(execution, result, contract):
        if result is None or not result.data['assessment']['passed']:
            raise RecordsError('Select an accepted own stage result')
        provenance = result.data['provenance']
        if provenance['stage_id'] != execution['current_stage']:
            raise RecordsError('Presented result must match the current stage')
        if provenance['iteration'] != execution['iteration']:
            raise RecordsError('Presented result must match the current iteration')
        expected = {'task_id': execution['task_id'], 'generation': execution['generation'],
                    'contract_revision': execution['contract_revision'],
                    'process_ref': contract['process_ref']}
        if any(provenance[key] != value for key, value in expected.items()):
            raise RecordsError('Presented result must match the own generation and contract')

    @staticmethod
    def rework(execution, process, visits, target):
        LifecyclePolicy.require_state(execution, ('verified',))
        stage = next(stage for stage in process['stages'] if stage['id'] == execution['current_stage'])
        if target not in stage['rework_targets']:
            raise RecordsError('Select a route-permitted rework target')
        return visits.get(target, 0) + 1
