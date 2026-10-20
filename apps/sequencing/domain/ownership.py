"""Pure permissions for local Task ownership and saved handoffs."""


class OwnershipError(ValueError):
    pass


def require_source(task_id, claim_id, claimant, active_claim):
    if (active_claim is None or active_claim['task_id'] != task_id
            or active_claim['claim_id'] != claim_id or active_claim['claimant'] != claimant):
        raise OwnershipError('claim_owner_mismatch')


def require_different_target(source, target):
    if source == target:
        raise OwnershipError('switch_target_conflict')


def require_target(state, active_claim, eligibility, session_claim, exempt_task_id=None):
    if state in ('completed', 'cancelled'):
        raise OwnershipError('not_eligible')
    if active_claim is not None:
        raise OwnershipError('claim_conflict')
    if not eligibility['eligible']:
        raise OwnershipError('not_eligible')
    if session_claim is not None and session_claim['task_id'] != exempt_task_id:
        raise OwnershipError('session_claim_conflict')


def require_handoff(handoff, task_id, reference, consumed):
    if (handoff['task_id'] != task_id or consumed
            or any(handoff.get(key) != value for key, value in reference.items())):
        raise OwnershipError('claim_owner_mismatch')


def require_same_intent(saved, current):
    if saved != current:
        raise OwnershipError('request_reuse_conflict')
