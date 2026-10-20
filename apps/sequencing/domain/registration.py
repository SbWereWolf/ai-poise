"""Sequencing-owned identities, immutable provenance and initial Sprint graphs."""
from collections import deque
from dataclasses import dataclass


class RegistrationError(ValueError):
    pass


@dataclass(frozen=True)
class RequirementsReference:
    kind: str
    revision: int

    def __post_init__(self):
        if self.kind not in ('statement', 'snapshot'):
            raise RegistrationError('Unknown Requirements reference kind')
        if type(self.revision) is not int or self.revision < 1:
            raise RegistrationError('Requirements reference needs a positive integer revision')

    def require_same(self, other):
        if self != other:
            raise RegistrationError('identity_conflict')


@dataclass(frozen=True, order=True)
class Dependency:
    predecessor: str
    successor: str
    kind: str


@dataclass(frozen=True)
class SprintDefinition:
    sprint_id: str
    members: tuple[str, ...]
    dependencies: tuple[Dependency, ...]

    @classmethod
    def create(cls, sprint_id, members, dependencies):
        identities = set(members)
        if not members or len(identities) != len(members):
            raise RegistrationError('dependency_conflict')
        adjacency = {identifier: [] for identifier in identities}
        indegree = dict.fromkeys(identities, 0)
        seen = set()
        for edge in dependencies:
            pair = (edge.predecessor, edge.successor)
            if (edge.predecessor not in identities or edge.successor not in identities
                    or pair in seen or edge.kind not in ('completion', 'result')):
                raise RegistrationError('dependency_conflict')
            seen.add(pair)
            adjacency[edge.predecessor].append(edge.successor)
            indegree[edge.successor] += 1
        pending = deque(identifier for identifier in sorted(identities) if indegree[identifier] == 0)
        visited = 0
        while pending:
            identifier = pending.popleft()
            visited += 1
            for successor in adjacency[identifier]:
                indegree[successor] -= 1
                if indegree[successor] == 0:
                    pending.append(successor)
        if visited != len(identities):
            raise RegistrationError('dependency_conflict')
        return cls(sprint_id, tuple(sorted(members)), tuple(sorted(dependencies)))

    def require_registered(self, known_ids):
        if not set(self.members).issubset(known_ids):
            raise RegistrationError('dependency_conflict')

    def with_member(self, task_id):
        return self.create(self.sprint_id, [*self.members, task_id], self.dependencies)

    def require_memberships(self, current_memberships):
        if any(sprint_id != self.sprint_id for sprint_id in current_memberships.values()):
            raise RegistrationError('dependency_conflict')

    def require_same(self, existing):
        if self != existing:
            raise RegistrationError('already_exists')
