"""Declarative presentation contracts. They never decide a check's verdict."""
from dataclasses import dataclass
from copy import deepcopy
import math
import re
from ..artifact_factory.domain import exact, relative
from ..foundation.errors import DomainError


def _positive(value, where, integral=True):
    if type(value) not in ((int,) if integral else (int,float)) or value<=0 or not math.isfinite(value):
        raise DomainError(f'{where} requires an explicit finite positive value')


@dataclass(frozen=True)
class OutputPolicy:
    data: dict

    @classmethod
    def parse(cls,value):
        exact(value,{'known','unknown','directory','manifest','job','worker_stdout','worker_stderr',
                     'chunk_bytes','worker_timeout_seconds','file_mode'},'output policy')
        for k in ('directory','manifest','job','worker_stdout','worker_stderr'):relative(value[k])
        names=[value[k] for k in ('manifest','job','worker_stdout','worker_stderr')]
        if len(names)!=len(set(names)):raise DomainError('Output control filenames must be distinct')
        _positive(value['chunk_bytes'],'chunk_bytes')
        _positive(value['worker_timeout_seconds'],'worker_timeout_seconds',False)
        if type(value['file_mode']) is not int or not 0<=value['file_mode']<=0o777:
            raise DomainError('Explicit file mode required')
        if not isinstance(value['known'],list):raise DomainError('known profiles must be a list')
        ids=[]
        for profile in [*value['known'],value['unknown']]:
            exact(profile,{'id','argv_tokens','parser','selection_pattern','primary_chars','extended_bytes','selected_bytes','files'},'output profile')
            if not isinstance(profile['id'],str) or not profile['id']:raise DomainError('Profile ID required')
            ids.append(profile['id'])
            tokens=profile['argv_tokens']
            if not isinstance(tokens,list) or any(not isinstance(x,str) or not x for x in tokens):raise DomainError('argv_tokens is a list of exact tokens')
            if profile['parser'] not in ('tail','lines'):raise DomainError('Unknown parser, no fallback')
            pat=profile['selection_pattern']
            if profile['parser']=='tail' and pat is not None:raise DomainError('Tail parser requires explicit null pattern')
            if profile['parser']=='lines':
                if not isinstance(pat,str) or not pat:raise DomainError('Line parser requires explicit pattern')
                try:re.compile(pat)
                except re.error as exc:raise DomainError('Invalid output selection pattern') from exc
            for k in ('primary_chars','extended_bytes','selected_bytes'):_positive(profile[k],k)
            exact(profile['files'],{'extended','full','selected'},'view files')
            for k in ('extended','full'):relative(profile['files'][k])
            if profile['files']['selected'] is not None:relative(profile['files']['selected'])
            if profile['parser']=='lines' and profile['files']['selected'] is None:raise DomainError('Line parser requires a selected view filename')
            filenames=[v for v in profile['files'].values() if v is not None]
            if len(set(filenames+names))!=len(filenames+names):raise DomainError('Duplicate result/control filenames')
        if len(ids)!=len(set(ids)):raise DomainError('Duplicate profile ID')
        if value['unknown']['argv_tokens']:raise DomainError('Unknown profile is explicit catch-all without a matcher')
        if any(not p['argv_tokens'] for p in value['known']):raise DomainError('Known profiles require a matcher')
        return cls(deepcopy(value))

    def select(self,argv):
        matches=[]
        for p in self.data['known']:
            tokens=p['argv_tokens']
            if any(argv[i:i+len(tokens)]==tokens for i in range(len(argv)-len(tokens)+1)):
                matches.append(p)
        if len(matches)>1:raise DomainError('Ambiguous known command profiles')
        return deepcopy(matches[0] if matches else self.data['unknown'])
