"""Accepted-result equivalence and admission across public application facts."""
from copy import deepcopy

from apps.execution.domain.outcomes import OutcomePolicy
from apps.execution.domain.records import RecordsError, canonical_json


REUSE_KIND = 'reuse_candidate'


class ReusePolicy:
    @staticmethod
    def admit(admission, source_fact, task_id):
        family, eligibility = admission['family'], admission['eligibility']
        if not isinstance(family, dict) or not isinstance(family.get('members'), list):
            raise RecordsError('Reuse requires an explicit public duplicate family')
        members = {m['task']['task_id']: m['task'] for m in family['members']}
        source = members.get(source_fact['task_id'])
        target = members.get(task_id)
        if source is None or target is None or source['task_id'] == task_id:
            raise RecordsError('Reuse source and target must be distinct members of the same family')
        if source['state'] != 'completed' or source_fact['kind'] != 'positive' or not source_fact['result_commit']:
            raise RecordsError('Reuse requires a completed positive source with an accepted result commit')
        if eligibility.get('task_id') != task_id or eligibility.get('eligible') is not True:
            raise RecordsError('Reuse target is not eligible under supplied public admission')
        if target['state'] not in ('available', 'claimed'):
            raise RecordsError('Reuse target must be available or claimed by this caller')
        claim = target.get('claim')
        if claim is not None and claim.get('claimant') != admission['claimant']:
            raise RecordsError('Reuse target is claimed by another claimant')
        workspace = target.get('workspace')
        if workspace is not None and workspace['status'] != 'ready':
            raise RecordsError('Reuse requires the supplied Workspace to be ready')

    @staticmethod
    def compatible(source_contract, source_process, source_snapshot,
                   target_contract, target_process, target_snapshot):
        def contract(value):
            return {k: deepcopy(v) for k, v in value.items() if k not in (
                'task_id', 'workspace_id', 'admission_id', 'requirements_snapshot_revision')}

        def snapshot(value):
            result = deepcopy(value)
            own_task = result['task_id']
            for key in ('task_id', 'snapshot_revision', 'statement_revision', 'registry_revision'):
                result.pop(key, None)
            for key in ('task_id', 'statement_revision'):
                result['statement'].pop(key, None)
            for link in result['links']['application_task']:
                if link['task_id'] == own_task:
                    link['task_id'] = {'own_task': True}
            return result

        if (canonical_json(contract(source_contract)) != canonical_json(contract(target_contract))
                or canonical_json(source_process) != canonical_json(target_process)
                or canonical_json(snapshot(source_snapshot)) != canonical_json(snapshot(target_snapshot))):
            raise RecordsError('Reuse contract/process/requirements snapshot is not compatible with the accepted source')

    @staticmethod
    def produced_methods(contract):
        return [m['id'] for m in contract['methods'] if m['verification_plan']['green_stages']]

    @staticmethod
    def fact(execution, snapshot, source_fact, candidate, current_gate_id, value, role):
        if value['result_commit'] != candidate['source']['commit']:
            raise RecordsError('Reuse result_commit must match the freshly verified local source')
        provenance = {key: deepcopy(source_fact['provenance'][key]) for key in (
            'visit_id', 'source_generation', 'source_actor_ids', 'gate_id', 'evidence_id')}
        provenance.update(current_gate_id=current_gate_id, current_evidence_id=candidate['evidence_id'])
        return {'outcome_id': value['outcome_id'], 'task_id': execution['task_id'],
                'generation': execution['generation'], 'contract_revision': execution['contract_revision'],
                'requirements_snapshot_revision': execution['contract']['requirements_snapshot_revision'],
                'kind': 'positive', 'stage_id': execution['current_stage'], 'iteration': execution['iteration'],
                'handler_outcome': None, 'source': deepcopy(candidate['source']), 'provenance': provenance,
                'satisfied_task_requirements': OutcomePolicy._current_keys(snapshot),
                'result_commit': value['result_commit'],
                'decision': {'actor_id': value['actor_id'], 'role_id': role, 'intent': 'accept', 'reason': value['reason']},
                'reuse': {k: candidate[k] for k in ('source_task_id', 'source_outcome_id', 'reuse_id')}}
