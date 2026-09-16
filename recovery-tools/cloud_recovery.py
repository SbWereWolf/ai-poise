#!/usr/bin/env python3
"""Cloud-agent project recovery and isolated reconciliation.

No Gmail credentials, native-event impersonation, Task lifecycle automation or
live-directory replacement. Legacy transports and Git commands use the existing
work_checkpoint owner. Run only against quiescent, explicitly selected inputs.
"""
from __future__ import annotations

import argparse
from contextlib import closing, contextmanager
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sqlite3
import subprocess
import tarfile
import tempfile
import time
import uuid

_ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location('poise_checkpoint_owner', _ROOT / 'tools/work_checkpoint.py')
_core = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_core)
CheckpointError = _core.CheckpointError
digest = _core.digest
archive_bytes = _core.archive_bytes
git = _core.git
json_write = _core.json_write
require_new = _core.require_new

SCHEMA = 'ai-poise-cloud-project-1'
MANIFEST = 'RECOVERY-MANIFEST.json'
MAX_UNPACKED = 8 * 1024 ** 3
IDENTITY = ('-c', 'user.name=Cloud recovery', '-c', 'user.email=cloud-recovery@example.invalid')


def regular_files(root: Path) -> list[Path]:
    files = []
    for path in sorted(root.rglob('*')):
        if path.is_symlink() or not (path.is_dir() or path.is_file()):
            raise CheckpointError(f'Unsupported link or special file: {path}')
        if path.is_file():
            files.append(path)
    return files


def sqlite_file(path: Path) -> bool:
    with path.open('rb') as stream:
        return stream.read(16) == b'SQLite format 3\x00'


def check_database(path: Path) -> dict:
    _core.check_database(path)
    with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro&immutable=1', uri=True)) as db:
        foreign = db.execute('PRAGMA foreign_key_check').fetchall()
        if foreign:
            raise CheckpointError(f'Foreign key violations in {path}: {foreign[:5]}')
        return {'integrity_check': 'ok', 'foreign_key_check': 'ok',
                'user_version': db.execute('PRAGMA user_version').fetchone()[0]}


def git_state(project: Path) -> dict:
    if not (project / '.git').is_dir() or (project / '.git').is_symlink():
        raise CheckpointError('A self-contained repository with an ordinary .git directory is required')
    if (project / '.git/objects/info/alternates').exists():
        raise CheckpointError('External Git object alternates are not self-contained')
    worktrees = project / '.git/worktrees'
    if worktrees.exists() and any(worktrees.iterdir()):
        raise CheckpointError('Snapshot linked worktrees separately; preserve their WIP before preparing a self-contained copy')
    if list((project / '.git').rglob('*.lock')):
        raise CheckpointError('Git lock present; quiesce the source, do not delete a live lock')
    if Path(git(project, 'rev-parse', '--show-toplevel').decode().strip()).resolve() != project.resolve():
        raise CheckpointError('Git core.worktree points outside the selected project')
    git(project, 'fsck', '--strict', '--no-dangling')
    head = git(project, 'rev-parse', 'HEAD').decode().strip()
    refs = dict(line.split(' ', 1)[::-1] for line in git(
        project, 'for-each-ref', '--format=%(objectname) %(refname)').decode().splitlines())
    try:
        branch = git(project, 'symbolic-ref', '--short', 'HEAD').decode().strip()
    except CheckpointError:
        branch = None
    status = git(project, 'status', '--porcelain=v1', '--untracked-files=all', '--', '.', f':(exclude){MANIFEST}').decode()
    return {'head': head, 'branch': branch, 'refs': refs, 'status': status}


def inventory(project: Path) -> dict:
    return {str(p.relative_to(project)): {'sha256': digest(p), 'mode': p.stat().st_mode & 0o777,
            'bytes': p.stat().st_size} for p in regular_files(project) if p.name != MANIFEST or p.parent != project}


