"""Own ordinary movement and replay of genuinely accepted stage results."""
from copy import deepcopy
import json

from apps.execution.application.gates import GateCommands
from apps.execution.domain.lifecycle import LifecyclePolicy
from apps.execution.domain.process.workflow import RouteDefinition
from apps.execution.domain.progression import ProgressionPolicy
from apps.execution.domain.records import RecordsError


class ExecutionProgression:
    def __init__(self, unit_of_work, settings, state_owner, evidence):
        self.unit_of_work, self.settings = unit_of_work, settings
        self.state, self.evidence = state_owner, evidence

    def execute(self, value):
        digest = GateCommands.request_digest('advance', value)
        with self.unit_of_work(write=True) as repository:
            prior = repository.get_request(value['request_id'])
            if prior is not None:
                if prior['digest'] != digest:
                    raise RecordsError('Advance request has different input; use its original request or a new id')
                return dict(json.loads(prior['response_json']), replayed=True)
            revision = repository.get_revision()
            if revision != value['expected_revision']:
                raise RecordsError('Advance revision conflict; read the current revision')
            execution = self.state.execution(repository, value['task_id'])
            if execution['generation'] != value['expected_generation']:
                raise RecordsError('Advance generation conflict')
            if execution['contract_revision'] != value['expected_contract_revision']:
                raise RecordsError('Advance contract revision conflict')
            role = LifecyclePolicy.actor(self.settings, value['actor_id'])
            LifecyclePolicy.require_state(execution, ('active', 'verified'))
            process = self.state.process(repository, execution)
            route = RouteDefinition.from_workflow(process)
            state = self.state.state(repository, execution)
            if state['entry_tree'] != value['entry_tree']:
                raise RecordsError('Advance entry_tree must match the current execution entry')
            before = deepcopy(execution)
            if value['action'] == 'next':
                status, reason, traversed = self._next(repository, execution, state, process, route, value, role)
            else:
                status, reason, traversed = self._fast_forward(repository, execution, state, process, route, value)
            if execution != before:
                repository.save_execution_state(execution, before)
            self.state.save_state(repository, execution, state)
            response = {'schema': 'stride-execution/v1', 'status': status, 'revision': revision + 1,
                'replayed': False, **{k: execution[k] for k in ('task_id', 'generation',
                    'contract_revision', 'state', 'current_stage', 'iteration')},
                'traversed': traversed,
                'required_role': None if status == 'route_end' else route.node(execution['current_stage']).role,
                'stop_reason': reason}
            repository.set_revision(revision + 1)
            repository.save_request(value['request_id'], digest, response)
            return response

    @staticmethod
    def _move(execution, state, route, outcome, gate_id):
        target = route.node(execution['current_stage']).target(outcome)
        if target is None:
            execution['state'] = 'verified'
            state['presented_result'] = gate_id
            return 'route_end'
        iteration = state['visits'].get(target, 0) + 1
        state['visits'][target] = iteration
        execution.update(state='active', current_stage=target, iteration=iteration)
        state['presented_result'] = None
        # performer_actor_id is the last actual performer, never an assignment
        # of this initiator to the newly reached stage.
        return 'advanced'

    def _traversal(self, repository, execution, value, original, proof, index):
        result = {key: deepcopy(original[key]) for key in ('stage_id', 'visit_id',
            'source_generation', 'source_actor_ids', 'gate_id', 'evidence_id', 'outcome')}
        result.update(iteration=execution['iteration'], current_evidence_id=proof['evidence_id'])
        self.state.put(repository, execution, 'advance_traversal',
            f'advance-traversal:{value["request_id"]}:{index}',
            {**result, 'request_id': value['request_id'], 'initiator_actor_id': value['actor_id']})
        return result

    def _next(self, repository, execution, state, process, route, value, role):
        LifecyclePolicy.require_state(execution, ('verified',))
        if role != route.node(execution['current_stage']).role:
            raise RecordsError('New stage outcome requires its configured role')
        reference = value['proof']
        proof = self.evidence.proof(repository, execution, process, execution['current_stage'],
                                    execution['iteration'], reference)
        if proof['gate_id'] != state['presented_result']:
            raise RecordsError('New outcome must use the actually presented Gate context/candidate')
        next_stage = route.node(execution['current_stage']).target(reference['outcome'])
        assessment, reason = self.evidence.assess_next(repository, execution, process, proof,
                                                       value['source'], reference['outcome'])
        if reason:
            return 'work_required', reason, []
        visit_id = f'accepted-visit:{execution["generation"]}:{value["request_id"]}'
        original = {'stage_id': execution['current_stage'], 'iteration': execution['iteration'],
            'visit_id': visit_id, 'source_generation': execution['generation'],
            'source_actor_ids': list(dict.fromkeys([state['performer_actor_id'], value['actor_id']])),
            'gate_id': proof['gate_id'], 'evidence_id': proof['evidence_id'],
            'outcome': reference['outcome'], 'next_stage': next_stage,
            'source': deepcopy(value['source']), 'proof_contract': proof['proof_contract'],
            'book': assessment.book.to_dict(),
            'assessment': {**assessment.to_dict(), 'tree': value['source']['tree'], 'submission_digest': visit_id}}
        self.state.put(repository, execution, 'accepted_visit', visit_id, original)
        self.evidence.save_book(repository, execution, assessment.book)
        traversal = self._traversal(repository, execution, value, original, proof, 0)
        status = self._move(execution, state, route, reference['outcome'], proof['gate_id'])
        return status, 'route_end' if status == 'route_end' else 'new_work', [traversal]

    def _fast_forward(self, repository, execution, state, process, route, value):
        target = value['target_stage']
        if target is not None:
            if target not in {node.stage_id for node in route.nodes}:
                raise RecordsError('Advance target stage is not declared')
            if not route.can_reach(execution['current_stage'], target):
                raise RecordsError('Advance target is not reachable')
        sources = ProgressionPolicy.accepted_sources(repository.list_records(execution['task_id']),
                                                    execution['generation'])
        proofs = {proof['stage_id']: proof for proof in value['proofs']}
        traversed, consumed = [], set()
        while True:
            stage = execution['current_stage']
            if stage == target:
                return 'target_reached', 'target_reached', traversed
            original = sources.get(stage)
            if original is None:
                return 'work_required', 'missing_source', traversed
            if original.get('reason'):
                return 'work_required', original['reason'], traversed
            if original['visit_id'] in consumed:
                return 'work_required', 'visited_source', traversed
            if stage not in proofs:
                return 'work_required', 'missing_current_evidence', traversed
            proof = self.evidence.proof(repository, execution, process, stage, execution['iteration'], proofs[stage])
            if proof['proof_contract'] != original['proof_contract']:
                return 'work_required', 'changed_contract', traversed
            if route.node(stage).target(original['outcome']) != original['next_stage']:
                return 'work_required', 'changed_contract', traversed
            assessment, reason = self.evidence.assess_replay(repository, execution, proof, value['source'], original)
            if reason:
                return 'work_required', reason, traversed
            consumed.add(original['visit_id'])
            self.evidence.save_book(repository, execution, assessment.book)
            traversed.append(self._traversal(repository, execution, value, original, proof, len(traversed)))
            if self._move(execution, state, route, original['outcome'], proof['gate_id']) == 'route_end':
                return 'route_end', 'route_end', traversed

    def query(self, repository, task_id):
        execution = self.state.execution(repository, task_id)
        records = repository.list_records(task_id)
        return {'execution': self.state.view(execution),
                'accepted_visits': [deepcopy(r.data) for r in records if r.kind == 'accepted_visit'],
                'traversals': [deepcopy(r.data) for r in records if r.kind == 'advance_traversal'],
                'accepted_arguments': self.evidence.load_book(repository, execution).to_dict()['arguments']}
