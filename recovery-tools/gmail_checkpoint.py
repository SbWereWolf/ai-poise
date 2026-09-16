#!/usr/bin/env python3
"""Prepare, verify and restore full Gmail checkpoints through the connected owner.

This tool does not contain credentials or impersonate Gmail. SEND-REQUEST.json and
SEND-RECEIPT.json are exact requests for the agent's connected Gmail.send_email.
The agent fetches the resulting attachment through Gmail.read_attachment; confirm
checks those bytes and performs a full isolated restore before issuing a receipt.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import tempfile

_SPEC = importlib.util.spec_from_file_location('gmail_cloud_owner', Path(__file__).with_name('cloud_recovery.py'))
cloud = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(cloud)
CheckpointError = cloud.CheckpointError
SCHEMA = 'ai-poise-gmail-checkpoint-1'
CACHE_DIRS = frozenset({'.venv', 'venv', '__pycache__', '.pytest_cache', '.poise-test-cache', 'node_modules'})


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def selected_inventory(project: Path) -> tuple[dict, list[str]]:
    """Only known reproducible caches are omitted; tracked files always survive."""
    tracked = {os.fsdecode(p) for p in cloud.git(project, 'ls-files', '-z').split(b'\0') if p}
    files, omitted = {}, []
    for base, dirs, names in os.walk(project, topdown=True, followlinks=False):
        directory = Path(base)
        for name in list(dirs):
            path = directory / name
            rel = path.relative_to(project).as_posix()
            is_cache = any(part in CACHE_DIRS for part in Path(rel).parts)
            if is_cache and not any(p.startswith(rel + '/') for p in tracked):
                omitted.append(rel + '/'); dirs.remove(name)
            elif path.is_symlink():
                raise CheckpointError(f'Unsupported directory link: {rel}')
        for name in names:
            path = directory / name
            rel = path.relative_to(project).as_posix()
            if rel == cloud.MANIFEST:
                continue
            disposable = any(part in CACHE_DIRS for part in Path(rel).parts) or name.endswith(('.pyc', '.pyo'))
            if disposable and rel not in tracked:
                omitted.append(rel); continue
            if path.is_symlink() or not path.is_file():
                raise CheckpointError(f'Unsupported file or link: {rel}')
            if cloud.sqlite_file(path):
                cloud.check_database(path)
            files[rel] = {'sha256': cloud.digest(path), 'mode': path.stat().st_mode & 0o777,
                          'bytes': path.stat().st_size}
    return files, sorted(omitted)


def prepare(project: Path, output: Path, checkpoint_id: str, recipient: str,
            next_action: str, journal: str = 'WORKLOG.md', compression: str = 'xz') -> dict:
    """Produce a full stopped-source snapshot and a connector send request."""
    project, output = project.resolve(), output.absolute()
    cloud.require_new(output)
    if output.resolve().is_relative_to(project):
        raise CheckpointError('Checkpoint output must be outside the project')
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,99}', checkpoint_id):
        raise CheckpointError('Use an explicit simple unique checkpoint ID')
    if not re.fullmatch(r'[^\s,<>@]+@[^\s,<>@]+', recipient):
        raise CheckpointError('One explicit recipient is required')
    if not next_action.strip():
        raise CheckpointError('An explicit next action is required')
    journal_path = project / journal
    if not journal_path.resolve().is_relative_to(project) or not journal_path.is_file() or journal_path.is_symlink():
        raise CheckpointError('Journal must be a regular file inside the project')
    journal_text = journal_path.read_text(encoding='utf-8')
    if not journal_text.strip():
        raise CheckpointError('Journal must describe actual progress')
    before_git = cloud.git_state(project)
    before, omitted = selected_inventory(project)
    if journal_path.relative_to(project).as_posix() not in before:
        raise CheckpointError('Journal cannot be excluded as a cache')
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.gmail-prepare-', dir=output.parent) as directory:
        stage = Path(directory)
        snapshot = stage / 'ai-poise'; snapshot.mkdir()
        for rel in sorted(before):
            target = snapshot / rel; target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(project / rel, target)
        after, _ = selected_inventory(project)
        if before != after or before_git != cloud.git_state(project) or cloud.inventory(snapshot) != before:
            raise CheckpointError('Source changed during snapshot; no checkpoint was promoted')
        packed = stage / 'mail-package'
        record = cloud.pack(snapshot, packed, compression)
        archive_name = Path(record['archive']).name
        record['archive'] = str(output / archive_name)
        cloud.json_write(packed / 'CHECKPOINT.json', record)
        meta = {'schema': SCHEMA, 'checkpoint_id': checkpoint_id, 'recipient': recipient,
                'prepared_at_utc': now_utc().isoformat(), 'head': record['head'],
                'branch': before_git['branch'], 'working_tree_status': before_git['status'],
                'archive': archive_name, 'archive_sha256': record['archive_sha256'],
                'transport': 'checkpoint.recovery.txt', 'transport_sha256': record['transport_sha256'],
                'transport_bytes': (packed / 'checkpoint.recovery.txt').stat().st_size,
                'journal': journal, 'next_action': next_action, 'excluded_cache_paths': omitted,
                'delivery_verified': False}
        # Bootstrap sources travel as openly labelled plain source text, not binaries.
        toolkit = {'gmail_checkpoint.py.txt': Path(__file__),
                   'cloud_recovery.py.txt': Path(__file__).with_name('cloud_recovery.py'),
                   'work_checkpoint.py.txt': cloud._ROOT / 'tools/work_checkpoint.py'}
        meta['toolkit_sources'] = {}
        for name, source in toolkit.items():
            shutil.copyfile(source, packed / name)
            meta['toolkit_sources'][name] = cloud.digest(packed / name)
        cloud.json_write(packed / 'GMAIL-CHECKPOINT.json', meta)
        shutil.copyfile(snapshot / journal, packed / 'WORKLOG.md')
        journal_text = (snapshot / journal).read_text(encoding='utf-8')
        body = (f'AI-POISE-BACKUP-{checkpoint_id}\nFull project checkpoint, not a delta.\n'
                f'HEAD: {meta["head"]}\nArchive SHA-256: {meta["archive_sha256"]}\n'
                f'Transport SHA-256: {meta["transport_sha256"]}\nNext: {next_action}\n\n'
                'The text/plain attachment is the explicitly labelled Base64 transport of the full archive.\n'
                'Preparation is not proof of Gmail storage. Fetch the full attachment and run confirm.\n'
                'Recovery after local loss: read this message and fetch checkpoint.recovery.txt plus\n'
                'GMAIL-CHECKPOINT.json. Use gmail_checkpoint.py restore --metadata ... --readback ...\n'
                '--destination NEW_DIRECTORY, or cloud_recovery.py restore with the archive SHA above.\n'
                'No local original, prior delta or Task harness is required for restoration.\n\n'
                'WORKLOG (stored also in the full archive):\n' + journal_text)
        (packed / 'MAIL.txt').write_text(body, encoding='utf-8')
        cloud.json_write(packed / 'SEND-REQUEST.json', {
            'to': recipient, 'subject': f'AI-POISE-BACKUP-{checkpoint_id} — {record["head"][:7]}',
            'body_file': str(output / 'MAIL.txt'), 'content_type': 'text/plain',
            'attachment_files': [str(output / n) for n in ('checkpoint.recovery.txt', 'GMAIL-CHECKPOINT.json', 'WORKLOG.md', *toolkit)]})
        cloud.require_new(output); packed.rename(output)
    return meta


def checked_metadata(meta: dict) -> None:
    if not isinstance(meta, dict) or meta.get('schema') != SCHEMA:
        raise CheckpointError('Unknown Gmail checkpoint metadata schema')
    for key in ('archive_sha256', 'transport_sha256'):
        if not re.fullmatch(r'[0-9a-f]{64}', str(meta.get(key, ''))):
            raise CheckpointError(f'Invalid {key}')
    if not re.fullmatch(r'[0-9a-f]{40,64}', str(meta.get('head', ''))):
        raise CheckpointError('Invalid expected HEAD')


def restore_from_gmail(meta: dict, readback: Path, destination: Path) -> dict:
    """Check complete downloaded bytes; never overwrite an existing destination."""
    checked_metadata(meta)
    destination = destination.absolute(); cloud.require_new(destination)
    if readback.is_symlink() or not readback.is_file():
        raise CheckpointError('Readback must be a complete regular downloaded file')
    if cloud.digest(readback) != meta['transport_sha256']:
        raise CheckpointError('Downloaded transport SHA-256 mismatch')
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.gmail-restore-', dir=destination.parent) as directory:
        restored = Path(directory) / 'restored'
        result = cloud.restore(readback, restored, meta['archive_sha256'])
        if result['head'] != meta['head']:
            raise CheckpointError('Restored HEAD differs from Gmail metadata')
        cloud.require_new(destination); restored.rename(destination)
    return {**result, 'project': str(destination / 'ai-poise'), 'checkpoint_id': meta['checkpoint_id']}


def confirm(output: Path, readback: Path, message_id: str, destination: Path) -> dict:
    """Caller supplies actual Gmail readback; local packing cannot set success."""
    output = output.resolve()
    meta = json.loads((output / 'GMAIL-CHECKPOINT.json').read_text(encoding='utf-8'))
    outgoing = output / 'checkpoint.recovery.txt'
    if readback.resolve() == outgoing.resolve() or os.path.samefile(readback, outgoing):
        raise CheckpointError('Gmail readback must be a separate downloaded file, not the outgoing payload')
    if not re.fullmatch(r'[0-9a-f]+', message_id):
        raise CheckpointError('Supply the exact Gmail message ID returned by the connector')
    receipt_path = output / 'GMAIL-VERIFIED.json'; cloud.require_new(receipt_path)
    result = restore_from_gmail(meta, readback, destination)
    receipt = {'schema': SCHEMA, 'checkpoint_id': meta['checkpoint_id'], 'message_id': message_id,
               'head': result['head'], 'archive_sha256': meta['archive_sha256'],
               'transport_sha256': cloud.digest(readback), 'prepared_at_utc': meta['prepared_at_utc'],
               'verified_at_utc': now_utc().isoformat(),
               'readback_verified': True, 'restoration': result, 'receipt_stored_in_gmail': False,
               'provenance': 'Gmail message and downloaded-file origin supplied by caller; bytes and restore verified here'}
    cloud.json_write(receipt_path, receipt)
    cloud.json_write(output / 'SEND-RECEIPT.json', {
        'to': meta['recipient'], 'subject': f'AI-POISE-BACKUP-{meta["checkpoint_id"]} — READBACK VERIFIED',
        'body': 'Full Gmail checkpoint restoration succeeded.\n' + json.dumps(receipt, ensure_ascii=False, indent=2),
        'content_type': 'text/plain', 'attachment_files': [str(receipt_path)]})
    return receipt


def backup_due(receipt: dict | None, now: datetime | None = None, max_age_seconds: int = 600) -> dict:
    """A polling check during active work, not a background scheduler."""
    if not 0 < max_age_seconds <= 600:
        raise CheckpointError('Active-work interval must be in 1..600 seconds')
    now = now or now_utc()
    if not receipt or receipt.get('readback_verified') is not True or not receipt.get('message_id'):
        return {'due': True, 'reason': 'No verified Gmail readback', 'remaining_seconds': 0}
    try:
        stamp = datetime.fromisoformat(receipt['prepared_at_utc'])
        elapsed = (now - stamp).total_seconds()
    except (ValueError, TypeError, KeyError):
        return {'due': True, 'reason': 'Invalid verification clock', 'remaining_seconds': 0}
    return {'due': elapsed < 0 or elapsed >= max_age_seconds,
            'reason': 'Snapshot age (a significant stage always requires a new checkpoint)',
            'elapsed_seconds': elapsed, 'remaining_seconds': max(0, max_age_seconds - elapsed)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    p = commands.add_parser('prepare')
    for name in ('project', 'output'):
        p.add_argument('--' + name, required=True, type=Path)
    for name in ('checkpoint-id', 'recipient', 'next-action'):
        p.add_argument('--' + name, required=True)
    p.add_argument('--journal', default='WORKLOG.md')
    p.add_argument('--compression', choices=('xz', 'zstd', 'compare'), default='xz')
    p = commands.add_parser('confirm')
    for name in ('output', 'readback', 'destination'):
        p.add_argument('--' + name, required=True, type=Path)
    p.add_argument('--message-id', required=True)
    p = commands.add_parser('restore')
    for name in ('metadata', 'readback', 'destination'):
        p.add_argument('--' + name, required=True, type=Path)
    p = commands.add_parser('due'); p.add_argument('--receipt', type=Path)
    args = parser.parse_args()
    try:
        values = vars(args); command = values.pop('command')
        if command == 'prepare':
            result = prepare(**values)
        elif command == 'confirm':
            result = confirm(**values)
        elif command == 'restore':
            meta = json.loads(args.metadata.read_text(encoding='utf-8'))
            result = restore_from_gmail(meta, args.readback, args.destination)
        else:
            receipt = json.loads(args.receipt.read_text(encoding='utf-8')) if args.receipt and args.receipt.exists() else None
            result = backup_due(receipt)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (CheckpointError, OSError, ValueError, KeyError) as exc:
        print(json.dumps({'error': str(exc), 'delivery_verified': False}, ensure_ascii=False))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
