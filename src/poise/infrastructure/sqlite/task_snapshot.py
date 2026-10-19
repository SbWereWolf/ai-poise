"""Materialized Task rows and their canonical, connection-free restoration."""
from dataclasses import dataclass
from copy import deepcopy
import json


@dataclass(frozen=True)
class TaskRawCapture:
    header: dict
    content: str | None
    workflow: str | None
    methods: tuple[str, ...]
    sections: tuple[tuple, ...]
    trace: tuple[tuple, ...]
    proof: str | None

    def restore(self):
        from . import tasks as owner
        row = self.header
        state = owner.SqliteTaskRepository._restore_state(row)
        metadata = json.loads(row['metadata'])
        if self.content is None:
            raise owner.PoiseError('Нет зарегистрированного контракта содержимого')
        if self.workflow is None:
            raise owner.PoiseError('Нет сохранённого состояния маршрута')
        workflow = json.loads(self.workflow)
        registry = restore_registry(metadata, workflow, self.methods)
        metadata['contract']['methods'] = [entry.to_dict()['method'] for entry in registry.entries]
        policy = owner.stored_content_policy_from_metadata(metadata, json.loads(self.content))
        contracts = (None if 'stage_contracts' not in metadata['contract'] else
                     owner.TaskStageContracts.parse(metadata['contract']['stage_contracts'],
                         owner.RouteDefinition.from_workflow(metadata['workflow']), policy))
        snapshot = owner.ContentSnapshot(
            tuple(owner.SectionValue(name, content, owner.ContentState(status))
                  for name, content, status in self.sections),
            tuple(owner.TraceValue(route, point, data) for route, point, data in self.trace))
        if self.proof is None:
            raise owner.PoiseError('Нет обязательного evidence state')
        proof = json.loads(self.proof)
        return owner.Task(state, owner.stages_from_workflow(metadata['workflow']), policy,
            snapshot, registry, owner.RouteDefinition.from_workflow(metadata['workflow']),
            owner.RouteProgress.from_dict(workflow['progress']),
            owner.FeedbackBook.from_dict(workflow['feedback']),
            owner.evidence_plan_from_metadata(metadata, registry),
            owner.EvidenceBook.from_dict(proof['book']), proof['input'], proof['assessment'],
            workflow['action_assessment'], contracts,
            None if 'duplicate_reuse' not in workflow else owner.encode(workflow['duplicate_reuse']),
            None if 'replay_candidate' not in workflow else
                json.dumps(workflow['replay_candidate'], sort_keys=True, ensure_ascii=False),
            owner.StageCriteriaBook.from_dict(metadata['stage_criteria'])
            if 'stage_criteria' in metadata else owner.SqliteTaskRepository._missing_criteria())


def capture_task(db, task_id):
    from .tasks import SqliteTaskRepository
    header = dict(SqliteTaskRepository(db)._load_state_row(task_id))
    content = db.execute('SELECT data FROM content_contracts WHERE task_id=? '
                         'ORDER BY version DESC LIMIT 1', (task_id,)).fetchone()
    workflow = db.execute('SELECT data FROM task_workflows WHERE task_id=?', (task_id,)).fetchone()
    proof = db.execute('SELECT data FROM task_proofs WHERE task_id=?', (task_id,)).fetchone()
    sections = db.execute('SELECT section_id,content,content_state FROM '
        '(SELECT section_id,content,content_state,ROW_NUMBER() OVER '
        '(PARTITION BY section_id ORDER BY submission_id DESC) AS n '
        'FROM section_layers WHERE task_id=?) WHERE n=1 ORDER BY section_id', (task_id,)).fetchall()
    trace = db.execute('SELECT route_id,point_id,data FROM '
        '(SELECT route_id,point_id,data,ROW_NUMBER() OVER '
        '(PARTITION BY route_id,point_id ORDER BY submission_id DESC) AS n '
        'FROM trace_point_layers WHERE task_id=?) WHERE n=1 ORDER BY route_id,point_id',
        (task_id,)).fetchall()
    return TaskRawCapture(header, None if content is None else content[0],
        None if workflow is None else workflow[0], capture_methods(db, task_id),
        tuple(tuple(row) for row in sections), tuple(tuple(row) for row in trace),
        None if proof is None else proof[0])


def capture_methods(db, task_id):
    return tuple(row[0] for row in db.execute(
        'SELECT data FROM task_methods WHERE task_id=? ORDER BY rowid', (task_id,)))


def restore_registry(metadata, workflow, methods):
    from .tasks import without_retired_method_timeout
    from ...modules.foundation.errors import PoiseError
    from ...modules.tasks.definition import (
        obligation_catalog, registry_inspection_stages, stored_executable_obligations,
    )
    from ...modules.verification.domain import CheckRegistry
    if not isinstance(workflow.get('registry'), dict):
        raise PoiseError('Current verification registry state is missing or corrupt')
    return CheckRegistry.from_items(
        [without_retired_method_timeout(json.loads(row)) for row in methods],
        tuple(stage['id'] for stage in metadata['workflow']['stages']),
    ).with_executable_obligations(
        stored_executable_obligations(metadata['contract'], metadata['workflow'], workflow['registry']),
        registry_inspection_stages(metadata['workflow']), obligation_catalog(metadata['contract']),
    ).restore_state(workflow['registry'])


def restore_restart_context(raw_metadata):
    metadata = json.loads(raw_metadata)
    return {'config_hash': metadata['config_hash'],
            'contract': deepcopy(metadata.get('contract')),
            'creation_request': deepcopy(metadata.get('creation_request')),
            'workflow': deepcopy(metadata.get('workflow')),
            'requirements_agreement': deepcopy(metadata.get('requirements_agreement')),
            'requirements_snapshot': deepcopy(metadata.get('requirements_snapshot')),
            'restart_history': deepcopy(metadata.get('restart_history', [])),
            'sprint_id': metadata['sprint_id'],
            'stage_contract_history': deepcopy(metadata.get('stage_contract_history', [])),
            'stage_criteria': deepcopy(metadata.get('stage_criteria'))}


def restore_restarted_registry(task_id, metadata, workflow, methods):
    from ...modules.foundation.errors import PoiseError
    metadata = json.loads(metadata)
    if not isinstance(metadata.get('registry_workflow'), dict):
        raise PoiseError('Restarted Task has no frozen registry workflow context')
    if workflow is None:
        raise PoiseError('Restarted Task has no current verification registry')
    return restore_registry({**metadata, 'workflow': metadata['registry_workflow']},
                            json.loads(workflow), methods)