def project_manifest(project: Path) -> dict:
    state = git_state(project)
    files = inventory(project)
    databases = {name: check_database(project / name) for name in files if sqlite_file(project / name)}
    return {'schema': SCHEMA, 'git': state, 'files': files, 'databases': databases,
            'delivery_verified': False, 'scope': 'All regular files in the explicitly prepared directory; no implicit cleanup.'}


def verify_project(project: Path) -> dict:
    manifest = json.loads((project / MANIFEST).read_text(encoding='utf-8'))
    if not isinstance(manifest, dict) or manifest.get('schema') != SCHEMA:
        raise CheckpointError('Unknown project manifest schema')
    if inventory(project) != manifest['files']:
        raise CheckpointError('File membership, mode, length or checksum differs from manifest')
    if git_state(project) != manifest['git']:
        raise CheckpointError('Git HEAD, branch, refs or working state differs from manifest')
    for name, expected in manifest['databases'].items():
        if name not in manifest['files'] or not sqlite_file(project / name):
            raise CheckpointError('Database entry is not an inventoried SQLite file')
        if check_database(project / name) != expected:
            raise CheckpointError(f'Database metadata differs: {name}')
    return {'status': 'verified', 'head': manifest['git']['head'],
            'files': len(manifest['files']), 'databases': len(manifest['databases']), 'delivery_verified': False}


def choose_compression(results: dict) -> str:
    smallest = min(results, key=lambda key: results[key]['bytes'])
    fastest = min(results, key=lambda key: results[key]['seconds'])
    slow, fast = results[smallest]['seconds'], results[fastest]['seconds']
    if slow - fast > 600 or (fast > 0 and slow >= fast * 10):
        return fastest
    return smallest


def _text_transport(archive: Path, output: Path) -> None:
    import base64
    with output.open('w', encoding='ascii', newline='\n') as target, archive.open('rb') as source:
        target.write(f'{_core.TEXT_HEADER}\nsha256:{digest(archive)}\n\n')
        while block := source.read(57 * 4096):
            target.write(base64.encodebytes(block).decode('ascii'))


def pack(project: Path, output: Path, compression: str = 'compare') -> dict:
    project, output = project.resolve(), output.absolute()
    require_new(output)
    if output.is_relative_to(project):
        raise CheckpointError('Checkpoint output must be outside its source project')
    if compression not in ('xz', 'zstd', 'compare'):
        raise CheckpointError('Choose xz, zstd or compare')
    manifest = project_manifest(project)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.cloud-pack-', dir=output.parent) as temp:
        stage = Path(temp)
        tarpath = stage / 'project.tar'
        with tarfile.open(tarpath, 'w', format=tarfile.PAX_FORMAT) as archive:
            for path in [project, *sorted(project.rglob('*'))]:
                if path == project / MANIFEST:
                    continue
                archive.add(path, arcname=str(Path('ai-poise') / path.relative_to(project)), recursive=False)
            raw = (json.dumps(manifest, ensure_ascii=False, indent=2) + '\n').encode()
            member = tarfile.TarInfo('ai-poise/' + MANIFEST)
            member.size = len(raw); member.mode = 0o644
            archive.addfile(member, io.BytesIO(raw))
        if inventory(project) != manifest['files'] or git_state(project) != manifest['git']:
            raise CheckpointError('Input changed while creating checkpoint')
        modes = ('xz', 'zstd') if compression == 'compare' else (compression,)
        timings = {}
        for mode in modes:
            command = ['xz', '-9e', '-T1', '-c', str(tarpath)] if mode == 'xz' else ['zstd', '-q', '--ultra', '-22', '-T1', '-c', str(tarpath)]
            compressed = stage / ('checkpoint.tar.xz' if mode == 'xz' else 'checkpoint.tar.zst')
            started = time.monotonic()
            with compressed.open('wb') as stream:
                result = subprocess.run(command, stdout=stream, stderr=subprocess.PIPE)
            if result.returncode:
                raise CheckpointError(result.stderr.decode(errors='replace'))
            timings[mode] = {'bytes': compressed.stat().st_size, 'seconds': time.monotonic() - started, 'argv': command[:-1]}
        chosen = choose_compression(timings)
        archive = stage / ('checkpoint.tar.xz' if chosen == 'xz' else 'checkpoint.tar.zst')
        delivery = stage / 'delivery'; delivery.mkdir()
        shutil.move(archive, delivery / archive.name)
        archive = delivery / archive.name
        _text_transport(archive, delivery / 'checkpoint.recovery.txt')
        record = {'schema': SCHEMA, 'archive': str(output / archive.name),
                  'archive_sha256': digest(archive), 'transport_sha256': digest(delivery / 'checkpoint.recovery.txt'),
                  'head': manifest['git']['head'], 'compression': chosen, 'comparison': timings,
                  'delivery_verified': False}
        json_write(delivery / 'CHECKPOINT.json', record)
        (delivery / (archive.name + '.sha256')).write_text(f'{record["archive_sha256"]}  {archive.name}\n', encoding='ascii')
        require_new(output); delivery.rename(output)
    return record


