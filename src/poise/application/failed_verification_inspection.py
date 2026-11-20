"""A limited independent review of frozen corrections after a terminal check failure."""

from dataclasses import asdict, replace

from ..common import digest
from ..modules.foundation.errors import PoiseError
from ..modules.tasks.domain import TaskStatus
from ..modules.tasks.ports import TaskReadSet
from ..modules.workflow.domain import HandlerKind
from ..modules.workflow.handlers import evaluate_stage

FROZEN_FIELDS = frozenset({
    'origin', 'iteration', 'submission', 'commit', 'tree', 'execution_key', 'batch',
    'executor', 'history_length', 'inspector', 'corrective', 'proposals', 'artifacts',
})
MARKER_FIELDS = FROZEN_FIELDS | {
    'kind', 'version', 'entry_request_id', 'reviewer', 'inspector_iteration', 'decisions',
}


def limited_inspection(pending):
    return isinstance(pending, dict) and pending.get('kind') == 'failed_verification_inspection'


class FailedVerificationInspection:
    def __init__(self, runtime):
        self.runtime = runtime
        self.unit_of_work = runtime.task_commands.unit_of_work

    @staticmethod
    def key(request_id, phase):
        return f'failed-verification-inspection:{request_id}:{phase}'

    def apply(self, args):
        runtime = self.runtime
        task_id, action = args['task_id'], args['action']
        identity = digest({'operation': 'failed_verification_inspection', 'input': args})
        offer_key = self.key(args['request_id'], 'offer')
        receipt_key = self.key(args['request_id'], 'committed')
        facade = self.unit_of_work()
        with self.unit_of_work() as uow:
            saved = uow.tasks.action_receipt_raw(task_id, receipt_key, identity)
            offered = uow.tasks.action_receipt_raw(task_id, offer_key, identity)
        if saved is not None:
            committed = saved.result()
            if committed['actor'] != runtime.session:
                raise PoiseError('Limited inspection receipt belongs to another actor')
            return {**committed['response'], 'replayed': True}

        data = runtime.current_task()
        claim = runtime.ownership.snapshot(runtime.session)
        if (data is None or data['id'] != task_id or data['claimed_by'] != runtime.session
                or claim.task_id != task_id
                or (data['worktree'] is not None and claim.worktree_task_id != task_id)):
            raise PoiseError('Limited inspection requires the current Task and worktree owner')
        history = runtime.task_queries.history(task_id)
        inputs = facade.capture(TaskReadSet(
            (task_id,), execution_ids=(task_id,), family_ids=(task_id,),
            material_ids=(task_id,), evidence_ids=(task_id,), history_ids=(task_id,),
            verification_ids=(task_id,), actors=(runtime.session,),
        ))
        task = inputs.task(task_id)
        execution, version = inputs.required_execution(task_id)
        if task.state.version != data['_version'] or task.state.claimed_by != runtime.session:
            raise PoiseError('Limited inspection Task ownership or version changed')
        offer = None if offered is None else {
            key: value for key, value in offered.result().items() if key != 'replayed'
        }
        if action == 'enter':
            outcome = self.enter(args, task, data, execution, history, offer)
        else:
            marker = execution['pending']
            if (not limited_inspection(marker) or set(marker) != MARKER_FIELDS or marker.get('version') != 1
                    or marker.get('entry_request_id') != args['entry_request_id']):
                raise PoiseError('Limited inspection entry is unavailable or changed')
            outcome = self.inspect(args, task, data, execution, marker, history, offer)
        change, next_execution, response, frozen = outcome
        response = {'task': task_id, **response, 'interaction': runtime.interactions.summary(data)}
        if frozen is not None and offer is not None:
            return {**response, 'replayed': True}
        phase_key = offer_key if frozen is not None else receipt_key
        value = frozen if frozen is not None else {'actor': runtime.session, 'response': response}
        prepared = facade.prepare_action_receipt(phase_key, identity, value)
        write = None if change is None else facade.prepare_change(inputs, change)
        execution_write = (None if next_execution is None
                           else facade.prepare_execution(inputs, task_id, next_execution))
        if action != 'decide' and change is not None:
            from .duplicate_tasks import require_duplicate_transition
            require_duplicate_transition(inputs, task, change, runtime.session)
        with self.unit_of_work() as uow:
            existing = uow.tasks.action_receipt_raw(task_id, phase_key, identity)
            if existing is not None:
                raise PoiseError('Limited inspection request committed concurrently; repeat the exact request')
            uow.require_inputs(inputs)
            if write is not None:
                uow.tasks.save_prepared(write)
            if execution_write is not None:
                uow.execution.save_prepared(task_id, execution_write, version)
            uow.tasks.remember_prepared_action(task_id, runtime.session, prepared)
        return {**response, 'replayed': False}

    @staticmethod
    def role_boundary(stage):
        return {'status': 'role_handoff_required', 'next_stage': stage}

    def enter(self, args, task, data, execution, history, offer):
        runtime = self.runtime
        if execution['pending'] is not None:
            raise PoiseError('Unknown check outcome or pending check attempt; resolve it before inspection')
        if task.state.status != TaskStatus.ACTIVE or task.state.submission_digest is None:
            raise PoiseError('Limited inspection requires an active submitted failed candidate')
        if offer is None:
            frozen = self.freeze(args, task, data, history)
            return None, None, self.role_boundary(frozen['inspector']), frozen
        if set(offer) != FROZEN_FIELDS:
            raise PoiseError('Frozen failed-verification inspection offer is malformed')
        if runtime.session == offer['executor']:
            return None, None, self.role_boundary(offer['inspector']), offer
        self.transferred(history, offer['history_length'], offer['executor'])
        if ((task.stage.stage_id, task.state.iteration, task.state.submission_digest) !=
                (offer['origin'], offer['iteration'], offer['submission'])):
            raise PoiseError('Failed verification candidate changed before independent inspection')
        self.validate_frozen(task, data, offer, pending=True)
        target = offer['inspector']
        self.require_entry(task, data, target)
        change = task._enter(
            'failed_verification_inspection_entered', args['reason'], runtime.session,
            target, task.route.enter(task.progress, target),
        )
        marker = {**offer, 'kind': 'failed_verification_inspection', 'version': 1,
                  'entry_request_id': args['request_id'], 'reviewer': runtime.session,
                  'inspector_iteration': change.task.state.iteration, 'decisions': []}
        return change, {**execution, 'pending': marker}, {
            'status': 'limited_inspection_active', 'stage': target, 'failed_check_id': args['failed_check_id'],
        }, None

    def inspect(self, args, task, data, execution, marker, history, offer):
        runtime = self.runtime
        if (task.state.status != TaskStatus.ACTIVE or task.stage.stage_id != marker['inspector']
                or task.state.iteration != marker['inspector_iteration']):
            raise PoiseError('Limited inspection stage or iteration changed')
        self.validate_frozen(task, data, marker, pending=not marker['decisions'])
        if args['action'] == 'decide':
            if runtime.session != marker['reviewer'] or runtime.session == marker['executor']:
                raise PoiseError('Only the independent limited-inspection reviewer may decide corrections')
            if marker['decisions']:
                raise PoiseError('Limited inspection decisions are already recorded')
            decisions = args['decisions']
            identifiers = [decision['resolution_id'] for decision in decisions]
            if len(identifiers) != len(set(identifiers)) or set(identifiers) != set(marker['proposals']):
                raise PoiseError('Limited inspection requires decisions for the exact pending proposals')
            handled = evaluate_stage(task.route, {
                'coverage': 'Frozen failed-verification correction proposals.',
                'findings': [], 'resolution_decisions': decisions,
            }, task.feedback, task.stage.stage_id, task.state.iteration)
            change = task._change('failed_verification_inspection_decided', None, None)
            change = replace(change, task=replace(change.task, feedback=handled.feedback))
            return change, {**execution, 'pending': {**marker, 'decisions': decisions}}, {
                'status': 'limited_inspection_decided', 'stage': task.stage.stage_id,
                'decisions': decisions,
            }, None

        if not marker['decisions']:
            raise PoiseError('Limited inspection cannot exit before independent decisions are complete')
        corrective = marker['corrective']
        if runtime.session == marker['reviewer']:
            offered = {'reviewer': marker['reviewer'], 'history_length': len(history),
                       'entry_request_id': marker['entry_request_id']}
            return None, None, self.role_boundary(corrective), offered if offer is None else offer
        if (runtime.session != marker['executor'] or offer is None
                or set(offer) != {'reviewer', 'history_length', 'entry_request_id'}
                or offer['reviewer'] != marker['reviewer']
                or offer['entry_request_id'] != marker['entry_request_id']):
            raise PoiseError('Limited inspection exit requires the entitled executor and a reviewer handoff')
        self.transferred(history, offer['history_length'], offer['reviewer'])
        self.require_entry(task, data, corrective)
        change = task._enter(
            'failed_verification_inspection_exited', args.get('reason'), runtime.session,
            corrective, task.route.enter(task.progress, corrective),
        )
        return change, {**execution, 'pending': None, 'entry_tree': marker['tree'],
                        'attempts': 0, 'publication': None}, {
            'status': 'limited_inspection_exited', 'stage': corrective, 'decisions': marker['decisions'],
        }, None

    def freeze(self, args, task, data, history):
        runtime = self.runtime
        origin = task.stage.stage_id
        workspace = runtime._verification_workspace(data)
        scope = runtime.validate_stage_scope(data)
        tree = scope['tree']
        commit = runtime._git(workspace, 'rev-parse', 'HEAD')
        runtime._require_verification_identity(data, commit, tree)
        checks = runtime._select_checks(data, scope['changed'])
        invocations, key = runtime._verification_execution(data, tree, checks, workspace)
        batch = task.evidence_book.failed_batch(origin, task.state.iteration, task.state.submission_digest, tree, key)
        if batch is None or not any(
            item['id'] == args['failed_check_id'] and item['guard'] and not item['passed']
            for item in batch['receipts']
        ):
            raise PoiseError('Failed receipt is unavailable for the current candidate commit and batch')
        self.require_interpretable_batch(batch)
        if not runtime._intact_receipts(data['id'], batch['receipts'], invocations, tree):
            raise PoiseError('Failed check receipt evidence is missing or changed')
        submitted = [event for event in history if event['event'] == 'submitted'
                     and event['stage'] == origin and event['iteration'] == task.state.iteration]
        if not submitted or submitted[-1]['actor'] != runtime.session:
            raise PoiseError('Failed candidate requires its original submitting executor')
        proposals = tuple(task.feedback.pending_resolutions)
        inspector = task.route.next_inspection(origin)
        if (not proposals or inspector is None or any(
                (task.route.report_inspector(proposal.stage) or task.route.next_inspection(proposal.stage)) != inspector
                for proposal in proposals)):
            raise PoiseError('No single configured inspector owns the pending correction proposals')
        corrective = task.route.node(inspector).target('changes_requested')
        self.require_route(task, origin, inspector, corrective)
        frozen = {'origin': origin, 'iteration': task.state.iteration,
                  'submission': task.state.submission_digest, 'commit': commit, 'tree': tree,
                  'execution_key': key, 'batch': digest(batch), 'executor': runtime.session,
                  'history_length': len(history), 'inspector': inspector, 'corrective': corrective,
                  'proposals': {proposal.id: asdict(proposal) for proposal in proposals},
                  'artifacts': self.correction_materials(data, proposals, history)}
        self.validate_frozen(task, data, frozen, pending=True)
        return frozen

    def correction_materials(self, data, proposals, history):
        runtime = self.runtime
        producing = {(proposal.stage, proposal.iteration) for proposal in proposals}
        verified = {(event['stage'], event['iteration']): event['submission']
                    for event in history if event['event'] == 'verified'}
        records = {item['id']: item for item in runtime.store.artifact_records(data['id'])}
        drafts = runtime.store.artifact_drafts(data['id'])
        material = {}
        for stage in sorted(producing):
            if verified.get(stage) is None:
                raise PoiseError('Frozen correction evidence is unavailable for a producing submission')
            report = runtime.task_queries.result(data['id'], verified[stage])
            for reference in report['artifacts']:
                record = records.get(reference['id'])
                if record is None or record['path'] != reference['path'] or reference['id'] in drafts:
                    raise PoiseError('Frozen correction artifact evidence is unavailable')
                material[record['id']] = record
        return material

    def validate_frozen(self, task, data, frozen, *, pending):
        runtime = self.runtime
        runtime._require_verification_identity(data, frozen['commit'], frozen['tree'])
        if runtime._current_tree(data) != frozen['tree']:
            raise PoiseError('Frozen failed candidate source tree changed')
        self.require_route(task, frozen['origin'], frozen['inspector'], frozen['corrective'])
        batch = task.evidence_book.failed_batch(
            frozen['origin'], frozen['iteration'], frozen['submission'], frozen['tree'], frozen['execution_key'],
        )
        if batch is None or digest(batch) != frozen['batch']:
            raise PoiseError('Frozen failed receipt batch is unavailable or changed')
        self.require_interpretable_batch(batch)
        recorded = runtime._recorded_check_receipts(data['id'])
        for receipt in batch['receipts']:
            method = next((item for item in data['contract']['methods']
                           if item['id'] == receipt['method']), None)
            if (method is None or recorded.get(receipt['id']) != receipt
                    or not runtime._receipt_files_intact(receipt, method)):
                raise PoiseError('Frozen failed receipt evidence is missing or changed')
        proposals = {proposal.id: asdict(proposal) for proposal in task.feedback.resolutions}
        if any(proposals.get(key) != value for key, value in frozen['proposals'].items()):
            raise PoiseError('Frozen correction proposal evidence changed')
        if pending and {proposal.id for proposal in task.feedback.pending_resolutions} != set(frozen['proposals']):
            raise PoiseError('Frozen correction proposal decisions changed before inspection')
        if not pending:
            recorded_decisions = {
                item.resolution_id: (item.decision, item.reason, item.stage, item.iteration)
                for item in task.feedback.decisions if item.resolution_id in frozen['proposals']
            }
            expected_decisions = {
                item['resolution_id']: (item['decision'], item['reason'], frozen['inspector'],
                                        frozen['inspector_iteration'])
                for item in frozen['decisions']
            }
            if recorded_decisions != expected_decisions or set(expected_decisions) != set(frozen['proposals']):
                raise PoiseError('Frozen independent decisions are unavailable or changed')
        current = {item['id']: item for item in runtime._existing_artifacts(data)}
        if any(any(current.get(key, {}).get(field) != material[field]
                   for field in ('path', 'digest', 'scope', 'owner'))
               for key, material in frozen['artifacts'].items()):
            raise PoiseError('Frozen correction artifact evidence is missing or changed')

    @staticmethod
    def require_interpretable_batch(batch):
        if any(receipt['interpretable'] is not True for receipt in batch['receipts']):
            raise PoiseError('Failed verification batch contains an uninterpretable result; establish its outcome before inspection')

    @staticmethod
    def require_route(task, origin, inspector, corrective):
        if (task.route.next_inspection(origin) != inspector
                or task.route.node(inspector).handler != HandlerKind.INSPECT
                or not corrective or corrective == origin
                or task.route.node(inspector).target('changes_requested') != corrective
                or task.route.node(corrective).handler in (HandlerKind.INSPECT, HandlerKind.PUBLISH)
                or task.route.node(corrective).role != task.route.node(origin).role
                or task.route.node(inspector).role == task.route.node(origin).role):
            raise PoiseError('No eligible corrective destination on the existing inspection route')

    def require_entry(self, task, data, target):
        gate = task.assess_stage_content(
            target, 'pre', self.runtime._artifact_facts(self.runtime._existing_artifacts(data)),
        )
        if not gate.passed:
            raise PoiseError('Limited inspection destination entry requirements are not satisfied')

    def transferred(self, history, index, previous_owner):
        events = history[index:]
        if (len(events) != 2 or events[0]['event'] != 'ownership_released'
                or events[1]['event'] != 'ownership_acquired'
                or events[1]['actor'] != self.runtime.session or self.runtime.session == previous_owner):
            raise PoiseError('Limited inspection requires native release and acquisition by the other role')
