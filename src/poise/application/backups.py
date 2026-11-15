from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone

from ..modules.backups.domain import (
    RETENTION_SECONDS, RETENTION_SCHEMA, admit_decision,
    exact_backup_name, generated_backup_name, retention_input_digest, validate_grant,
)
from ..modules.foundation.errors import PoiseError
from ..modules.backups.ports import TaskDatabaseBackups


def aware_instant(value) -> datetime:
    try:
        instant = datetime.fromisoformat(value.replace('Z', '+00:00')) if isinstance(value, str) else value
        if not isinstance(instant, datetime) or instant.utcoffset() is None:
            raise ValueError('timezone required')
        return instant.astimezone(timezone.utc)
    except (ValueError, TypeError, OverflowError) as exc:
        raise PoiseError('Retention deadline/clock must be a finite timezone-aware instant') from exc


def backup_created_at(name: str, seed: str) -> datetime | None:
    before, dot, suffix = seed.rpartition('.')
    stem, extension = (before, '.' + suffix) if dot else (seed, '')
    prefix = stem + '-backup-'
    if not name.startswith(prefix) or (extension and not name.endswith(extension)):
        return None
    stamp = name[len(prefix):len(name)-len(extension) if extension else None]
    if len(stamp) != 22:
        return None
    try:
        created = datetime.strptime(stamp, '%Y%m%dT%H%M%S%fZ').replace(tzinfo=timezone.utc)
        return created if generated_backup_name(seed, created) == name else None
    except ValueError:
        return None


class BackupCommands:
    def __init__(self, backups: TaskDatabaseBackups, clock: Callable[[], datetime], *, authority=None):
        self.backups = backups
        self.clock = clock
        self.authority = authority

    def list(self) -> list[dict]:
        return [
            {"name": item.name, "created_at": item.created_at}
            for item in self.backups.list_entries()
        ]

    def create(self) -> dict:
        name = generated_backup_name(self.backups.filename_source, self.clock())
        copy = self.backups.create_copy(name)
        return {
            "success": True,
            "name": copy.name,
            "live_size_bytes": copy.live_size_bytes,
            "backup_size_bytes": copy.backup_size_bytes,
        }

    def restore(self, name: str) -> dict:
        selected = exact_backup_name(name)
        self.backups.restore_copy(selected)
        return {"success": True, "name": selected}

    def _grant(self, name):
        grant, pending = self.backups.grant_state(name)
        if pending:
            raise PoiseError('Pending retention decision; resume the same backup retain request '
                             'or reconcile its exact source before removal')
        if grant is None:
            return None, None
        until = validate_grant(grant, project=self.backups.project_identity, name=name, instant=aware_instant)
        if self.backups.archive_digest(name) != grant['sha256']:
            raise PoiseError('Archive digest differs from the admitted retention decision; '
                             'reconcile this exact archive and recorded source')
        return grant, until

    def retain(self, name, *, valid_until, expected_digest, authorization_ref, request_id,
               authority=None):
        name = exact_backup_name(name)
        until = aware_instant(valid_until)
        if not isinstance(request_id, str) or not request_id.strip():
            raise PoiseError('Retention requires a stable nonempty request identity')
        resolver = authority if authority is not None else self.authority
        if not callable(resolver):
            raise PoiseError('Recorded authorization source owner is required')
        with self.backups.locked():
            if until <= aware_instant(self.clock()):
                raise PoiseError('Retention decision deadline must be later than the observed clock')
            if not self.backups.archive_exists(name):
                raise PoiseError(f'Backup is absent: {name}; no effective grant can be created')
            digest = self.backups.archive_digest(name)
            if expected_digest != digest:
                raise PoiseError('Expected backup digest does not match immutable source bytes')
            decision = admit_decision(resolver(authorization_ref), reference=authorization_ref,
                                      project=self.backups.project_identity, name=name,
                                      digest=digest, deadline=until, instant=aware_instant)
            grant = {'schema': RETENTION_SCHEMA, 'project': self.backups.project_identity,
                     'backup_name': name, 'sha256': digest, 'valid_until': until.isoformat(),
                     'authorization': decision, 'actor': self.backups.operator_identity(),
                     'request_id': request_id}
            grant['input_digest'] = retention_input_digest(grant)
            self.backups.publish_grant(name, grant)
        return {'success': True, 'name': name, 'valid_until': until.isoformat(),
                'authorization_ref': authorization_ref}

    def remove(self, name):
        name = exact_backup_name(name)
        with self.backups.locked():
            now = aware_instant(self.clock())
            if not self.backups.archive_exists(name):
                grant, pending = self.backups.grant_state(name)
                if pending:
                    raise PoiseError('Pending decision; reconcile exact archive/source before metadata removal')
                if grant is not None:
                    validate_grant(grant, project=self.backups.project_identity, name=name, instant=aware_instant)
                    self.backups.delete_grant(name)
                return {'success': True, 'name': name, 'removed': False}
            grant, until = self._grant(name)
            if grant is not None and now < until:
                raise PoiseError(f'Backup is protected by a retention decision until {until.isoformat()}; '
                                 'repeat exact removal after that deadline or resolve with its source owner')
            removed = self.backups.delete_archive(name)
            if grant is not None:
                self.backups.delete_grant(name)
            return {'success': True, 'name': name, 'removed': removed}

    def prune(self):
        result = {'success': True, 'removed': [], 'retained': [], 'rejected': []}

        def reject(name, exc):
            result['success'] = False
            result['rejected'].append({'name': name, 'reason': str(exc),
                'archive_present': self.backups.archive_exists(name),
                'continuation': 'Reconcile this exact backup/source with its owner, then retry backup prune.'})

        with self.backups.locked():
            now = aware_instant(self.clock())
            for entry in self.backups.list_entries():
                name = entry.name
                try:
                    created = backup_created_at(name, self.backups.filename_source)
                    if created is None:
                        raise PoiseError('Unknown legacy backup identity; use addressed owner removal')
                    grant, until = self._grant(name)
                    if grant is not None and now < until:
                        result['retained'].append({'name': name, 'reason': 'retained_by_user_decision',
                                                  'valid_until': until.isoformat(),
                                                  'authorization_ref': grant['authorization']['reference']})
                    elif (now - created).total_seconds() < RETENTION_SECONDS:
                        result['retained'].append({'name': name, 'reason': 'not_expired'})
                    else:
                        removed = self.backups.delete_archive(name)
                        result['removed'].append({'name': name, 'removed': removed})
                        if grant is not None:
                            self.backups.delete_grant(name)
                except (PoiseError, OSError) as exc:
                    reject(name, exc)
            # Resume archive-removed/metadata-left partial outcomes, not missing backups.
            failed_names = {item['name'] for item in result['rejected']}
            for name in self.backups.grant_names():
                if name in failed_names or self.backups.archive_exists(name):
                    continue
                try:
                    grant, pending = self.backups.grant_state(name)
                    if pending:
                        raise PoiseError('Orphan pending grant needs exact source reconciliation')
                    validate_grant(grant, project=self.backups.project_identity, name=name, instant=aware_instant)
                    self.backups.delete_grant(name)
                    result['removed'].append({'name': name, 'removed': False, 'metadata_removed': True})
                except (PoiseError, OSError) as exc:
                    reject(name, exc)
        return result
