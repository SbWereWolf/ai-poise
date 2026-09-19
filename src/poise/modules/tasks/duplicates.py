"""Pure duplicate-family distance and stage-admission policy."""
from collections import deque
from ..foundation.errors import DomainError
from ..workflow.domain import RouteDefinition


class DuplicateStartBlocked(DomainError):
    """Structured refusal, not a completed Task or a failed external command."""
    def __init__(self, decision):
        self.decision = decision
        super().__init__(decision['reason'])


def positive_terminal_distances(process):
    """Reverse breadth-first traversal of the saved, directed unit-edge route."""
    route = RouteDefinition.from_process(process)
    reverse = {node.stage_id: [] for node in route.nodes}
    distance = {}
    queue = deque()
    for node in route.nodes:
        for outcome, target in node.transitions:
            if target is not None:
                reverse[target].append(node.stage_id)
            elif outcome in ('complete', 'clear', 'satisfied'):
                if node.stage_id not in distance:
                    distance[node.stage_id] = 1
                    queue.append(node.stage_id)
        for target in node.rework_targets:
            reverse[target].append(node.stage_id)
    while queue:
        target = queue.popleft()
        for predecessor in reverse[target]:
            if predecessor not in distance:
                distance[predecessor] = distance[target] + 1
                queue.append(predecessor)
    return distance


def assess_duplicate_start(family, current_id, actor, *, force_duplicate_start=False,
                           target_stage=None):
    """Assess a fresh family snapshot without writing claims or remembering force."""
    if type(force_duplicate_start) is not bool:
        raise ValueError('force_duplicate_start must be boolean')
    if family is None:
        return None
    members = family['members']
    current = next((m for m in members if m['task_id'] == current_id), None)
    if current is None:
        raise DomainError('Incomplete duplicate family: current Task is absent')
    ranked, invalid = [], []
    for member in members:
        if member['status'] == 'cancelled':
            continue
        # Drafts have no executable position. They are visible in the family but
        # do not invent a stage or stop an existing, ready implementation.
        if member['status'] == 'newborn' and member['task_id'] != current_id:
            continue
        stage = target_stage if member is current and target_stage is not None else member['stage_id']
        if member['status'] == 'completed':
            d = 0
        else:
            try:
                d = positive_terminal_distances(member['process']).get(stage)
            except (DomainError, TypeError, KeyError, ValueError):
                d = None
        if d is None:
            invalid.append(member['task_id'])
        else:
            ranked.append({k: v for k, v in member.items() if k != 'process'} | {'stage_id': stage, 'distance': d})
    minimum = min((m['distance'] for m in ranked), default=None)
    now = next((m for m in ranked if m['task_id'] == current_id), None)
    nearest = [m for m in ranked if m['distance'] == minimum]
    result = {
        'parent_id': family['parent_id'],
        'role': 'parent' if family['parent_id'] == current_id else 'child',
        'distance': now['distance'] if now else None,
        'minimum': minimum,
        'relatives': [m for m in nearest if m['task_id'] != current_id],
        'allowed': False, 'reason': 'invalid_duplicate_distance',
    }
    if invalid and (not current.get('local_repair') or current_id in invalid):
        return result | {'invalid_task_ids': sorted(invalid)}
    if current['status'] in ('completed', 'cancelled', 'newborn'):
        return result | {'reason': 'task_not_executable'}
    if current['session_id'] not in (None, actor):
        return result | {'reason': 'foreign_owner'}
    if now is None or minimum is None:
        return result
    if current.get('local_repair'):
        return result | {'allowed': True, 'reason': 'local_repair',
                         'source': current['local_repair']}
    if now['distance'] > minimum:
        return result | {'reason': 'family_ahead'}
    owned = sum(m['session_id'] is not None or (m['task_id'] == current_id and actor is not None)
                for m in nearest)
    if owned >= 2 and not force_duplicate_start:
        return result | {'reason': 'duplicate_sessions'}
    return result | {'allowed': True, 'reason': 'eligible'}


def family_projection(family, task_id):
    if family is None:
        return None
    parent = family['parent_id']
    children = [m['task_id'] for m in family['members'] if m['task_id'] != parent]
    value = {'kind': 'duplicate', 'role': 'parent' if task_id == parent else 'child',
             'parent_id': parent}
    return value | ({'children': children} if task_id == parent else
                    {'siblings': [tid for tid in children if tid != task_id]})
