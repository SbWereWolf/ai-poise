"""Bind own external facts to the canonical, extracted evidence assessor."""
from copy import deepcopy

from apps.execution.application.gates import internal_record_id
from apps.execution.domain.evidence import EvidenceBook, EvidencePlan
from apps.execution.domain.progression import ProgressionPolicy
from apps.execution.domain.records import ExecutionRecord, RecordsError


class ExecutionEvidence:
    @staticmethod
    def load_book(repository, execution):
        record = repository.latest(execution['task_id'], 'evidence_state',
                                   f'evidence-book:{execution["generation"]}')
        return EvidenceBook.empty() if record is None else EvidenceBook.from_dict(record.data)

    @staticmethod
    def save_book(repository, execution, book):
        record_id = f'evidence-book:{execution["generation"]}'
        previous = repository.latest(execution['task_id'], 'evidence_state', record_id)
        value = book.to_dict()
        if previous is None or previous.data != value:
            repository.insert(ExecutionRecord('evidence_state', record_id, execution['task_id'],
                execution['generation'], 1 if previous is None else previous.revision + 1, value))

    @staticmethod
    def plan(process, contract):
        return EvidencePlan.parse(contract['evidence_plan'],
            {s['id']: s['handler'] for s in process['stages']},
            {s['stage_id']: s['methods'] for s in contract['stage_expectations']})

    @staticmethod
    def proof(repository, execution, process, stage, iteration, reference):
        task, generation = execution['task_id'], execution['generation']
        context = repository.latest(task, 'gate_context', internal_record_id(
            'gate_context', generation, reference['context_id']))
        result = repository.latest(task, 'stage_result', internal_record_id(
            'stage_result', generation, reference['context_id'], reference['candidate_id']))
        if context is None or result is None or not result.data['assessment']['passed']:
            raise RecordsError('Select an accepted current Task Gate context/candidate')
        provenance = result.data['provenance']
        for field, expected in [('stage_id', stage), ('iteration', iteration),
                ('contract_revision', execution['contract_revision'])]:
            if provenance[field] != expected:
                raise RecordsError(f'Current Gate {field} must match the current stage proof')
        evidence = repository.latest(task, 'evidence', reference['evidence_id'])
        if evidence is None:
            raise RecordsError('Select an evidence record for this Task')
        if evidence.generation != generation:
            raise RecordsError('Evidence generation must match the current execution')
        attempt = repository.latest(task, 'attempt', evidence.data['attempt_id'])
        if attempt is None or attempt.generation != generation:
            raise RecordsError('Select the current Task/generation attempt of this evidence')
        for field, expected in [('stage_id', stage), ('iteration', iteration),
                ('context_id', reference['context_id']), ('candidate_id', reference['candidate_id'])]:
            if attempt.data[field] != expected:
                raise RecordsError(f'Evidence attempt {field} must match the current Gate context/candidate')
        selected = next(s['methods'] for s in execution['contract']['stage_expectations'] if s['stage_id'] == stage)
        methods = [m for m in execution['contract']['methods'] if m['id'] in selected]
        return {'context': deepcopy(context.data), 'gate_id': result.record_id,
                'evidence_id': evidence.record_id, 'attempt': deepcopy(attempt.data),
                'work': deepcopy(evidence.data['evidence_work']), 'methods': methods,
                'proof_contract': ProgressionPolicy.stage_proof_contract(
                    process, execution['contract'], context.data, stage)}

    def assess_next(self, repository, execution, process, proof, source, outcome):
        reason = ProgressionPolicy.admitted_observations(proof['methods'], proof['attempt'], source)
        if reason:
            return None, reason
        if proof['attempt']['status'] != 'completed':
            return None, 'incomplete_capture'
        book = self.load_book(repository, execution).record_batch(execution['current_stage'],
            execution['iteration'], source['tree'], proof['attempt']['execution_key'], proof['attempt']['receipts'])
        assessment = book.assess(self.plan(process, execution['contract']), execution['current_stage'],
            execution['iteration'], source['tree'], proof['attempt']['execution_key'], proof['work'])
        if not assessment.ready:
            return None, ('missing_review' if any(x.startswith('review:') for x in assessment.missing)
                          else 'missing_argument')
        if assessment.outcome != outcome:
            raise RecordsError('Evidence decision must agree with the declared stage outcome')
        return assessment, None

    def assess_replay(self, repository, execution, proof, source, original):
        reason = ProgressionPolicy.admitted_observations(proof['methods'], proof['attempt'], source)
        if reason:
            return None, reason
        if proof['attempt']['status'] != 'completed':
            return None, 'incomplete_capture'
        current = self.load_book(repository, execution)
        saved = EvidenceBook.from_dict(original['book'])
        # Import only genuine accepted original facts; old arguments/decisions
        # retain their actors, revisions and source instead of being reauthored.
        book = EvidenceBook(*(tuple(dict.fromkeys([*getattr(saved, key), *getattr(current, key)]))
                              for key in ('batches', 'arguments', 'decisions')))
        attempt = proof['attempt']
        candidate = {'candidate_digest': proof['gate_id'], 'commit': source['commit'],
            'source_tree': original['source']['tree'], 'source_digest': original['visit_id'],
            'accepted_assessment': original['assessment'], 'outcome': original['outcome'],
            'method_ids': [m['id'] for m in proof['methods']]}
        book = book.record_submission_batch(execution['current_stage'], execution['iteration'],
            source['tree'], attempt['execution_key'], candidate['candidate_digest'], attempt['receipts'])
        assessment = book.assess_replay(execution['current_stage'], execution['iteration'],
                                      source['tree'], attempt['execution_key'], candidate)
        return (assessment, None) if assessment.ready else (None, 'missing_current_evidence')
