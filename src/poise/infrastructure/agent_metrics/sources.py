"""Bounded external source capture; raw records live only in worker memory."""
from dataclasses import dataclass
import hashlib
from pathlib import Path

from ...modules.accounting.metrics.domain import identity, instant
from ...modules.foundation.errors import PoiseError
from ..goal_config import strict_json


def file_identity(stat):
    return {'device': stat.st_dev, 'inode': stat.st_ino, 'mtime_ns': stat.st_mtime_ns}


def capture_source(path, source):
    """Freeze a descriptor, never copy a transcript into the delivery spool."""
    selected = Path(path)
    stat = selected.stat()
    if not selected.is_file():
        raise PoiseError('Metric source is not a regular file: ' + str(selected))
    return {'path': str(selected), 'source': dict(source), 'boundary': stat.st_size,
            'source_identity': file_identity(stat)}


@dataclass(frozen=True)
class SourceRecord:
    offset: int
    event_hash: str
    event: dict
    size: int


@dataclass(frozen=True)
class SourceBatch:
    descriptor: dict
    records: tuple
    sha256: str
    complete_offset: int
    errors: tuple
    later_bytes: int
    observed_identity: dict

    @property
    def source_key(self):
        return identity([self.descriptor['source']['id'], self.descriptor['path']])

    @property
    def coverage(self):
        boundary = self.descriptor['boundary']
        return {'boundary': boundary, 'complete_offset': self.complete_offset,
                'partial_tail': self.complete_offset < boundary, 'later_bytes': self.later_bytes,
                'errors': list(self.errors), 'captured_source_identity': self.descriptor['source_identity'],
                'observed_source_identity': self.observed_identity,
                'source_changed_since_capture': self.descriptor['source_identity'] != self.observed_identity,
                **{name: self.descriptor[name] for name in ('selection_key', 'selection_generation')
                   if name in self.descriptor}}


def scan_jsonl(descriptor, *, max_source_bytes, max_records):
    """Read every admitted chunk to the saved boundary, excluding an unfinished tail."""
    path = Path(descriptor['path'])
    boundary = descriptor['boundary']
    if type(boundary) is not int or boundary < 0 or boundary > max_source_bytes:
        raise PoiseError('Metric source exceeds the configured capture bound; select a bounded source')
    if type(max_records) is not int or max_records <= 0:
        raise PoiseError('Metric source chunk size must be a positive integer')
    before = path.stat()
    if before.st_size < boundary or (before.st_dev, before.st_ino) != (
            descriptor['source_identity']['device'], descriptor['source_identity']['inode']):
        raise PoiseError('Captured metric source was replaced or truncated; capture its current identity')
    digest = hashlib.sha256()
    complete, records, errors = 0, [], []
    with path.open('rb') as stream:
        while stream.tell() < boundary:
            chunk = []
            for _ in range(max_records):
                offset = stream.tell()
                if offset >= boundary:
                    break
                raw = stream.readline(boundary - offset)
                digest.update(raw)
                if not raw:
                    raise PoiseError('Metric source truncated during capture; capture its current identity')
                if not raw.endswith(b'\n'):
                    break
                complete = stream.tell()
                try:
                    event = strict_json(raw.decode('utf-8'))
                    if not isinstance(event, dict):
                        raise PoiseError('Metric source record must be a JSON object')
                except (PoiseError, UnicodeError) as exc:
                    errors.append({'kind': 'malformed_record', 'source_offset': offset, 'reason': str(exc)})
                    continue
                if event.get('timestamp') is not None:
                    try:
                        instant(event['timestamp'])
                    except PoiseError as exc:
                        errors.append({'kind': 'invalid_source_time', 'source_offset': offset, 'reason': str(exc)})
                        event = {**event, 'timestamp': None}
                payload = raw.rstrip(b'\r\n')
                chunk.append(SourceRecord(offset, hashlib.sha256(payload).hexdigest(), event, len(payload)))
            records.extend(chunk)
    after = path.stat()
    # An ordinary append is allowed only when the exact admitted prefix survived.
    if file_identity(after) != file_identity(before) or after.st_size != before.st_size:
        check = hashlib.sha256()
        with path.open('rb') as stream:
            remaining = boundary
            while remaining:
                data = stream.read(min(remaining, 1024 * 1024))
                if not data:
                    raise PoiseError('Metric source disappeared during capture; capture it again')
                remaining -= len(data)
                check.update(data)
        if check.hexdigest() != digest.hexdigest():
            raise PoiseError('Captured metric source bytes changed; capture their current identity')
    return SourceBatch(descriptor, tuple(records), digest.hexdigest(), complete,
                       tuple(errors), max(0, after.st_size - boundary), file_identity(after))


