"""Detached history of one selected Task; no database access from its projections."""
from dataclasses import dataclass
import json
from copy import deepcopy
from ...modules.foundation.errors import PoiseError


@dataclass(frozen=True)
class TaskHistoryCapture:
    events: tuple
    journal: tuple
    submissions: tuple
    proofs: tuple

    def event_range(self, after_version, through_version):
        if through_version < after_version:
            return None
        rows = [row for row in self.events if after_version < row['version'] <= through_version]
        rows = sorted(rows, key=lambda row: row['version'])
        if [r['version'] for r in rows] != list(range(after_version + 1, through_version + 1)):
            return None
        return tuple((row['version'], json.loads(row['data'])) for row in rows)

    def ownership_event_suffix(self, after_version, through_version):
        suffix = self.event_range(after_version, through_version)
        if suffix is None:
            return ()
        events = tuple(data['event'] for _, data in suffix)
        return events if set(events) <= {'handed_off','handoff_resumed','ownership_acquired','ownership_released'} else ()

    def replay_progress(self, request_id):
        for row in reversed(self.journal):
            if row['event'] == 'progression.phase':
                data = json.loads(row['data'])
                if data['request_id'] == request_id:
                    return data['cursor']
        return None

    def progression_view(self, request_id=None):
        actions = [json.loads(row['data']) for row in self.journal if row['event'] == 'progression.started']
        if request_id is not None:
            actions = [a for a in actions if a['request_id'] == request_id]
        if not actions:
            return None
        action = actions[-1]
        reached = any(row['event'] in ('progression.reached', 'progression.result')
            and json.loads(row['data'])['request_id'] == action['request_id'] for row in self.journal)
        return {'request_id':action['request_id'], 'target_stage':action['target_stage'],
                'status':'reached' if reached else 'active'}

    def progression_request(self, header, request_id, digest):
        if header is not None and header['status'] in ('completed', 'cancelled'):
            terminal = next((json.loads(r['data']) for r in self.events if r['version'] == header['version']), None)
            expected = ('user_cancel',) if header['status'] == 'cancelled' else ('user_accept', 'duplicate_reuse_accepted')
            if terminal is None or terminal.get('event') not in expected:
                raise PoiseError('Terminal progression requires its native Task transition audit')
        rows = [r for r in self.journal if r['event'] in
                ('progression.started','progression.noop','progression.result')
                and json.loads(r['data'])['request_id'] == request_id]
        result = None
        for row in rows:
            value = json.loads(row['data'])
            if value['digest'] != digest:
                raise PoiseError('Progression request conflict: identity has another target or mode')
            if row['event'] in ('progression.noop','progression.result'):
                if result is not None:
                    raise PoiseError('Contradictory terminal progression identity')
                result = deepcopy(value['result'])
        return None if not rows else {'result':result, 'intent':json.loads(rows[0]['data'])}


def cancellation_recovery_point(history, task_id, version):
    from .tasks import TaskState, TaskStatus
    row = next((r for r in history.events if r['version'] == version
                and json.loads(r['data']).get('event') == 'user_cancel'), None)
    prior = None if row is None else json.loads(row["data"]).get("previous")
    fields = {"status", "stage_index", "iteration", "claimed_by", "version", "current_submission_id"}
    if not isinstance(prior, dict) or set(prior) != fields:
        raise PoiseError("Cancellation has no exact recovery point; do not guess historical state")
    digest = None
    if prior["current_submission_id"] is not None:
        submission = next((r for r in history.submissions
                           if r['seq'] == prior['current_submission_id']), None)
        if submission is None:
            raise PoiseError("Cancellation recovery submission is missing")
        digest = submission["digest"]
    return TaskState(task_id, prior["stage_index"], prior["iteration"],
                     TaskStatus.parse(prior["status"]), prior["claimed_by"], prior["version"], digest)



def capture_history(db, task_id):
    return TaskHistoryCapture(
        tuple(dict(r) for r in db.execute('SELECT seq,version,data FROM task_events '
            'WHERE task_id=? ORDER BY seq', (task_id,))),
        tuple(dict(r) for r in db.execute("SELECT seq,event,data FROM journal WHERE task_id=? "
            "AND event LIKE 'progression.%' ORDER BY seq", (task_id,))),
        tuple(dict(r) for r in db.execute('SELECT s.seq,s.stage,s.iteration,s.digest,s.data,'
            'w.data AS workflow,r.data AS report FROM submissions s '
            'LEFT JOIN workflow_layers w ON w.task_id=s.task_id AND w.submission_id=s.seq '
            'LEFT JOIN task_results r ON r.task_id=s.task_id AND r.submission_id=s.seq '
            'WHERE s.task_id=? ORDER BY s.seq', (task_id,))),
        tuple(dict(r) for r in db.execute('SELECT version,data FROM task_proof_layers '
            'WHERE task_id=? ORDER BY version', (task_id,))))


