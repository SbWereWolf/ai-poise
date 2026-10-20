"""Immutable family identity and the existing parent/child projection."""
from dataclasses import asdict, dataclass

from apps.sequencing.domain.registration import RegistrationError, RequirementsReference


@dataclass(frozen=True)
class DuplicateIdentity:
    parent_id: str
    reference: RequirementsReference
    sprint_id: str

    def require_same(self, saved):
        if asdict(self) != saved:
            raise RegistrationError('duplicate_conflict')


def family_projection(family, task_id):
    """Extracted from the original Task domain, without its stage-admission policy."""
    if family is None:
        return None
    parent = family['parent_id']
    children = [m['task_id'] for m in family['members'] if m['task_id'] != parent]
    value = {'kind': 'duplicate', 'role': 'parent' if task_id == parent else 'child',
             'parent_id': parent}
    return value | ({'children': children} if task_id == parent else
                    {'siblings': [tid for tid in children if tid != task_id]})