def prefix_digest(path, boundary):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        remaining = boundary
        while remaining:
            chunk = stream.read(min(remaining, 1024 * 1024))
            if not chunk:
                return None
            digest.update(chunk)
            remaining -= len(chunk)
    return digest.hexdigest()


def owned_records(batch):
    """Retain the actual session owner and exclude inherited/foreign fork events."""
    metas = [record for record in batch.records if record.event.get('type') == 'session_meta'
             and isinstance(record.event.get('payload'), dict)]
    if not metas:
        raise PoiseError('Transcript has no session metadata; select the complete source journal')
    meta = metas[0].event['payload']
    session = meta.get('id')
    if not isinstance(session, str) or not session:
        raise PoiseError('Transcript session identity is unavailable')
    start = metas[0].offset
    if not meta.get('forked_from_id') and any(
            record.event['payload'].get('id') != session
            for record in metas):
        raise PoiseError('Transcript has several owners without an explicit fork identity')
    fork = instant(meta['timestamp']) if meta.get('forked_from_id') and meta.get('timestamp') else None
    inherited, selected, excluded = False, [], []
    for record in batch.records:
        event, payload = record.event, record.event.get('payload')
        if not isinstance(payload, dict):
            excluded.append(record.offset)
            continue
        if fork and payload.get('type') == 'task_started' and type(payload.get('started_at')) is int:
            inherited = payload['started_at'] < int(fork.timestamp())
        stamp = instant(event['timestamp']) if event.get('timestamp') else None
        if record.offset < start or payload.get('thread_id', session) != session or inherited or (
                fork is not None and stamp is not None and stamp < fork):
            excluded.append(record.offset)
        else:
            selected.append(record)
    return session, meta, tuple(selected), tuple(excluded)


def source_selections(sources, max_files, selected_ids=None):
    """Select physical journals once, preserving every configured source owner."""
    selected_ids = None if selected_ids is None else set(selected_ids)
    dedicated = {Path(address) for source in sources
                 if source['kind'] not in ('codex-native', 'codex-transcript')
                 for address in source['paths']
                 if source['kind'] == 'hook-adoption' or Path(address).is_file()}
    primary = [Path(address) for source in sources if source['kind'] == 'codex-transcript'
               for address in source['paths']]
    used, count, result = set(), 0, []
    for source in sources:
        if selected_ids is not None and source['id'] not in selected_ids:
            continue
        if source['kind'] not in ('codex-native', 'codex-transcript', 'hook-adoption'):
            continue
        for address in source['paths']:
            root, errors, excluded, eligible = Path(address), [], False, []
            delegated_root = (source['kind'] in ('codex-native', 'codex-transcript') and
                              any(root == owner or owner in root.parents for owner in dedicated)
                              or source['kind'] == 'codex-native' and
                              any(root == owner or owner in root.parents for owner in primary))
            try:
                if delegated_root:
                    candidates, excluded = [], True
                elif not root.exists():
                    errors.append({'kind': 'source_unavailable', 'source': address,
                                   'reason': 'Configured source path is unavailable'})
                    candidates = []
                else:
                    candidates = sorted(root.rglob('*.jsonl')) if root.is_dir() else [root]
                for path in candidates:
                    delegated = (source['kind'] in ('codex-native', 'codex-transcript') and
                                 any(path == owner or owner in path.parents for owner in dedicated)
                        or source['kind'] == 'codex-native' and any(path == parent or parent in path.parents
                                                                   for parent in primary)
                        or (source['id'], path) in used)
                    if delegated:
                        excluded = True
                        continue
                    used.add((source['id'], path))
                    eligible.append(path)
            except OSError as exc:
                errors.append({'kind': 'source_unavailable', 'source': address, 'reason': str(exc)})
            if not eligible and not errors and not excluded:
                errors.append({'kind': 'source_selection_empty', 'source': address,
                               'reason': 'No eligible source journals in the configured selection'})
            remaining = max(0, max_files - count)
            if len(eligible) > remaining:
                errors.append({'kind': 'source_selection_bound', 'source': address,
                               'reason': 'Configured collection file bound reached'})
                eligible = eligible[:remaining]
            count += len(eligible)
            result.append({'source': source, 'address': address, 'files': eligible, 'errors': errors,
                           'selection_key': identity([source['id'], 'selection', address]),
                           'owned_elsewhere': excluded and not eligible and not errors,
                           'delegated_root': bool(delegated_root)})
    return result