def empty_rework_recovery_point(history, task_id, handoff_version, last_report, transition_event):
    from .tasks import RouteProgress, EmptyReworkRecoveryPoint, FeedbackBook, EvidenceBook
    events = [r for r in history.events if r['version'] <= handoff_version]
    parsed = [
        (row["version"], json.loads(row["data"])["event"])
        for row in events
    ]
    transitions = [
        version for version, event in parsed if event == transition_event
    ]
    if not transitions:
        raise PoiseError(f"Recovery requires the exact preceding {transition_event} event")
    transition_version = transitions[-1]
    ownership_events = {
        "handed_off",
        "handoff_resumed",
        "ownership_acquired",
        "ownership_released",
    }
    unexpected = [
        event
        for version, event in parsed
        if transition_version < version <= handoff_version
        and event not in ownership_events
    ]
    if unexpected:
        raise PoiseError(
            f"Recovery event suffix contains non-ownership events: {unexpected}"
        )
    rows = [r for r in history.submissions if r['workflow'] is not None and r['report'] is not None]
    row = None if not rows else {**rows[-1], 'result':rows[-1]['report']}
    saved_result = None if row is None else json.loads(row["result"])
    expected_report = (
        None
        if saved_result is None
        else {
            **saved_result,
            "status": (
                "accepted"
                if transition_event == "user_accept_and_continue"
                else saved_result["status"]
            ),
        }
    )
    if row is None or expected_report != last_report:
        raise PoiseError("Recovery requires the immediately preceding verified result")
    if saved_result.get("status") != "verified":
        raise PoiseError("Recovery requires a previously verified result")
    workflow = json.loads(row["workflow"])
    envelope = json.loads(row["data"])
    proof_row = next((r for r in reversed(history.proofs) if r['version'] < transition_version), None)
    if proof_row is None:
        raise PoiseError("Recovery requires the previous immutable proof snapshot")
    proof = json.loads(proof_row["data"])
    feedback = workflow["feedback"]
    progress = RouteProgress(
        tuple(workflow["visits"].items()),
        workflow["transitions"],
        workflow["outcome"],
        json.dumps(envelope["stage_work"], sort_keys=True, ensure_ascii=False),
    )
    return EmptyReworkRecoveryPoint(
        row["stage"],
        row["iteration"],
        row["digest"],
        progress,
        FeedbackBook.from_dict(
            {name: feedback[name] for name in ("findings", "resolutions", "decisions")}
        ),
        EvidenceBook.from_dict(proof["book"]),
        proof["input"],
        proof["assessment"],
    )



def verified_ownership_history(history, header, task_id, stage_id, iteration, submission_digest, through_version):
    row = next((r for r in history.submissions if r['seq'] == header['current_submission_id']), None)
    if (row is None or header['version'] != through_version or row['digest'] != submission_digest
            or row['stage'] != stage_id or row['iteration'] != iteration):
        return None
    submission_id = header['current_submission_id']
    anchors = [r['version'] for r in history.events if r['version'] <= through_version
        and (data := json.loads(r['data'])).get('event') == 'verified'
        and data.get('submission') == submission_id and data.get('stage') == stage_id
        and data.get('iteration') == iteration]
    if not anchors:
        return None
    suffix = history.event_range(max(anchors) - 1, through_version)
    if suffix is None:
        return None
    return {'task_id':task_id, 'submission_id':submission_id, 'submission_digest':submission_digest,
            'stage_id':stage_id, 'iteration':iteration, 'through_version':through_version,
            'events':[{**data, 'version':version} for version, data in suffix]}


def begin_progression(history, header, task_id, actor, request_id, digest, target_stage):
    if not isinstance(request_id, str) or not request_id:
        raise PoiseError('Progression request_id is required')
    saved_request = history.progression_request(header, request_id, digest)
    if saved_request is not None and saved_request['result'] is not None:
        raise PoiseError('Progression request already has a terminal result')
    existing = history.progression_view(request_id)
    if existing is not None:
        saved = next(json.loads(row['data']) for row in reversed(history.journal)
            if row['event'] == 'progression.started' and json.loads(row['data'])['request_id'] == request_id)
        if saved['digest'] != digest:
            raise PoiseError('Progression request conflict: request_id has another target')
        return existing, None
    active = history.progression_view()
    if active is not None and active['status'] == 'active':
        raise PoiseError('Task already has an active progression target; resume its exact request')
    return ({'request_id':request_id, 'target_stage':target_stage, 'status':'active'},
            {'actor':actor, 'digest':digest, 'request_id':request_id, 'target_stage':target_stage})


@dataclass(frozen=True)
class ProgressionRequestCapture:
    header: dict | None
    history: TaskHistoryCapture

    @property
    def present(self):
        return bool(self.history.journal)

    def restore(self, request_id, digest):
        return self.history.progression_request(self.header, request_id, digest)