@contextmanager
def tar_stream(path: Path):
    with path.open('rb') as stream:
        signature = stream.read(4)
    if signature == b'\x28\xb5\x2f\xfd':
        with tempfile.TemporaryFile() as error:
            process = subprocess.Popen(['zstd', '-q', '-d', '-c', str(path)], stdout=subprocess.PIPE, stderr=error)
            try:
                with tarfile.open(fileobj=process.stdout, mode='r|') as archive:
                    yield archive
                process.stdout.close()
                if process.wait() != 0:
                    error.seek(0); raise CheckpointError(error.read().decode(errors='replace'))
            finally:
                if process.poll() is None:
                    process.terminate(); process.wait()
    else:
        with tarfile.open(path, 'r|*') as archive:
            yield archive


def restore(checkpoint: Path, destination: Path, expected_sha256: str | None = None,
            max_unpacked_bytes: int = MAX_UNPACKED) -> dict:
    destination = destination.absolute(); require_new(destination)
    if max_unpacked_bytes <= 0:
        raise CheckpointError('Unpacked byte limit must be positive')
    data = archive_bytes(checkpoint)
    if expected_sha256 is not None and hashlib.sha256(data).hexdigest() != expected_sha256:
        raise CheckpointError('Archive SHA-256 mismatch')
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.cloud-restore-', dir=destination.parent) as temp:
        stage = Path(temp); source = stage / 'input.archive'; source.write_bytes(data); del data
        output = stage / 'output'; output.mkdir()
        seen, total, legacy = set(), 0, False
        with tar_stream(source) as archive:
            for member in archive:
                if not seen and member.name.split('/')[0] == 'payload':
                    legacy = True
                name = PurePosixPath(member.name)
                if (name.is_absolute() or '..' in name.parts or '\\' in member.name or ':' in member.name
                        or not name.parts or name.parts[0] != ('payload' if legacy else 'ai-poise')
                        or not (member.isdir() or member.isfile()) or str(name) in seen
                        or member.size < 0):
                    raise CheckpointError(f'Unsafe or duplicate archive entry: {member.name}')
                seen.add(str(name)); total += member.size
                if total > max_unpacked_bytes:
                    raise CheckpointError('Archive exceeds the explicit unpacked byte limit')
                if legacy:
                    continue
                target = output.joinpath(*name.parts)
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with archive.extractfile(member) as incoming, target.open('xb') as outgoing:
                        shutil.copyfileobj(incoming, outgoing)
                    target.chmod(member.mode & 0o777)
        if legacy:
            return _core.restore(checkpoint, destination)
        report = verify_project(output / 'ai-poise')
        require_new(destination); output.rename(destination)
        return {**report, 'status': 'restored', 'project': str(destination / 'ai-poise')}


def _read_db(path: Path):
    check_database(path)
    db = sqlite3.connect(path.resolve().as_uri() + '?mode=ro&immutable=1', uri=True)
    db.row_factory = sqlite3.Row
    return db


