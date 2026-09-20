"""Typed, explicitly selected task templates. No filesystem or goal-name dispatch."""
from __future__ import annotations
from copy import deepcopy
from dataclasses import dataclass
import json
from ..foundation.errors import DomainError
from ..verification.domain import exact_keys
from ..tasks.definition import validate_creation


def _parameter(value, kind, name):
    types = {'text': lambda v: isinstance(v,str) and bool(v.strip()),
             'nullable_text': lambda v: v is None or isinstance(v,str) and bool(v.strip()),
             'strings': lambda v: isinstance(v,list) and all(isinstance(x,str) and x.strip() for x in v),
             'list': lambda v: isinstance(v,list), 'object': lambda v: isinstance(v,dict)}
    if kind not in types or not types[kind](value):
        raise DomainError(f'Parameter {name} must satisfy explicit type {kind}')


def _references(node):
    if isinstance(node,dict):
        if '$input' in node:
            exact_keys(node,{'$input'},'typed placeholder')
            if not isinstance(node['$input'],str) or not node['$input']:
                raise DomainError('Placeholder requires a parameter name')
            return {node['$input']}
        result=set()
        for value in node.values():result.update(_references(value))
        return result
    if isinstance(node,list):
        result=set()
        for value in node:result.update(_references(value))
        return result
    return set()


def _render(node,values):
    if isinstance(node,dict):
        if '$input' in node:return deepcopy(values[node['$input']])
        return {k:_render(v,values) for k,v in node.items()}
    if isinstance(node,list):return [_render(v,values) for v in node]
    return node


@dataclass(frozen=True)
class TaskBlueprint:
    document: str

    @property
    def data(self):return json.loads(self.document)

    @property
    def goal_type(self):return self.data['goal_type']

    @classmethod
    def parse(cls,raw):
        exact_keys(raw,{'schema','goal_type','parameters','task'},'task blueprint')
        if raw['schema']!='task-blueprint-1':raise DomainError('Unsupported task blueprint schema')
        if not isinstance(raw['goal_type'],str) or not raw['goal_type'].strip():
            raise DomainError('Explicit goal type required')
        if not isinstance(raw['parameters'],dict) or not isinstance(raw['task'],dict):
            raise DomainError('Explicit parameter declarations and task object required')
        allowed={'text','nullable_text','strings','list','object'}
        if any(not isinstance(k,str) or not k or v not in allowed for k,v in raw['parameters'].items()):
            raise DomainError('Unknown parameter type/name')
        if _references(raw['task'])!=set(raw['parameters']):
            raise DomainError('Declared parameters must equal used typed placeholders')
        if raw['task'].get('goal_type')!=raw['goal_type']:
            raise DomainError('Task template has a different goal identity')
        return cls(json.dumps(raw,ensure_ascii=False,sort_keys=True,allow_nan=False))

    def materialize_draft(self, values):
        """Copy known starter values; unresolved parameters remain absent, not fake requirements."""
        raw = self.data
        if not isinstance(values, dict) or set(values) - set(raw['parameters']):
            raise DomainError('Task draft parameters must be declared by its template')
        for key, value in values.items():
            _parameter(value, raw['parameters'][key], key)
        return {key: _render(value, values) for key, value in raw['task'].items()
                if _references(value) <= set(values)}

    def instantiate(self,values,process,automatic_checks,decomposition_policy):
        raw=self.data
        exact_keys(values,set(raw['parameters']),'task template parameters')
        for key,kind in raw['parameters'].items():_parameter(values[key],kind,key)
        contract=_render(raw['task'],values)
        # Reuse the exact same owner as standalone/sprint creation. No second schema.
        validate_creation(contract,process,automatic_checks,decomposition_policy)
        return contract
