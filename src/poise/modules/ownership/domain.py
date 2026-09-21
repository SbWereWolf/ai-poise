from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Liveness(str, Enum):
    LIVE = "live"
    DEAD = "dead"
    UNCERTAIN = "uncertain"


class UnobservedSessionLiveness:
    """Fail-closed policy when no authoritative native lifecycle is available."""

    def __call__(self, actor):
        return Liveness.UNCERTAIN


@dataclass(frozen=True)
class OwnershipSnapshot:
    actor: str
    task_id: str | None
    worktree_task_id: str | None


@dataclass(frozen=True)
class OwnershipPreflight:
    actor: str
    task_id: str
    task_owner: str | None
    worktree_owner: str | None


@dataclass(frozen=True)
class OwnershipChange:
    before: OwnershipSnapshot
    after: OwnershipSnapshot
    recovered_sessions: tuple[str, ...]


def parse_legacy_repair(value):
    """An explicit release-only decision, never a request to invent an owner."""
    from copy import deepcopy
    from ..foundation.errors import PoiseError
    fields = {'request_id', 'task_ids', 'expected_snapshot', 'task_claims',
              'worktree_bindings', 'reason', 'authorization'}
    if not isinstance(value, dict) or set(value) != fields:
        raise PoiseError('recover_ownership requires an exact decision object')
    for key in ('request_id', 'expected_snapshot', 'reason', 'authorization'):
        text = value[key]
        if not isinstance(text, str) or not text.strip() or '\0' in text:
            raise PoiseError(f'recover_ownership requires explicit {key}')
    stamp = value['expected_snapshot']
    if len(stamp) != 64 or any(c not in '0123456789abcdef' for c in stamp):
        raise PoiseError('recover_ownership requires an exact snapshot digest')
    tasks = value['task_ids']
    if (not isinstance(tasks, list) or not tasks
            or any(not isinstance(t, str) or not t or '\0' in t for t in tasks)
            or len(set(tasks)) != len(tasks)):
        raise PoiseError('recover_ownership requires unique explicit task_ids')
    for key in ('task_claims', 'worktree_bindings'):
        mapping = value[key]
        if (not isinstance(mapping, dict)
                or any(not isinstance(k, str) or not k or '\0' in k for k in mapping)
                or any(v is not None and (not isinstance(v, str) or not v or '\0' in v)
                       for v in mapping.values())):
            raise PoiseError(f'recover_ownership requires exact {key}')
    return deepcopy(value)


def legacy_releases(intent, component):
    """Validate the entire connected scope before returning the actors to release."""
    from ..foundation.errors import PoiseError
    tasks = {row['id']: row['claimed_by'] for row in component['tasks']}
    sessions = {row['id']: row['task_id'] for row in component['sessions']}
    if (set(intent['task_claims']) != set(tasks)
            or set(intent['worktree_bindings']) != set(sessions)):
        raise PoiseError('Ownership decision must cover the exact conflict component')
    released = set()
    for name, before in (('task_claims', tasks), ('worktree_bindings', sessions)):
        for key, old in before.items():
            target = intent[name][key]
            if target is not None and target != old:
                raise PoiseError('Ownership repair is release-only; no new owner or binding')
            if old is not None and target is None:
                released.add(old if name == 'task_claims' else key)
        retained = [v for v in intent[name].values() if v is not None]
        if len(retained) != len(set(retained)):
            raise PoiseError('Ownership decision leaves duplicate owners unresolved')
    if not released:
        raise PoiseError('Ownership repair must resolve an actual conflict')
    return released


CRASH_RECOVERY_FIELDS = frozenset({
    'mode', 'request_id', 'task_ids', 'expected_snapshot', 'reason',
    'writers_stopped', 'authorization',
})


def recovery_task_ids(value):
    from ..foundation.errors import PoiseError
    if (not isinstance(value, list) or not value
            or any(not isinstance(t, str) or not t.strip() or '\0' in t for t in value)
            or len(set(value)) != len(value)):
        raise PoiseError('Ownership recovery requires unique explicit task_ids')
    return sorted(value)


def parse_crash_recovery(value):
    """An operator revocation decision, not inferred native session termination."""
    from copy import deepcopy
    from ..foundation.errors import PoiseError
    if not isinstance(value, dict) or set(value) != CRASH_RECOVERY_FIELDS:
        raise PoiseError('Crash recovery requires an exact decision object')
    if value['mode'] != 'after_crash' or value['writers_stopped'] is not True:
        raise PoiseError('Crash recovery requires after_crash and confirmed stopped writers')
    recovery_task_ids(value['task_ids'])
    for key in ('request_id', 'expected_snapshot', 'reason'):
        text = value[key]
        if not isinstance(text, str) or not text.strip() or '\0' in text:
            raise PoiseError(f'Crash recovery requires explicit {key}')
    digest = value['expected_snapshot']
    if len(digest) != 64 or any(c not in '0123456789abcdef' for c in digest):
        raise PoiseError('Crash recovery requires an exact snapshot digest')
    auth = value['authorization']
    if (not isinstance(auth, dict) or set(auth) != {'role', 'decision'}
            or auth['role'] != 'user' or not isinstance(auth['decision'], str)
            or not auth['decision'].strip() or '\0' in auth['decision']):
        raise PoiseError('Crash ownership recovery requires explicit user authorization')
    return deepcopy(value)
