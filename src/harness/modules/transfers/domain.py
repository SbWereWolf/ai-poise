"""Portable saved-work contracts. No storage, process execution or implicit policy."""
from copy import deepcopy
from dataclasses import dataclass
import re
from ..foundation.errors import DomainError
from ..artifact_factory.domain import exact, relative
from ..tasks.definition import path_identifier

TRANSFER_FORMAT = 'harness-transfer-1'


@dataclass(frozen=True)
class TransferPolicy:
    data: dict

    @classmethod
    def parse(cls, raw):
        paths = {'directory', 'archive', 'manifest', 'database', 'files_directory', 'bundles_directory'}
        limits = {'max_tasks', 'max_files', 'max_file_bytes', 'max_total_bytes', 'chunk_bytes'}
        exact(raw, paths | limits | {'file_mode'}, 'transfer config')
        for key in paths:
            relative(raw[key])
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
