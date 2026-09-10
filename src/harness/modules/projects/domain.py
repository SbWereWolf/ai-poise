"""Explicit project templates and questionnaires; no files, Git or task state."""
from __future__ import annotations
from copy import deepcopy
from dataclasses import dataclass
import json
from ..foundation.errors import DomainError
from ..verification.domain import exact_keys


def path_key(value):
    if not isinstance(value,list) or not value or any(not isinstance(x,str) or not x for x in value):
        raise DomainError('An edit requires a non-empty list of field names')
    return tuple(value)


def field_at(document, path):
    value=document
    for key in path:
        if not isinstance(value,dict) or key not in value:
            raise DomainError(f'Unknown project field: {".".join(path)}')
        value=value[key]
    return value


def answer_type(value,kind):
    if kind=='text' and isinstance(value,str) and value.strip():return
    if kind=='boolean' and type(value) is bool:return
    if kind=='strings' and isinstance(value,list) and all(isinstance(x,str) and x for x in value):return
    raise DomainError(f'Explicit answer must have type {kind}')


@dataclass(frozen=True)
class ProjectBlueprint:
    document: str

    @property
    def data(self):return json.loads(self.document)

    @classmethod
    def parse(cls,raw):
        exact_keys(raw,{'schema','version','config','process_sources','questions'},'project blueprint')
        if raw['schema']!='project-blueprint-1':raise DomainError('Unsupported project template schema')
        if not isinstance(raw['version'],str) or not raw['version']:raise DomainError('Explicit version required')
        if not isinstance(raw['config'],dict) or not isinstance(raw['process_sources'],dict):
            raise DomainError('Complete project and process sources required')
        if not isinstance(raw['questions'],list) or not raw['questions']:raise DomainError('Explicit questions required')
        ids=set();paths=set()
        for q in raw['questions']:
            exact_keys(q,{'id','path','type','prompt'},'project question')
            if any(not isinstance(q[k],str) or not q[k].strip() for k in ('id','prompt')):
                raise DomainError('Question identity and prompt required')
            p=path_key(q['path']);field_at(raw['config'],p)
            if q['id'] in ids or p in paths:raise DomainError('Duplicate question or field')
            if q['type'] not in {'text','boolean','strings'}:raise DomainError('Unknown answer type')
            ids.add(q['id']);paths.add(p)
        for item in raw['process_sources'].values():
            exact_keys(item,{'path','digest'},'process source')
            if any(not isinstance(item[k],str) or not item[k] for k in item):raise DomainError('Process path/digest required')
        try:return cls(json.dumps(raw,ensure_ascii=False,sort_keys=True,allow_nan=False))
        except (ValueError,TypeError) as exc:raise DomainError(f'Invalid template data: {exc}') from exc

    def build(self,edits,max_edits):
        if type(max_edits) is not int or max_edits<=0:raise DomainError('Explicit edit limit required')
        if not isinstance(edits,list) or len(edits)>max_edits:raise DomainError('Edit batch limit exceeded')
        raw=self.data;candidate=deepcopy(raw['config']);changed={}
        for item in edits:
            exact_keys(item,{'path','value'},'project edit')
            p=path_key(item['path']);field_at(raw['config'],p)
            if any(p[:len(old)]==old or old[:len(p)]==p for old in changed):
                raise DomainError('Conflicting duplicate or parent/child project edits')
            changed[p]=item['value']
        for q in raw['questions']:
            p=tuple(q['path'])
            if p not in changed:raise DomainError(f'Explicit answer missing: {q["id"]}')
            answer_type(changed[p],q['type'])
        for p,value in changed.items():
            parent=field_at(candidate,p[:-1]);parent[p[-1]]=deepcopy(value)
        try:json.dumps(candidate,allow_nan=False)
        except (ValueError,TypeError) as exc:raise DomainError(f'Invalid edit value: {exc}') from exc
        return candidate


class Survey:
    """Navigation never silently selects an answer. keep is an explicit choice."""
    def __init__(self,blueprint):
        self.blueprint=blueprint;self.questions=blueprint.data['questions'];self.position=0;self.answers={}

    @property
    def complete(self):return self.position==len(self.questions)

    def current(self):
        if self.complete:raise DomainError('Review answers before publication')
        return deepcopy(self.questions[self.position])

    def value(self):
        q=self.current()
        return deepcopy(self.answers[q['id']] if q['id'] in self.answers else field_at(self.blueprint.data['config'],q['path']))

    def answer(self,value):
        q=self.current();answer_type(value,q['type']);self.answers[q['id']]=deepcopy(value);self.position+=1

    def keep(self):self.answer(self.value())

    def back(self):self.position=max(0,self.position-1)

    def edits(self):
        if not self.complete:raise DomainError('Questionnaire is incomplete')
        return [{'path':q['path'],'value':deepcopy(self.answers[q['id']])} for q in self.questions]

    def candidate(self,max_edits):return self.blueprint.build(self.edits(),max_edits)
