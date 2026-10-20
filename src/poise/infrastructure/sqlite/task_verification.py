"""Immutable verification inputs and the canonical criteria provenance guard."""
from dataclasses import dataclass
import json


@dataclass(frozen=True)
class VerificationCapture:
    submission: dict | None
    layers: tuple
    events: tuple
    receipts: tuple
    packets: tuple


def capture_verification(db, task_id):
    row = db.execute('SELECT s.task_id,s.stage,s.iteration,s.digest,s.data FROM submissions s '
        'JOIN tasks t ON t.current_submission_id=s.seq WHERE t.id=?', (task_id,)).fetchone()
    return VerificationCapture(None if row is None else dict(row),
        tuple(dict(r) for r in db.execute('SELECT section_id,content,content_state '
            'FROM section_layers WHERE task_id=? AND submission_id='
            '(SELECT current_submission_id FROM tasks WHERE id=?)', (task_id, task_id))),
        tuple(dict(r) for r in db.execute('SELECT version,data FROM task_events WHERE task_id=? '
            'ORDER BY version,seq', (task_id,))),
        tuple(dict(r) for r in db.execute('SELECT id,stage,iteration,data FROM evidence '
            'WHERE task_id=? ORDER BY id', (task_id,))),
        tuple(tuple(r) for r in db.execute('SELECT stage,iteration,digest FROM work_packets '
            'WHERE task_id=? ORDER BY stage,iteration', (task_id,))))


def criteria_submission_guard(captured, header, execution, task, actor, report):
    from .tasks import PoiseError, split_work, SectionValue, ContentState, encode
    from ...modules.tasks.domain import Submission
    row = header
    guard = json.loads(row['metadata']).get('criteria_pending')
    if (not isinstance(guard, dict) or row['claimed_by'] != actor or guard['actor'] != actor
            or guard['submission'] != row['current_submission_id']
            or guard['stage'] != task.stage.stage_id
            or guard['iteration'] != task.state.iteration):
        raise PoiseError('criteria verification source guard mismatch')
    immutable = captured.submission
    if (immutable is None or immutable['task_id'] != task.state.task_id
            or immutable['stage'] != guard['stage'] or immutable['iteration'] != guard['iteration']
            or immutable['digest'] != task.state.submission_digest):
        raise PoiseError('criteria verification immutable submission mismatch')
    envelope = json.loads(immutable['data'])
    _, original = split_work(envelope['stage_work'])
    if (original is None or original.document != guard['intent']
            or original.data['expected_version'] != guard['entry_version']
            or envelope['stage_work'] != json.loads(task.progress.stage_work)):
        raise PoiseError('criteria verification immutable intent mismatch')
    layers = captured.layers
    submitted = Submission(
        tuple(SectionValue(item['section_id'], item['content'], ContentState(item['content_state']))
              for item in layers), tuple(envelope['artifact_paths']), envelope['commit_message'],
        encode(envelope['content_additions']), encode(envelope['trace']), (),
        encode(envelope['method_additions']), encode(envelope['stage_work']), encode(envelope['evidence_work']))
    if submitted.digest != immutable['digest']:
        raise PoiseError('criteria verification immutable content digest mismatch')
    events = [event for event in captured.events
              if guard['entry_version'] < event['version'] <= task.state.version]
    if [e['version'] for e in events] != list(range(guard['entry_version'] + 1, task.state.version + 1)):
        raise PoiseError('criteria verification version history incomplete')
    allowed = {'submitted', 'observations_recorded', 'evidence_assessed', 'action_assessed'}
    for event in events:
        value = json.loads(event['data'])
        if (value['event'] not in allowed or value['submission'] != guard['submission']
                or value.get('actor') != actor
                or value['stage'] != guard['stage'] or value['iteration'] != guard['iteration']):
            raise PoiseError('criteria verification intervening change')
    if not events or json.loads(events[0]['data'])['event'] != 'submitted':
        raise PoiseError('criteria verification entry event mismatch')
    _, work = split_work(json.loads(task.progress.stage_work))
    if work is None or work.document != guard['intent']:
        raise PoiseError('criteria verification intent mismatch')
    publication = execution['publication']
    assessed = None if task.evidence_assessment is None else json.loads(task.evidence_assessment)
    if (execution['pending'] is not None or not isinstance(publication, dict)
            or publication['payload_hash'] != immutable['digest']
            or publication['tree'] != report['verified_tree']
            or publication['commit'] != report['verification_commit']
            or publication['checks'] != report['checks']
            or assessed is None or assessed['submission_digest'] != immutable['digest']
            or assessed['tree'] != publication['tree']
            or assessed['execution_key'] != publication['execution_key']):
        raise PoiseError('criteria verification attempt/assessment mismatch')
    batch = task.evidence_book.submission_batch(guard['stage'], guard['iteration'],
        immutable['digest'], publication['tree'], publication['execution_key'])
    if batch is None or batch['receipts'] != report['checks']:
        raise PoiseError('criteria verification observations mismatch')
    for receipt in batch['receipts']:
        stored = next((item for item in captured.receipts if item['id'] == receipt['id']
            and item['stage'] == guard['stage'] and item['iteration'] == guard['iteration']), None)
        if (stored is None or json.loads(stored['data']) != receipt
                or receipt['submission_digest'] != immutable['digest']
                or receipt['execution_key'] != publication['execution_key']
                or receipt['tree'] != publication['tree']):
            raise PoiseError('criteria verification receipt identity mismatch')
    return guard['submission']

