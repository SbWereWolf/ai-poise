"""Caller identity and the Session's immutable first-role business rule."""
from dataclasses import dataclass, replace
import json

from domain.generic import Problem, exact_fields, text


@dataclass(frozen=True)
class Caller:
    installation: str
    mode: str
    token: str

    @property
    def key(self):
        return json.dumps([self.installation, self.mode, self.token],
                          ensure_ascii=False, separators=(',', ':'))

    @property
    def native_session(self):
        return self.token if self.mode == 'native' else None


@dataclass(frozen=True)
class Session:
    id: str
    origin: str
    status: str
    revision: int
    role: str | None
    role_assignment: dict | None

    @classmethod
    def initial(cls, identity, origin):
        return cls(identity, origin, 'open', 1, None, None)

    def view(self):
        return {'id': self.id, 'origin': self.origin, 'status': self.status,
                'revision': self.revision, 'role': self.role,
                'role_assignment': self.role_assignment,
                'locked': self.role_assignment is not None}

    def assign(self, assignment):
        if self.status == 'closed':
            raise Problem('session_closed', {'status': self.status}, 'select:session')
        role = assignment['role']
        if self.role is not None:
            if self.role != role:
                raise Problem('role_locked', {'current_role': self.role,
                              'requested_role': role}, 'select:session')
            return self, False
        revision = self.revision + 1
        provenance = {'request_id': assignment['request_id'],
                      'authorization_ref': assignment['authorization_ref'],
                      'assigned_revision': revision}
        return replace(self, revision=revision, role=role,
                       role_assignment=provenance), True


def assignment_input(value, roles):
    exact_fields(value, {'request_id', 'role', 'authorization_ref'}, '/input')
    for key in ('request_id', 'role', 'authorization_ref'):
        text(value[key], '/input/' + key)
    if value['role'] not in roles:
        raise Problem('role_not_allowed', {'role': value['role']}, 'select:role')
    return value
