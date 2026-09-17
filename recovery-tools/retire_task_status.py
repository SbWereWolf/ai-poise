#!/usr/bin/env python3
"""Explicit offline retirement into a NEW SQLite candidate; never edit the source.

Run only in an exclusive stopped-writer operator window. Historical records stay
opaque evidence; they are not executable contracts or current lifecycle aliases.
"""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile

SCHEMA = 'ai-poise-retired-task-status-1'
CURRENT = {'newborn', 'available', 'active', 'verified', 'accepted', 'completed', 'cancelled'}
TASK_COLUMNS = 'id,status,stage_index,iteration,claimed_by,version,current_submission_id,metadata'


def digest(path: Path) -> str:
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def encode(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def regular(path: Path) -> None:
    if any(p.is_symlink() for p in (path, *path.parents)) or not path.is_file():
        raise ValueError(f'Source must be a regular symlink-free file: {path}')


def quiescent(path: Path) -> None:
    for suffix in ('-wal', '-journal'):
        sidecar = Path(str(path) + suffix)
        if sidecar.exists() and sidecar.stat().st_size:
            raise ValueError(f'Active SQLite sidecar; stop all writers first: {sidecar}')


def integrity(db: sqlite3.Connection) -> None:
    if db.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
        raise ValueError('SQLite integrity_check failed')
    if db.execute('PRAGMA foreign_key_check').fetchall():
        raise ValueError('SQLite foreign_key_check failed')


def migrate(source: Path, candidate: Path, plan: dict) -> dict:
    source, candidate = Path(source).absolute(), Path(candidate).absolute()
    regular(source)
    if candidate.exists() or candidate.is_symlink():
        raise ValueError('Candidate already exists; refusing overwrite')
    if any(p.is_symlink() for p in candidate.parents):
        raise ValueError('Candidate parent must not be a symlink')
    if not isinstance(plan, dict) or plan.get('schema') != SCHEMA:
        raise ValueError('Unknown migration plan schema')
    for field in ('request_id', 'reason', 'authorization'):
        if not isinstance(plan.get(field), str) or not plan[field].strip():
            raise ValueError(f'Explicit {field} is required')
    for field in ('task_versions', 'sprint_revisions'):
        value = plan.get(field)
        if not isinstance(value, dict) or any(
            not isinstance(k, str) or not k or type(v) is not int or v < 0
            for k, v in value.items()
        ):
            raise ValueError(f'Explicit observed {field} are required')
    expected = plan.get('source_sha256', '')
    if not isinstance(expected, str) or not re.fullmatch('[0-9a-f]{64}', expected):
        raise ValueError('Invalid source SHA-256')
    quiescent(source)
    if digest(source) != expected:
        raise ValueError('Source SHA-256 changed')
    candidate.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.retire-status-', dir=candidate.parent) as temporary:
        staged = Path(temporary) / 'candidate.sqlite'
        with closing(sqlite3.connect(source.as_uri() + '?mode=ro', uri=True)) as old:
            integrity(old)
            if old.execute('PRAGMA user_version').fetchone()[0] != 13:
                raise ValueError('This explicit migration requires schema version 13')
            with closing(sqlite3.connect(staged)) as new:
                old.backup(new)
        # SQL rollback is confined to a disposable candidate, never the source.
        with closing(sqlite3.connect(staged)) as db:
            db.execute('PRAGMA foreign_keys=ON')
            db.execute('BEGIN IMMEDIATE')
            columns = TASK_COLUMNS.split(',')
            tasks = [dict(zip(columns, row)) for row in db.execute(
                f"SELECT {TASK_COLUMNS} FROM tasks WHERE status='superseded' ORDER BY id")]
            unknown = {row[0] for row in db.execute('SELECT DISTINCT status FROM tasks')} - CURRENT - {'superseded'}
            if unknown:
                raise ValueError(f'Unknown Task statuses require a separate reviewed migration: {unknown}')
            if {r['id']: r['version'] for r in tasks} != plan['task_versions']:
                raise ValueError('Observed Task versions/selection changed')
            if any(r['claimed_by'] is not None for r in tasks):
                raise ValueError('Unresolved Task claim; no ownership inference')
            for task in tasks:
                if db.execute('SELECT 1 FROM sessions WHERE task_id=?', (task['id'],)).fetchone():
                    raise ValueError('Unresolved session claim/binding; release through its owner first')
            sprints = []
            for row in db.execute('SELECT id,project,state,revision,data FROM sprints ORDER BY id'):
                record = dict(zip(('id', 'project', 'state', 'revision', 'data'), row))
                data = json.loads(record['data'])
                if any(item.get('kind') == 'task_replacement' for item in data['aggregate']['decisions']):
                    if data['aggregate']['revision'] != record['revision']:
                        raise ValueError('Sprint revisions disagree with stored aggregate')
                    sprints.append(record)
            if {r['id']: r['revision'] for r in sprints} != plan['sprint_revisions']:
                raise ValueError('Observed Sprint revisions/selection changed')
            stamp = datetime.now(timezone.utc).isoformat()
            audit = {'schema': SCHEMA, 'plan': plan, 'tasks': tasks, 'sprints': sprints,
                     'meaning': 'Retired outcome becomes cancelled, not completed; historical state is retained verbatim.'}
            for task in tasks:
                db.execute("UPDATE tasks SET status='cancelled',version=version+1 WHERE id=? AND version=?",
                           (task['id'], task['version']))
                event = {'event': 'retired_status_migrated', 'prior_status': task['status'],
                         'status': 'cancelled', 'reason': plan['reason'], 'request_id': plan['request_id']}
                db.execute('INSERT INTO task_events(task_id,version,at,data) VALUES(?,?,?,?)',
                           (task['id'], task['version'] + 1, stamp, encode(event)))
            for sprint in sprints:
                data = json.loads(sprint['data'])
                data['aggregate']['decisions'] = [item for item in data['aggregate']['decisions']
                                                  if item.get('kind') != 'task_replacement']
                revision = sprint['revision'] + 1
                data['aggregate']['revision'] = revision
                raw = encode(data)
                db.execute('UPDATE sprints SET revision=?,data=? WHERE id=? AND revision=?',
                           (revision, raw, sprint['id'], sprint['revision']))
                db.execute('INSERT INTO sprint_layers(sprint_id,revision,data,at) VALUES(?,?,?,?)',
                           (sprint['id'], revision, raw, stamp))
            db.execute('INSERT INTO journal(at,session_id,task_id,event,data) VALUES(?,?,?,?,?)',
                       (stamp, 'operator-migration', None, 'migration.retired_task_status', encode(audit)))
            integrity(db)
            db.commit()
        quiescent(source)
        if digest(source) != expected:
            raise ValueError('Source SHA-256 changed during migration; candidate not published')
        os.chmod(staged, source.stat().st_mode & 0o777)
        with staged.open('rb') as stream:
            os.fsync(stream.fileno())
        # O_EXCL-style publication; a racing foreign file is never overwritten.
        os.link(staged, candidate)
        directory = os.open(candidate.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    return {'schema': SCHEMA, 'request_id': plan['request_id'], 'source_sha256': expected,
            'candidate_sha256': digest(candidate), 'candidate': str(candidate),
            'task_ids': [r['id'] for r in tasks], 'sprint_ids': [r['id'] for r in sprints],
            'source_unchanged': True, 'status': 'candidate_created'}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('source', 'candidate', 'plan'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    try:
        result = migrate(args.source, args.candidate, json.loads(args.plan.read_text(encoding='utf-8')))
    except (ValueError, OSError, sqlite3.DatabaseError) as exc:
        print(encode({'error': str(exc), 'status': 'not_published'}))
        return 1
    print(encode(result))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
