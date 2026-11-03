"""Addressed Task read model: one protected snapshot, then pure projection."""
import json

from ...modules.foundation.errors import PoiseError
from ...modules.tasks.domain import is_terminal_task_status
from .task_delivery import SqliteTaskDeliveryRepository
from .task_record import capture_record_in
from .task_snapshot import capture_task


def _matches(db, selector):
    projection = ("SELECT id,status,COALESCE(json_extract(metadata,'$.goal'),"
                  "json_extract(metadata,'$.newborn.draft.goal')) AS goal FROM tasks ")
    exact = db.execute(projection + 'WHERE id=?', (selector,)).fetchone()
    if exact is not None:
        return 'exact', [dict(exact)]
    return 'substring', [dict(row) for row in db.execute(
        projection + 'WHERE instr(id,?)>0 ORDER BY id', (selector,))]


def _stage_context(record, task):
    stage_id = task.stage.stage_id
    node = task.route.node(stage_id)
    contract = task._require_stage_contracts().stage(stage_id)
    phase = next((phase for phase in record['contract']['decomposition']['phases']
                  if phase['stage'] == stage_id), None)
    if phase is None:
        raise PoiseError(f"Task {record['id']}: отсутствует decomposition phase {stage_id}; "
                         'восстановите сохранённый договор через владельца Task')
    instruction = record['workflow']['stages'][record['stage_index']]['instruction']
    return {'id': stage_id, 'iteration': task.state.iteration,
            'role': node.role, 'purpose': node.purpose.value,
            'instruction': instruction, 'allowed_paths': list(contract.allowed_paths),
            'criteria': contract.criteria.to_dict(), 'skills': list(phase['skills'])}


def _context(record, history, captured_task, delivery):
    context = {
        'id': record['id'], 'goal': record.get('goal'), 'status': record['status'],
        'claimed_by': record['claimed_by'], 'sprint_id': record.get('sprint_id'),
        'worktree': record.get('worktree'), 'branch': record.get('branch'),
        'base': record.get('base'), 'history': [json.loads(event) for event in history],
        'stage': None,
    }
    if record['status'] == 'newborn':
        context.update(revision=record['revision'], draft=record['draft'],
                       ready=record['ready'], goal_type=record['goal_type'],
                       route_entry=record['route_entry'], workflow=record.get('workflow'))
        return context
    context.update(version=record['version'], requirements=record['contract']['requirements'],
                   definition_of_done=record['contract']['definition_of_done'])
    if is_terminal_task_status(record['status']):
        context.update(historical=True, result_commit=record.get('result_commit'),
                       last_report=record.get('last_report'), workflow=record['workflow'],
                       delivery=None if delivery is None else delivery.result(),
                       retirement_started=None if delivery is None else delivery.data['retirement_started'])
        return context
    task = captured_task.restore()
    context.update(stage=_stage_context(record, task), content=task.content_context(),
                   feedback=task.feedback_context(), pending=record['pending'])
    return context


def read_addressed(database, selector, project_record):
    with database.read_transaction() as db:
        match, matches = _matches(db, selector)
        if not matches:
            return {'status': 'not_found', 'query': selector, 'matches': [],
                    'next_action': 'Проверьте обозначение задачи и укажите полный ID или другой фрагмент'}
        if len(matches) > 1:
            return {'status': 'ambiguous', 'query': selector, 'matches': matches,
                    'next_action': 'Уточните фрагмент или укажите полный ID найденной задачи'}
        selected = matches[0]
        record_capture = capture_record_in(db, selected['id'])
        terminal = is_terminal_task_status(selected['status'])
        captured_task = (None if terminal or selected['status'] == 'newborn'
                         else capture_task(db, selected['id']))
        delivery = SqliteTaskDeliveryRepository(db).read(selected['id']) if terminal else None
    record = project_record(record_capture)
    return {'status': 'found', 'query': selector, 'match': match,
            'context': _context(record, record_capture.history, captured_task, delivery)}
