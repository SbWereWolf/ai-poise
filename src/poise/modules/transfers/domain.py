"""Portable saved-work contracts. No storage, process execution or implicit policy."""
from copy import deepcopy
from dataclasses import dataclass
import re
from ..foundation.errors import DomainError
from ..artifact_factory.domain import exact, relative
from ..tasks.definition import path_identifier

TRANSFER_FORMAT = 'poise-recovery-1'


@dataclass(frozen=True)
class TransferPolicy:
    data: dict

    @classmethod
    def parse(cls, raw):
        paths = {'directory', 'archive', 'manifest', 'database', 'files_directory'}
        if not isinstance(raw, dict) or 'recovery' not in raw:
            raise DomainError('transfer.recovery required')
        limits = {'max_tasks', 'max_files', 'max_file_bytes', 'max_total_bytes', 'chunk_bytes'}
        exact(raw, paths | limits | {'file_mode', 'recovery'}, 'transfer config')
        RecoveryPolicy.parse(raw['recovery'])
        for key in paths:
            try:
                relative(raw[key])
            except DomainError as exc:
                raise DomainError(f'runtime_services.transfer.{key}: {exc}') from exc
        names = [raw[k] for k in paths - {'directory'}]
        if len(names) != len(set(names)) or any(a.startswith(b + '/') or b.startswith(a + '/')
                                                for i,a in enumerate(names) for b in names[i+1:]):
            raise DomainError('Transfer paths must be distinct and non-overlapping')
        for key in limits:
            if type(raw[key]) is not int or raw[key] <= 0:
                raise DomainError(f'Explicit positive transfer.{key} required')
        if type(raw['file_mode']) is not int or not 0 <= raw['file_mode'] <= 0o777:
            raise DomainError('Explicit transfer.file_mode required')
        return cls(deepcopy(raw))


@dataclass(frozen=True)
class RecoveryPolicy:
    data: dict

    @classmethod
    def parse(cls, raw):
        fields = {'format', 'tool_argv', 'mapping', 'systems', 'repositories', 'paths',
                  'placement_decisions', 'create_flow', 'restore_flow', 'component_steps'}
        exact(raw, fields, 'recovery required fields')
        if raw['format'] != TRANSFER_FORMAT:
            raise DomainError('Unsupported recovery format')
        argv = raw['tool_argv']
        if not isinstance(argv, list) or not argv or any(not isinstance(x, str) or not x for x in argv):
            raise DomainError('recovery.tool_argv required')
        exact(raw['mapping'], {'version', 'materials'}, 'recovery.mapping')
        if not isinstance(raw['mapping']['version'], str) or not raw['mapping']['version']:
            raise DomainError('mapping.version required')
        if not isinstance(raw['mapping']['materials'], list):
            raise DomainError('mapping.materials required')
        seen = set()
        for item in raw['mapping']['materials']:
            exact(item, {'id', 'role', 'source', 'destination', 'include'}, 'mapping material')
            path_identifier(item['id'])
            if item['id'] in seen or not isinstance(item['role'], str) or not item['role']:
                raise DomainError('Unique material id and explicit role required')
            seen.add(item['id']); relative(item['source'])
            if type(item['include']) is not bool:
                raise DomainError('Explicit material include required')
            if item['include']:
                relative(item['destination'])
            elif item['destination'] is not None:
                raise DomainError('Excluded material destination must be null')
        exact(raw['systems'], {'poise', 'project'}, 'recovery.systems')
        for owner, spec in raw['systems'].items():
            exact(spec, {'repository', 'configs'}, 'systems.' + owner)
            if not isinstance(spec['repository'], str) or not spec['repository'].startswith('/'):
                raise DomainError('systems.' + owner + '.repository must be absolute')
            if not isinstance(spec['configs'], list) or not spec['configs'] or any(
                    not isinstance(x, str) or not x.startswith('/') for x in spec['configs']):
                raise DomainError('systems.' + owner + '.configs required')
        exact(raw['repositories'], {'task'}, 'recovery.repositories')
        if not isinstance(raw['repositories']['task'], str) or not raw['repositories']['task'].startswith('/'):
            raise DomainError('recovery.repositories.task must be absolute')
        exact(raw['paths'], {'staging', 'create_session', 'restore_session'}, 'recovery.paths')
        for value in raw['paths'].values(): relative(value)
        for key in ('placement_decisions', 'component_steps'):
            if not isinstance(raw[key], list): raise DomainError('recovery.' + key + ' required')
        for key in ('create_flow', 'restore_flow'):
            flow = raw[key]
            if not isinstance(flow, dict) or flow.get('schema') != 'workspace-recover/flow/v3' or not isinstance(flow.get('steps'), list) or not flow['steps']:
                raise DomainError('recovery.' + key + ' flow/v3 required')
        return cls(deepcopy(raw))