def _tables(db) -> list[str]:
    return [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def database_plan(cloud: Path, local: Path) -> dict:
    hashes = {'cloud_sha256': digest(cloud), 'local_sha256': digest(local)}
    with closing(_read_db(cloud)) as a, closing(_read_db(local)) as b:
        tables_a, tables_b = _tables(a), _tables(b)
        identities = {}
        if 'tasks' in tables_b:
            source_ids = {r[0] for r in b.execute('SELECT id FROM tasks')}
            target_ids = {r[0] for r in a.execute('SELECT id FROM tasks')} if 'tasks' in tables_a else set()
            for task_id in sorted(source_ids):
                classification = 'local_only' if task_id not in target_ids else 'identity_unproved'
                if task_id in target_ids and 'task_events' in tables_a and 'task_events' in tables_b:
                    histories = [db.execute('SELECT version,at,data FROM task_events WHERE task_id=? ORDER BY version,seq', (task_id,)).fetchall() for db in (a, b)]
                    left, right = [[tuple(r) for r in hist] for hist in histories]
                    if left and right and left[:min(len(left), len(right))] == right[:min(len(left), len(right))]:
                        classification = 'common_history_equal' if left == right else ('local_history_extension' if len(right) > len(left) else 'cloud_history_extension')
                    elif left and right:
                        classification = 'divergent_history_needs_identity_decision'
                identities[task_id] = {'classification': classification,
                                       'target': None, 'kind': None, 'reason': None}
        return {**hashes, 'requires_sql_plan': hashes['cloud_sha256'] != hashes['local_sha256'],
                'schema_tables_equal': tables_a == tables_b, 'task_identity': identities,
                'statements': [], 'checks': [],
                'note': 'Matching IDs never prove identity. Inspect requirements and immutable history; explicitly map every incoming task before SQL apply.'}


def merge_database(cloud: Path, local: Path, output: Path, plan: dict) -> dict:
    output = output.absolute(); require_new(output)
    if not isinstance(plan, dict):
        raise CheckpointError('SQL plan must be a JSON object')
    actual = database_plan(cloud, local)
    for key in ('cloud_sha256', 'local_sha256'):
        if plan.get(key) != actual[key]:
            raise CheckpointError(f'Stale SQL plan: {key} changed')
    identities = plan.get('task_identity', {})
    if set(identities) != set(actual['task_identity']):
        raise CheckpointError('Explicit identity decisions required for every incoming Task')
    for source_id, decision in identities.items():
        if (not isinstance(decision.get('target'), str) or not decision['target']
                or decision.get('kind') not in ('same', 'different', 'new')
                or not isinstance(decision.get('reason'), str) or not decision['reason'].strip()
                or (decision['kind'] == 'different' and decision['target'] == source_id)):
            raise CheckpointError(f'Incomplete Task identity decision: {source_id}')
    if not plan.get('checks'):
        raise CheckpointError('At least one explicit SQL postcondition is required')
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.sql-reconcile-', dir=output.parent) as temp:
        target = Path(temp) / 'candidate.sqlite'; shutil.copy2(cloud, target)
        with closing(sqlite3.connect(target, uri=True)) as db:
            db.execute('PRAGMA foreign_keys=ON')
            db.execute('ATTACH DATABASE ? AS incoming', (local.resolve().as_uri() + '?mode=ro&immutable=1',))
            before_ids = {r[0] for r in db.execute('SELECT id FROM main.tasks')} if 'tasks' in _tables(db) else set()
            if len({d['target'] for d in identities.values()}) != len(identities):
                raise CheckpointError('Distinct incoming Tasks require distinct target identities')
            for source_id, d in identities.items():
                if d['kind'] in ('new', 'different') and d['target'] in before_ids:
                    raise CheckpointError('A new/different Task cannot overwrite an existing target identity')
                if d['kind'] == 'same' and d['target'] not in before_ids:
                    raise CheckpointError('A same-Task decision must identify an existing cloud Task')
            def authorize(action, arg1, arg2, database, trigger):
                if action in (sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE):
                    return sqlite3.SQLITE_OK if database == 'main' and not arg1.startswith('sqlite_') else sqlite3.SQLITE_DENY
                if action in (sqlite3.SQLITE_DELETE, sqlite3.SQLITE_ATTACH, sqlite3.SQLITE_DETACH,
                              sqlite3.SQLITE_ALTER_TABLE, sqlite3.SQLITE_DROP_TABLE, sqlite3.SQLITE_PRAGMA):
                    return sqlite3.SQLITE_DENY
                return sqlite3.SQLITE_OK
            try:
                db.execute('BEGIN IMMEDIATE'); db.execute('PRAGMA defer_foreign_keys=ON')
                db.set_authorizer(authorize)
                for statement in plan.get('statements', []):
                    sql = statement['sql'].strip()
                    if not re.match(r'^(INSERT|UPDATE)\b', sql, re.I) or re.search(r'\bREPLACE\b', sql, re.I):
                        raise CheckpointError('A merge plan permits only explicit INSERT/UPDATE statements')
                    changes = db.execute(sql, statement.get('parameters', [])).rowcount
                    if changes != statement['expected_changes']:
                        raise CheckpointError(f'SQL affected {changes}, expected {statement["expected_changes"]}')
                for check in plan['checks']:
                    if not re.match(r'^SELECT\b', check['sql'].strip(), re.I):
                        raise CheckpointError('Postconditions must be SELECT statements')
                    rows = [list(r) for r in db.execute(check['sql'], check.get('parameters', []))]
                    if rows != check['expected']:
                        raise CheckpointError('SQL postcondition failed')
                after_ids = {r[0] for r in db.execute('SELECT id FROM main.tasks')} if 'tasks' in _tables(db) else set()
                if not before_ids <= after_ids or any(d['target'] not in after_ids for d in identities.values()):
                    raise CheckpointError('Task union is incomplete after SQL application')
                db.set_authorizer(None)
                if db.execute('PRAGMA foreign_key_check').fetchall():
                    raise CheckpointError('SQL merge introduced foreign key violations')
                db.commit()
            except BaseException:
                db.set_authorizer(None); db.rollback(); raise
        check_database(target)
        if digest(cloud) != actual['cloud_sha256'] or digest(local) != actual['local_sha256']:
            raise CheckpointError('Input changed during SQL merge')
        require_new(output); target.rename(output)
    return {'status': 'merged', 'database': str(output), 'sha256': digest(output), 'source_hashes': {k: actual[k] for k in ('cloud_sha256', 'local_sha256')}}


def _files_without_git(root: Path) -> dict[str, Path]:
    return {str(p.relative_to(root)): p for p in regular_files(root)
            if '.git' not in p.relative_to(root).parts and p.name != MANIFEST}


def _git_text(root: Path, *args: str) -> str:
    return git(root, *args).decode().strip()


def _wip_commit(target: Path, source: Path, head: str, side: str, ref_base: str) -> str:
    with tempfile.TemporaryDirectory(prefix='.wip-index-', dir=target.parent) as temp:
        index = Path(temp) / 'index'; env = {'GIT_INDEX_FILE': str(index)}
        git(target, 'read-tree', head, env=env)
        names = set(git(source, 'ls-files', '-z').split(b'\0')) | set(git(target, 'ls-tree', '-rz', '--name-only', head).split(b'\0'))
        names.discard(b''); paths = Path(temp) / 'paths'; paths.write_bytes(b'\0'.join(sorted(names)) + b'\0')
        if names:
            git(source, '--literal-pathspecs', '--git-dir=' + str(target / '.git'), '--work-tree=' + str(source),
                'add', '-A', '--pathspec-from-file=' + str(paths), '--pathspec-file-nul', env=env)
        tree = git(target, 'write-tree', env=env).decode().strip()
        if tree == _git_text(target, 'rev-parse', head + '^{tree}'):
            return head
        commit = _git_text(target, *IDENTITY, 'commit-tree', tree, '-p', head, '-m', f'Preserve {side} working files for recovery')
        git(target, 'update-ref', f'{ref_base}/{side}-wip', commit)
        saved = target / '.recovery-sources'; saved.mkdir(exist_ok=True)
        for name, arguments in [('staged', ('diff', '--cached', '--binary', '--full-index')),
                                ('unstaged', ('diff', '--binary', '--full-index'))]:
            (saved / f'{side}-{name}.patch.txt').write_bytes(git(source, *arguments))
        return commit


def merge_projects(cloud: Path, local: Path, destination: Path, apply: bool = False,
                   database_plans: dict | None = None) -> dict:
    cloud, local, destination = cloud.resolve(), local.resolve(), destination.absolute()
    require_new(destination)
    if destination.is_relative_to(cloud) or destination.is_relative_to(local):
        raise CheckpointError('Merge destination must be outside both sources')
    if any((root / '.recovery-sources').exists() for root in (cloud, local)):
        raise CheckpointError('Resolve or separately checkpoint prior merge sources before starting another merge')
    states = [git_state(root) for root in (cloud, local)]
    files = [_files_without_git(root) for root in (cloud, local)]
    hashes = [{name: digest(path) for name, path in data.items()} for data in files]
    different = sorted(name for name in hashes[0].keys() & hashes[1].keys() if hashes[0][name] != hashes[1][name])
    db_diffs = {name: database_plan(files[0][name], files[1][name]) for name in different
                if sqlite_file(files[0][name]) and sqlite_file(files[1][name])}
    ref_base = 'refs/recovery'
    if any(ref in states[0]['refs'] for ref in ('refs/recovery/cloud-head', 'refs/recovery/local-head', 'refs/recovery/cloud-wip', 'refs/recovery/local-wip')) or any(ref.startswith('refs/recovery/local/') for ref in states[0]['refs']):
        ref_base += '/import-' + uuid.uuid4().hex
    report = {'preserved_ref_namespace': ref_base, 'applied': False, 'cloud_head': states[0]['head'], 'local_head': states[1]['head'],
              'different_paths': different, 'database_plans': db_diffs, 'data_conflicts': [], 'git_conflicts': []}
    if not apply:
        return report
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.project-merge-', dir=destination.parent) as temp:
        candidate = Path(temp) / 'candidate'; candidate.mkdir()
        git(candidate, 'init', '-q', '-b', 'recovery/import-' + uuid.uuid4().hex[:12])
        git(candidate, '-c', 'gc.auto=0', 'fetch', '--no-tags', str(cloud), '+refs/*:refs/*')
        git(candidate, 'fetch', '--no-tags', str(cloud), 'HEAD:' + ref_base + '/cloud-head')
        git(candidate, 'fetch', '--no-tags', str(local), '+refs/*:' + ref_base + '/local/*', 'HEAD:' + ref_base + '/local-head')
        git(candidate, 'checkout', '--detach', states[0]['head'])
        for name, path in files[0].items():
            target = candidate / name; target.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(path, target)
        left = _wip_commit(candidate, cloud, states[0]['head'], 'cloud', ref_base)
        right = _wip_commit(candidate, local, states[1]['head'], 'local', ref_base)
        # Only this newly created scratch candidate is reset; both source WIPs
        # have already been retained as Git objects/refs and are never modified.
        git(candidate, 'reset', '--hard', left)
        git(candidate, 'checkout', '-B', 'recovery/merge-' + uuid.uuid4().hex[:12], left)
        try:
            git(candidate, *IDENTITY, 'merge', '--no-ff', '--no-edit', right)
        except CheckpointError as exc:
            conflicts = _git_text(candidate, 'diff', '--name-only', '--diff-filter=U').splitlines()
            if not conflicts:
                raise
            report['git_conflicts'] = conflicts
            report['git_merge_error'] = str(exc)
        tracked = {p.decode() for p in git(local, 'ls-files', '-z').split(b'\0') if p}
        for name, path in files[1].items():
            if name in tracked:
                continue
            target = candidate / name
            if not target.exists():
                target.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(path, target)
            elif digest(target) != hashes[1][name]:
                plan = (database_plans or {}).get(name)
                if plan is not None and name in db_diffs:
                    merged = Path(temp) / ('db-' + uuid.uuid4().hex + '.sqlite')
                    merge_database(files[0][name], path, merged, plan)
                    os.replace(merged, target)
                else:
                    report['data_conflicts'].append(name)
                    saved = candidate / '.recovery-sources/local' / name
                    saved.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(path, saved)
        for root, expected, state in zip((cloud, local), hashes, states):
            if {name: digest(path) for name, path in _files_without_git(root).items()} != expected or git_state(root) != state:
                raise CheckpointError('Source changed during merge; discard only the new candidate and retry')
        report.update(applied=True, status='needs_decisions' if report['data_conflicts'] or report['git_conflicts'] else 'merged',
                      destination=str(destination), head=_git_text(candidate, 'rev-parse', 'HEAD'))
        if (candidate / 'RECOVERY-MERGE.json').exists():
            previous = candidate / '.recovery-sources/prior-merge-report.json'
            previous.parent.mkdir(exist_ok=True)
            shutil.move(candidate / 'RECOVERY-MERGE.json', previous)
        json_write(candidate / 'RECOVERY-MERGE.json', report)
        require_new(destination); candidate.rename(destination)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    pack_parser = sub.add_parser('pack'); pack_parser.add_argument('--project', type=Path, required=True)
    pack_parser.add_argument('--output', type=Path, required=True); pack_parser.add_argument('--compression', choices=('xz', 'zstd', 'compare'), default='compare')
    for command in ('restore', 'verify'):
        p = sub.add_parser(command); p.add_argument('--checkpoint', type=Path, required=True); p.add_argument('--sha256')
        p.add_argument('--max-unpacked-bytes', type=int, default=MAX_UNPACKED)
        if command == 'restore': p.add_argument('--destination', type=Path, required=True)
    p = sub.add_parser('merge'); p.add_argument('--cloud', type=Path, required=True); p.add_argument('--local', type=Path, required=True)
    p.add_argument('--destination', type=Path, required=True); p.add_argument('--apply', action='store_true'); p.add_argument('--database-plans', type=Path)
    p = sub.add_parser('merge-db'); p.add_argument('--cloud', type=Path, required=True); p.add_argument('--local', type=Path, required=True)
    p.add_argument('--output', type=Path); p.add_argument('--plan', type=Path)
    args = parser.parse_args()
    try:
        if args.command == 'pack': result = pack(args.project, args.output, args.compression)
        elif args.command == 'restore': result = restore(args.checkpoint, args.destination, args.sha256, args.max_unpacked_bytes)
        elif args.command == 'verify':
            with tempfile.TemporaryDirectory(prefix='poise-verify-') as temp:
                result = restore(args.checkpoint, Path(temp) / 'restored', args.sha256, args.max_unpacked_bytes)
                result = {k: v for k, v in result.items() if k != 'project'}
                result['status'] = 'verified'
        elif args.command == 'merge': result = merge_projects(args.cloud, args.local, args.destination, args.apply,
                None if args.database_plans is None else json.loads(args.database_plans.read_text()))
        elif args.plan is None:
            if args.output is not None: raise CheckpointError('--output requires --plan; without a plan merge-db is read-only')
            result = database_plan(args.cloud, args.local)
        else:
            if args.output is None: raise CheckpointError('--plan requires a new --output database')
            result = merge_database(args.cloud, args.local, args.output, json.loads(args.plan.read_text()))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 3 if result.get('status') == 'needs_decisions' else 0
    except (CheckpointError, OSError, ValueError, sqlite3.DatabaseError, tarfile.TarError, KeyError) as exc:
        print(json.dumps({'status': 'error', 'error': str(exc)}, ensure_ascii=False)); return 2


if __name__ == '__main__':
    raise SystemExit(main())
