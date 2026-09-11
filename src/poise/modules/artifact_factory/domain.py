"""Artifact creation intent. Pure rendering; no file system or implicit templates."""
from dataclasses import dataclass
from string import Template
from ..foundation.errors import DomainError


def exact(value, keys, label):
    if not isinstance(value,dict) or set(value)!=set(keys):
        raise DomainError(f'{label}: required fields {sorted(keys)}')


def relative(value):
    if not isinstance(value,str) or not value or '\\' in value or '\x00' in value:
        raise DomainError('Artifact path must be a nonempty relative POSIX path')
    if any(part in ('','..','.') for part in value.split('/')):
        raise DomainError('Artifact path must remain below the selected owner directory')
    return value


@dataclass(frozen=True)
class ArtifactItem:
    scope: str
    path: str
    content: str


@dataclass(frozen=True)
class ArtifactPlan:
    items: tuple[ArtifactItem,...]

    @classmethod
    def parse(cls, raw, config):
        if not isinstance(raw,list) or len(raw)>config['max_items']:
            raise DomainError('Artifact batch exceeds explicit max_items or is not a list')
        seen={}
        for spec in raw:
            exact(spec,{'scope','path','source'},'artifact')
            scope=spec['scope']
            if scope not in ('runtime','task','sprint'):
                raise DomainError('Artifact owner must be runtime/task/sprint')
            path=relative(spec['path'])
            source=spec['source']
            if not isinstance(source,dict) or 'kind' not in source:
                raise DomainError('Artifact source.kind is required')
            if source['kind']=='text':
                exact(source,{'kind','text'},'text source')
                text=source['text']
            elif source['kind']=='template':
                exact(source,{'kind','id','version','values'},'template source')
                if not isinstance(source['id'],str) or source['id'] not in config['templates']:
                    raise DomainError('Unknown explicit artifact template')
                definition=config['templates'][source['id']]
                if source['version']!=definition['version']:
                    raise DomainError('Artifact template version mismatch')
                exact(source['values'],definition['parameters'],'template parameters')
                if any(not isinstance(v,str) for v in source['values'].values()):
                    raise DomainError('Template parameter values must be text')
                try:
                    text=Template(definition['text']).substitute(source['values'])
                except (ValueError,KeyError) as exc:
                    raise DomainError(f'Invalid explicit artifact template: {exc}') from exc
            else:
                raise DomainError('Unknown artifact source kind')
            if not isinstance(text,str):
                raise DomainError('Artifact content must be text')
            try:
                size=len(text.encode('utf-8'))
            except UnicodeError as exc:
                raise DomainError('Artifact text must be valid UTF-8') from exc
            if size>config['max_artifact_bytes']:
                raise DomainError('Artifact exceeds configured max_artifact_bytes')
            item=ArtifactItem(scope,path,text)
            key=(scope,path)
            if key in seen and seen[key]!=item:
                raise DomainError('Conflicting content for the same artifact path')
            seen[key]=item
        return cls(tuple(seen.values()))