def placement_plan(source, target, decisions):
    included = [x for x in source['materials'] if x['include']]
    if source['version'] == target['version']:
        return {x['id']: x['destination'] for x in included}, None
    for decision in decisions:
        exact(decision, {'source_version', 'target_version', 'reason', 'destinations'}, 'placement decision')
        if (decision['source_version'], decision['target_version']) != (source['version'], target['version']):
            continue
        if not isinstance(decision['reason'], str) or not decision['reason'].strip():
            raise DomainError('Placement decision reason required')
        if set(decision['destinations']) != {x['id'] for x in included}:
            raise DomainError('Placement decision must cover all included materials')
        for path in decision['destinations'].values(): relative(path)
        return deepcopy(decision['destinations']), deepcopy(decision)
    return None, None


@dataclass(frozen=True)
class TransferRequest:
    data: dict

    @classmethod
    def parse(cls, raw, policy):
        if not isinstance(raw, dict) or raw.get('action') not in ('export', 'import'):
            raise DomainError('Unknown transfer action')
        if raw['action'] == 'export':
            exact(raw, {'action','request_id','task_ids','sprint_id','handoff'}, 'transfer export')
            ids,sid,checkpoint = raw['task_ids'],raw['sprint_id'],raw['handoff']
            if ids is not None:
                if not isinstance(ids,list) or not ids or len(ids)>policy['max_tasks']:
                    raise DomainError('Explicit nonempty bounded task selection required')
                for tid in ids:path_identifier(tid)
                if len(set(ids))!=len(ids):raise DomainError('Duplicate transfer task ID')
            if sid is not None:path_identifier(sid)
            if ids is not None and sid is not None:
                raise DomainError('Select tasks or one complete sprint, not both')
            if ids is None and sid is None and checkpoint is None:
                raise DomainError('Explicit owner selection or current handoff required')
            if checkpoint is not None:
                exact(checkpoint, {'request_id','reason','result','commit_message','artifact_paths'}, 'transfer handoff')
                path_identifier(checkpoint['request_id'])
                if not isinstance(checkpoint['reason'],str) or not checkpoint['reason'].strip():
                    raise DomainError('Handoff reason required')
                if not isinstance(checkpoint['artifact_paths'],list):raise DomainError('Handoff artifact paths required')
        else:
            exact(raw, {'action','request_id','package_path','package_digest'}, 'transfer import')
            if not isinstance(raw['package_path'],str) or not raw['package_path'].startswith('/'):
                raise DomainError('Explicit absolute package path required')
            if not isinstance(raw['package_digest'],str) or not re.fullmatch('[0-9a-f]{64}',raw['package_digest']):
                raise DomainError('Explicit SHA-256 package digest required')
        path_identifier(raw['request_id'])
        return cls(deepcopy(raw))


def validate_saved_work(records, released_ids):
    """A snapshot may preserve work; it cannot manufacture a release or acceptance."""
    for record in records:
        if record['claimed_by'] is not None:
            raise DomainError(f"Task {record['id']} is still owned: handoff before export")
        if record['status'] in ('active','verified','accepted') and record['id'] not in released_ids:
            raise DomainError(f"Task {record['id']} lacks an explicit released handoff")
        if record['status'] not in ('available','active','verified','accepted','completed','cancelled'):
            raise DomainError('Unsupported saved Task state')
