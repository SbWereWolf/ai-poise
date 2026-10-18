"""Caller-relative work availability from explicit Sequencing-owned facts."""


def project_eligibility(task_id, state, claimant, active_claim, predecessors):
    blockers = []
    provenance = []
    if state in ('completed', 'cancelled'):
        blockers.append({'reason': 'task_terminal', 'state': state})
    if active_claim is not None and active_claim['claimant'] != claimant:
        blockers.append({'reason': 'claimed_by_other', 'claim_id': active_claim['claim_id'],
                         'claimant': active_claim['claimant']})
    for predecessor in sorted(predecessors, key=lambda item: (item['predecessor'], item['kind'])):
        identity = predecessor['predecessor']
        kind = predecessor['kind']
        if predecessor['state'] != 'completed':
            blockers.append({'predecessor': identity, 'kind': kind, 'reason': 'predecessor_not_completed'})
        elif kind == 'result':
            if predecessor['result_commit'] is None:
                blockers.append({'predecessor': identity, 'kind': kind, 'reason': 'missing_predecessor_result'})
            else:
                provenance.append({'predecessor': identity, 'result_commit': predecessor['result_commit']})
    return {'task_id': task_id, 'eligible': not blockers,
            'blockers': blockers, 'result_provenance': provenance}
