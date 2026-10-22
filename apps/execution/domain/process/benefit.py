"""Pure benefit declaration extracted without accounting runtime."""

from dataclasses import dataclass

from .errors import DomainError

def exact(value, fields, label):
    if not isinstance(value,dict) or set(value)!=set(fields):
        raise DomainError(f'{label}: required explicit fields {sorted(fields)}')

def names(value, label):
    if not isinstance(value,list) or any(not isinstance(x,str) or not x.strip() for x in value) or len(value)!=len(set(value)):
        raise DomainError(f'{label}: explicit unique names required')

@dataclass(frozen=True)
class BenefitDefinition:
    git_categories: tuple[str,...]
    sections: tuple[str,...]

    @classmethod
    def parse(cls,value):
        exact(value,{'git_categories','sections'},'benefit')
        names(value['git_categories'],'benefit.git_categories');names(value['sections'],'benefit.sections')
        return cls(tuple(value['git_categories']),tuple(value['sections']))
