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


@dataclass(frozen=True)
class ArtifactRecoveryIntent:
    request_id: str
    task_id: str
    expected_version: int
    artifact_ids: tuple[str, ...]
    source_roots: dict[str, str]
    reason: str
    authorization: str

    @classmethod
    def parse(cls, raw, max_items):
        exact(raw, {'request_id','task_id','expected_version','artifact_ids',
                    'source_roots','reason','authorization'}, 'artifact recovery')
        for key in ('request_id','task_id','reason','authorization'):
            if not isinstance(raw[key], str) or not raw[key].strip() or '\0' in raw[key]:
                raise DomainError(f'Artifact recovery {key} is required')
        if type(raw['expected_version']) is not int or raw['expected_version'] < 0:
            raise DomainError('Artifact recovery expected_version must be nonnegative')
        ids = raw['artifact_ids']
        if (not isinstance(ids, list) or not 0 < len(ids) <= max_items
                or any(not isinstance(x,str) or not x for x in ids)
                or len(ids) != len(set(ids))):
            raise DomainError('Artifact recovery requires unique bounded registered artifact IDs')
        roots = raw['source_roots']
        if not isinstance(roots,dict) or not roots or set(roots) - {'task','sprint'}:
            raise DomainError('Artifact recovery requires explicit task/sprint source owner roots')
        for root in roots.values():
            if not isinstance(root,str) or not root.startswith('/'):
                raise DomainError('Artifact recovery source owner root must be absolute')
            relative(root[1:])
        return cls(raw['request_id'],raw['task_id'],raw['expected_version'],tuple(ids),
                   dict(roots),raw['reason'],raw['authorization'])


@dataclass(frozen=True)
class ArtifactDraftIntent:
    action: str
    request_id: str
    task_id: str
    expected_version: int
    drafts: tuple[dict, ...]
    artifacts: tuple[dict, ...]
    artifact_ids: tuple[str, ...]
    reason: str
    authorization: str | None

    @classmethod
    def parse(cls, raw, max_items):
        if not isinstance(raw, dict) or raw.get('action') not in {
            'declare', 'recover', 'review', 'finalize'
        }:
            raise DomainError('Artifact draft action must be declare/recover/review/finalize')
        action = raw['action']
        common = {'action','request_id','task_id','expected_version','reason'}
        fields = {
            'declare': common | {'drafts'},
            'recover': common | {'artifacts','authorization'},
            'review': common | {'artifact_ids','authorization'},
            'finalize': common | {'artifact_ids','authorization'},
        }[action]
        exact(raw, fields, 'artifact draft request')
        for key in ('request_id','task_id','reason'):
            if not isinstance(raw[key],str) or not raw[key].strip() or '\0' in raw[key]:
                raise DomainError(f'Artifact draft {key} is required')
        if type(raw['expected_version']) is not int or raw['expected_version'] < 0:
            raise DomainError('Artifact draft expected_version must be nonnegative')
        authorization = raw.get('authorization')
        if action != 'declare' and (
            not isinstance(authorization,str) or not authorization.strip() or '\0' in authorization
        ):
            raise DomainError('Artifact draft authorization is required')
        drafts = raw.get('drafts', [])
        artifacts = raw.get('artifacts', [])
        artifact_ids = raw.get('artifact_ids', [])
        selected = drafts if action == 'declare' else artifacts if action == 'recover' else artifact_ids
        if not isinstance(selected,list) or not 0 < len(selected) <= max_items:
            raise DomainError('Artifact draft selection must be a nonempty bounded list')
        if action == 'declare':
            seen = set()
            for item in drafts:
                exact(item, {'scope','path'}, 'artifact draft declaration')
                if item['scope'] != 'task':
                    raise DomainError('Artifact draft scope must be task')
                relative(item['path'])
                key = (item['scope'],item['path'])
                if key in seen: raise DomainError('Artifact draft declarations must be unique')
                seen.add(key)
        elif action == 'recover':
            seen = set()
            for item in artifacts:
                exact(item, {'id','scope','path'}, 'artifact draft recovery target')
                if (not isinstance(item['id'],str) or not item['id']
                        or item['scope'] != 'task'):
                    raise DomainError('Artifact draft recovery target scope/identity is invalid')
                relative(item['path'])
                key = (item['id'],item['scope'],item['path'])
                if key in seen: raise DomainError('Artifact draft recovery targets must be unique')
                seen.add(key)
        else:
            if (any(not isinstance(item,str) or not item for item in artifact_ids)
                    or len(artifact_ids) != len(set(artifact_ids))):
                raise DomainError('Artifact draft IDs must be unique nonempty strings')
        return cls(action,raw['request_id'],raw['task_id'],raw['expected_version'],
                   tuple(drafts),tuple(artifacts),tuple(artifact_ids),raw['reason'],authorization)


@dataclass(frozen=True)
class RegisteredArtifactMaterial:
    """Physical input authority keeps the original registered identity intact."""
    artifact_id: str
    scope: str
    owner: str
    logical_path: str
    digest: str
    physical_path: str
    delivery_id: str | None

    @classmethod
    def active(cls, record):
        return cls(record['id'], record['scope'], record['owner'], record['path'],
                   record['digest'], record['path'], None)

    @classmethod
    def delivered(cls, record, destination, delivery_id):
        if not isinstance(delivery_id, str) or not delivery_id:
            raise DomainError('Permanent artifact material requires explicit delivery authority')
        return cls(record['id'], record['scope'], record['owner'], record['path'],
                   record['digest'], destination, delivery_id)

    def matches(self, record):
        return (self.artifact_id, self.scope, self.owner, self.logical_path, self.digest) == (
            record['id'], record['scope'], record['owner'], record['path'], record['digest'])
