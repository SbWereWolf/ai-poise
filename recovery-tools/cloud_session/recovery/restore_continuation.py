#!/usr/bin/env python3
"""Restore a verified Poise base plus one cumulative continuation into a NEW root.

No network, Git push, config rewrite, ownership takeover, or Task transition.
Only externally selected, checksum-verified inputs are used. Archive code is
never executed. Runtime installation uses the original verified local wheels.
"""
from __future__ import annotations

import argparse
from contextlib import closing
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import tempfile

BASE_SHA = 'd7890704cd40409cebeac0407d7ada39e0da5ce10ce8655e794731635be5be02'
BASE_HEAD = '43e9bc2a5b17a2af4dcfc15554ec32a9c185f937'
CANONICAL_ROOT = Path('/home/sbwerewolf/workdata')
KIT = Path(__file__).resolve().parent
PARTS = [
    (94371840, 'd55c82a1636112ff994cfc54961908bfb08016bdd64cea9628eabfa2816c2578'),
    (94371840, '6849a52042a1c04b91637a28d5377c6503d63c88cb1f0a6e80f425c4800cec02'),
    (75310888, '8558d7ea63980bc9cb24016524f00f61d7ac96261dd3799958bc6b005fe9b273'),
]


class RecoveryError(RuntimeError):
    """An integrity or restoration precondition failed."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RecoveryError(message)


def digest(path: Path) -> str:
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, f'Duplicate JSON field: {key}')
        result[key] = value
    return result


def read_json(path: Path):
    return json.loads(path.read_text(encoding='utf-8'), object_pairs_hook=unique_object)


def write_json(path: Path, data) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def relative_name(name: str) -> PurePosixPath:
    require(isinstance(name, str) and bool(name), 'Empty path')
    p = PurePosixPath(name)
    require(not p.is_absolute() and p.parts and '..' not in p.parts
            and '\\' not in name and ':' not in name and '\x00' not in name
            and str(p) == name.rstrip('/') and p != PurePosixPath('.'),
            f'Unsafe relative path: {name}')
    return p


def verify_task_artifact(task_dir: Path, manifest: dict) -> str | None:
    """Verify a declared artifact; retain legacy Sequencing checkpoints."""
    name = manifest.get('artifact_relative_path',
                        'apps/sequencing/docs/contract-seq.md')
    expected = manifest.get('contract_sha256')
    if name is None:
        require(expected is None, 'Absent artifact cannot have a checksum')
        return None
    relative = relative_name(name)
    require('.git' not in relative.parts, 'Git metadata is not a Task artifact')
    require(isinstance(expected, str) and bool(re.fullmatch(r'[0-9a-f]{64}', expected)),
            'Declared artifact requires an exact SHA-256')
    target = task_dir.joinpath(*relative.parts)
    require(not any(p.is_symlink() for p in (target, *target.parents)),
            'Symlink Task artifact or ancestor refused')
    require(target.is_file(), 'Declared Task artifact is absent')
    actual = digest(target)
    require(actual == expected, 'Restored product contract differs')
    return actual


def new_destination(path: Path) -> Path:
    p = path.absolute()
    require('..' not in p.parts, 'Destination cannot contain ..')
    for parent in (p, *p.parents):
        require(not parent.is_symlink(), f'Symlink destination/ancestor: {parent}')
    require(not p.exists(), f'Destination already exists: {p}')
    return p


def extract_checkpoint(archive: Path, destination: Path, expected_sha256: str,
                       *, max_bytes: int = 2 * 1024**3) -> Path:
    """Extract regular files only; verify exact inventory before publication."""
    destination = new_destination(destination)
    require(max_bytes > 0, 'Unpacked limit must be positive')
    require(bool(re.fullmatch(r'[0-9a-f]{64}', expected_sha256)), 'Invalid SHA-256')
    require(digest(archive) == expected_sha256, 'Checkpoint SHA-256 mismatch')
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.checkpoint-', dir=destination.parent) as temp:
        temp = Path(temp)
        root_name = None
        seen = set()
        directory_modes = {}
        total = 0
        with tarfile.open(archive, 'r:*') as t:
            for count, member in enumerate(t, 1):
                require(count <= 100000, 'Too many archive entries')
                p = relative_name(member.name)
                root_name = root_name or p.parts[0]
                require(p.parts[0] == root_name, 'Multiple archive roots')
                require(str(p) not in seen, f'Duplicate archive entry: {p}')
                seen.add(str(p))
                require(member.isfile() or member.isdir(), f'Link/special file refused: {p}')
                # Shared cloud storage contributes inherited directory setgid.
                # Accept that metadata, but never restore privilege/sticky bits.
                forbidden = 0o5000 if member.isdir() else 0o7000
                require(member.size >= 0 and not (member.mode & forbidden), 'Invalid size or special mode')
                total += member.size
                require(total <= max_bytes, 'Unpacked byte limit exceeded')
                target = temp.joinpath(*p.parts)
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                    directory_modes[str(p)] = member.mode & 0o777
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with t.extractfile(member) as source, target.open('xb') as output:
                        shutil.copyfileobj(source, output)
                    target.chmod(member.mode & 0o777)
        require(root_name is not None, 'Empty checkpoint')
        root = temp/root_name
        for directory in [root, *(p for p in root.rglob('*') if p.is_dir())]:
            directory.chmod(directory_modes.get(str(directory.relative_to(temp)), 0o700))
        inventory = read_json(root/'FILES.json')
        require(isinstance(inventory, dict), 'Invalid file inventory')
        actual = {str(p.relative_to(root)) for p in root.rglob('*') if p.is_file()}
        require(actual == set(inventory) | {'FILES.json', 'SHA256SUMS'}, 'Inventory membership mismatch')
        for name, value in inventory.items():
            relative_name(name)
            require(isinstance(value, str) and bool(re.fullmatch(r'[0-9a-f]{64}', value)),
                    'Invalid inventory hash')
            require(digest(root/name) == value, f'Payload checksum mismatch: {name}')
        sums = ''.join(f'{value}  {name}\n' for name, value in inventory.items())
        require((root/'SHA256SUMS').read_text() == sums, 'SHA256SUMS disagrees with FILES.json')
        require(read_json(root/'CHECKPOINT.json').get('schema') == 'poise-cloud-continuation/v1',
                'Unsupported continuation schema')
        new_destination(destination)
        root.rename(destination)
    return destination


def run(argv: list[str], cwd: Path | None = None, timeout: int = 180) -> bytes:
    env = dict(os.environ)
    env.update({'GIT_TERMINAL_PROMPT': '0', 'GIT_CONFIG_NOSYSTEM': '1',
                'GIT_CONFIG_GLOBAL': os.devnull, 'PYTHONDONTWRITEBYTECODE': '1',
                'PIP_NO_INDEX': '1', 'PIP_DISABLE_PIP_VERSION_CHECK': '1'})
    r = subprocess.run(argv, cwd=cwd, env=env, capture_output=True, timeout=timeout)
    if r.returncode:
        raise RecoveryError(f'Command failed ({r.returncode}): {argv[0:3]}\n'
                            + r.stderr.decode(errors='replace')[-4000:])
    return r.stdout


def git(repo: Path, *args: str) -> bytes:
    return run(['git', '-c', 'core.hooksPath=/dev/null', '-c', 'core.fsmonitor=false',
                '-C', str(repo), *args])


def apply_worktree(repo: Path, overlay: Path, row: dict) -> None:
    """Retain the exact difference between index, working tree and untracked files."""
    require(git(repo, 'rev-parse', 'HEAD').decode().strip() == row['head'], 'Wrong baseline HEAD')
    require(git(repo, 'status', '--porcelain=v1', '--untracked-files=all') == b'',
            'Worktree must be clean before applying saved WIP')
    role = row['role']
    require(role in {'main', 'task'}, 'Unknown worktree role')
    for suffix, flags in [('index', ['--index']), ('work', [])]:
        patch = overlay/f'{role}-{suffix}.patch'
        if patch.stat().st_size:
            git(repo, 'apply', '--check', *flags, str(patch))
            git(repo, 'apply', *flags, str(patch))
    for name in row['untracked']:
        p = relative_name(name)
        require('.git' not in p.parts, 'Untracked Git metadata refused')
        target = repo.joinpath(*p.parts)
        require(not target.exists() and not target.is_symlink(), f'Untracked conflict: {name}')
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(overlay/'untracked'/role/name, target)
    require(git(repo, 'status', '--porcelain=v1', '--untracked-files=all').decode() == row['status'],
            'Restored index/worktree status differs')


def load_verified_core():
    checks = read_json(KIT/'VENDOR-SHA256.json')
    for name, value in checks.items():
        relative_name(name)
        require(digest(KIT/name) == value, f'Recovery dependency changed: {name}')
    spec = importlib.util.spec_from_file_location('poise_original_recovery',
            KIT/'vendor/recovery-tools/cloud_recovery.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def join_parts(directory: Path, output: Path) -> Path:
    new_destination(output)
    with output.open('xb') as target:
        for number, (size, expected) in enumerate(PARTS):
            stem = f'POISE-PS-DEV-002-CLOUD-HANDOFF-20260926.tar.xz.part-{number:02}'
            matches = [p for p in (directory/stem, directory/(stem+'.bin')) if p.is_file()]
            require(len(matches) == 1, f'Expected exactly one file for {stem}')
            p = matches[0]
            require(p.stat().st_size == size and digest(p) == expected, f'Part mismatch: {stem}')
            with p.open('rb') as source:
                shutil.copyfileobj(source, target)
    require(digest(output) == BASE_SHA, 'Combined base archive differs')
    return output


def excluded_operator(name: str) -> bool:
    return (any(p in {'.venv', '.python', '__pycache__'} for p in PurePosixPath(name).parts)
            or name.startswith('operator/state/backups/'))


def file_metadata(path: Path) -> dict:
    return {'sha256': digest(path), 'bytes': path.stat().st_size, 'mode': path.stat().st_mode & 0o777}


def check_database(path: Path) -> dict:
    with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro&immutable=1', uri=True)) as db:
        require(db.execute('PRAGMA integrity_check').fetchall() == [('ok',)], f'Corrupt database: {path}')
        require(not db.execute('PRAGMA foreign_key_check').fetchall(), f'Foreign key failure: {path}')
        return {'integrity_check': 'ok', 'foreign_key_check': 'ok',
                'user_version': db.execute('PRAGMA user_version').fetchone()[0]}


def install_runtime(root: Path, python: Path) -> dict:
    operator = root/'poise-op/operator'
    venv = operator/'.venv'
    new_destination(venv)
    run([str(python), '-I', '-m', 'venv', str(venv)])
    interpreter = venv/'bin/python'
    output = run([str(interpreter), '-I', '-m', 'pip', 'install', '--no-index',
                  '--find-links', str(operator/'wheelhouse'),
                  'ai-poise==1.0.1', 'environment-maintenance==0.1.1'])
    (root/'.cloud-recovery/runtime-install.log').write_bytes(output)
    versions = json.loads(run([str(interpreter), '-I', '-c',
        'import importlib.metadata as m,json,sys; print(json.dumps({"python":sys.version,'
        '"ai-poise":m.version("ai-poise"),"environment-maintenance":m.version("environment-maintenance")}))']))
    require(versions['ai-poise'] == '1.0.1' and versions['environment-maintenance'] == '0.1.1',
            'Installed operator version differs')
    run([str(interpreter), '-I', '-B', '-m', 'poise', '--help'])
    return versions


def restore(base: Path, checkpoint: Path, checksum: str, destination: Path,
            *, python: Path | None = None) -> dict:
    destination = new_destination(destination)
    require(digest(base) == BASE_SHA, 'Immutable base SHA-256 mismatch')
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.poise-cd-', dir=destination.parent) as tmp:
        tmp = Path(tmp)
        overlay = extract_checkpoint(checkpoint, tmp/'overlay', checksum)
        m = read_json(overlay/'CHECKPOINT.json')
        require(m.get('base_archive_sha256') == BASE_SHA and m.get('base_commit') == BASE_HEAD,
                'Continuation targets a different base')
        rows = m['worktrees']
        require(len(rows) == 2 and [row['role'] for row in rows] == ['main','task'],
                'This recovery profile requires main plus one Task worktree')
        require(rows[0]['relative_path'] == 'ai-poise', 'Main root differs')
        expected_task = 'ai-poise/projects/ai-poise/worktrees/'+m['task']
        require(rows[1]['relative_path'] == expected_task
                and rows[1]['branch'] == 'tasks/'+m['task'], 'Task workspace binding differs')
        for row in rows:
            relative_name(row['relative_path'])
            require(bool(re.fullmatch(r'[0-9a-f]{40}', row['head'])), 'Invalid Git object ID')
            run(['git', 'check-ref-format', '--branch', row['branch']])
        stage = tmp/'restored'
        base_report = load_verified_core().restore(base, stage, expected_sha256=BASE_SHA)
        repo = stage/'ai-poise'
        preservation = stage/'.cloud-recovery'; preservation.mkdir()
        # Read the archived root handoff before any post-base mutation.
        handoff = (repo/'HANDOFF.md').read_text(encoding='utf-8')
        (preservation/'BASE-HANDOFF.md').write_text(handoff, encoding='utf-8')
        base_manifest = read_json(repo/'RECOVERY-MANIFEST.json')
        shutil.move(str(repo/'RECOVERY-MANIFEST.json'), preservation/'BASE-RECOVERY-MANIFEST.json')
        shutil.move(str(repo/'.cloud-handoff/poise-op'), stage/'poise-op')
        (repo/'.cloud-handoff').rmdir()
        operator = stage/'poise-op'
        # Restore source directory modes; never chmod an existing live installation.
        with tarfile.open(base, 'r:xz') as t:
            for entry in t:
                prefix = 'ai-poise/.cloud-handoff/poise-op/'
                if entry.isdir() and entry.name.startswith(prefix):
                    p = operator/str(relative_name(entry.name[len(prefix):]))
                    require(p.is_dir(), f'Base directory missing: {p}')
                    p.chmod(entry.mode & 0o777)
        prefix = '.cloud-handoff/poise-op/'
        expected = {k[len(prefix):]:v for k,v in base_manifest['files'].items()
                    if k.startswith(prefix) and not excluded_operator(k[len(prefix):])}
        for name in m['operator_removed']:
            relative_name(name)
            require(name in expected, f'Unknown removed base file: {name}')
            (operator/name).unlink(); del expected[name]
        for name, metadata in m['operator_files'].items():
            relative_name(name)
            require(not excluded_operator(name), 'Reproducible runtime must not be overlaid')
            source = overlay/'operator'/name
            require(file_metadata(source) == metadata, f'Operator overlay differs: {name}')
            target = operator/name; target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target); expected[name] = metadata
        for name, mode in m.get('operator_directory_modes', {}).items():
            relative_name(name)
            require(type(mode) is int and 0 <= mode <= 0o777, 'Invalid directory mode')
            target = operator/name
            target.mkdir(parents=True, exist_ok=True); target.chmod(mode)
        actual = {str(p.relative_to(operator)): file_metadata(p) for p in operator.rglob('*')
                  if p.is_file() and not excluded_operator(str(p.relative_to(operator)))}
        require(actual == expected, 'Restored operator inventory differs')
        # Only the fresh isolated repository is reset; archived HANDOFF is preserved above.
        git(repo, 'restore', '--source=HEAD', '--staged', '--worktree', '--', 'HANDOFF.md')
        require(git(repo, 'status', '--porcelain=v1', '--untracked-files=all') == b'',
                'Unexpected unrepresented base repository WIP')
        git(repo, 'bundle', 'verify', str(overlay/'delta.bundle'))
        for row in rows:
            role = row['role']
            git(repo, 'fetch', '--no-tags', str(overlay/'delta.bundle'),
                f'refs/heads/{row["branch"]}:refs/recovery/{role}')
            require(git(repo,'rev-parse',f'refs/recovery/{role}').decode().strip() == row['head'],
                    'Bundle ref disagrees with checkpoint')
        git(repo, 'checkout', '-B', rows[0]['branch'], rows[0]['head'])
        task_dir = stage/rows[1]['relative_path']; task_dir.parent.mkdir(parents=True, exist_ok=True)
        git(repo, 'worktree', 'add', '-b', rows[1]['branch'], str(task_dir), rows[1]['head'])
        for row in rows:
            apply_worktree(stage/row['relative_path'], overlay, row)
        artifact_digest = verify_task_artifact(task_dir, m)
        databases = {}
        for name in expected:
            p = operator/name
            with p.open('rb') as f:
                is_db = f.read(16) == b'SQLite format 3\x00'
            if is_db:
                databases[name] = check_database(p)
        require(digest(operator/'operator/state/state.sqlite') == m['database_sha256'],
                'Restored Task database checksum differs')
        git(repo, 'fsck', '--strict', '--no-dangling')
        for name in ('CHECKPOINT.json','FILES.json','SHA256SUMS','delta.bundle',
                     'main-index.patch','main-work.patch','task-index.patch','task-work.patch'):
            shutil.copy2(overlay/name, preservation/name)
        for name in ('kit','evidence'):
            if (overlay/name).is_dir():
                shutil.copytree(overlay/name, preservation/name)
        report = {'status':'files_restored','base_verification':base_report,
                  'base_sha256':BASE_SHA,'checkpoint_sha256':checksum,
                  'main_commit':m['main_commit'],'task_commit':rows[1]['head'],
                  'task':m['task'],'worktrees':rows,'contract_sha256':artifact_digest,
                  'artifact_relative_path':m.get('artifact_relative_path', 'apps/sequencing/docs/contract-seq.md'),
                  'database_sha256':m['database_sha256'],'databases':databases,
                  'canonical_paths_match':destination == CANONICAL_ROOT,
                  'managed_config_modified':False,'task_lifecycle_mutated':False,
                  'archived_caller_binding_reused':False,'git_push_attempted':False,
                  'runtime_installation':'not_requested','managed_operator_work_executed':False,
                  'resume_note':'Create a fresh caller binding; do not reuse archived Session identity. '
                   'Inspect current saved ownership and stage through the public owner. '
                   'An alternate root verifies files/runtime only; managed paths remain canonical.'}
        new_destination(destination)
        stage.rename(destination)
    # Linked-worktree Git metadata and venv shebangs contain absolute paths.
    # Repair/build at the final location, never before the directory move.
    report_path = destination/'.cloud-recovery/RESTORE-REPORT.json'
    try:
        git(destination/'ai-poise', 'worktree', 'repair', str(destination/rows[1]['relative_path']))
        for row in rows:
            p = destination/row['relative_path']
            require(git(p,'rev-parse','--show-toplevel').decode().strip() == str(p), 'Worktree path not repaired')
            require(git(p,'rev-parse','HEAD').decode().strip() == row['head'], 'Post-move HEAD differs')
            require(git(p,'status','--porcelain=v1','--untracked-files=all').decode() == row['status'],
                    'Post-move WIP differs')
        if python is not None:
            report['runtime_installation'] = install_runtime(destination, python)
        report['status'] = 'restored_and_verified'
    except Exception as e:
        report['status'] = 'files_published_post_install_failed'
        report['error'] = str(e)
        write_json(report_path, report)
        raise
    write_json(report_path, report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--base', type=Path)
    source.add_argument('--parts-directory', type=Path)
    parser.add_argument('--checkpoint', type=Path, required=True)
    parser.add_argument('--checkpoint-sha256', required=True)
    parser.add_argument('--destination', type=Path, required=True)
    parser.add_argument('--install-operator', action='store_true')
    parser.add_argument('--python', type=Path, default=Path(sys.executable))
    args = parser.parse_args()
    try:
        new_destination(args.destination)
        with tempfile.TemporaryDirectory(prefix='poise-base-') as tmp:
            base = args.base or join_parts(args.parts_directory, Path(tmp)/'base.tar.xz')
            result = restore(base, args.checkpoint, args.checkpoint_sha256, args.destination,
                             python=args.python.absolute() if args.install_operator else None)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (RecoveryError, OSError, ValueError, KeyError, TypeError, tarfile.TarError,
            subprocess.SubprocessError) as e:
        print(json.dumps({'status':'rejected','error':str(e)},ensure_ascii=False), file=sys.stderr)
        return 2

if __name__ == '__main__':
    raise SystemExit(main())
