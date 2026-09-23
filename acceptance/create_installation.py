#!/usr/bin/env python3
"""Create a separate empty installation after black-box acceptance has passed."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import shlex
import sqlite3
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from release_evidence import sha256, validate_acceptance, validate_delivery


def authorize(protocol: Path, manifest: Path, python: Path, source: Path) -> dict:
    report = json.loads(protocol.read_text())
    specification = json.loads(manifest.read_text())
    validate_acceptance(report, specification)
    inputs = report['inputs']
    if inputs['manifest_sha256'] != sha256(manifest) or inputs['source_dirty']:
        raise ValueError('Acceptance must refer to this PMI and a clean committed source')
    head = subprocess.check_output(['git', '-c', 'safe.directory=' + str(source),
                                    '-C', str(source), 'rev-parse', 'HEAD'], text=True).strip()
    if head != inputs['source_commit']:
        raise ValueError('Accepted source commit differs from the installation source')
    if validate_delivery(python, source) != inputs['delivery']:
        raise ValueError('Accepted installed delivery changed')
    observation = json.loads(subprocess.check_output([str(python), '-I', '-c',
        'import json,platform,sys; r=platform.freedesktop_os_release();print(json.dumps([r["ID"],r["VERSION_ID"],list(sys.version_info[:2]),platform.machine()]))'], text=True))
    qualified = inputs['environment']
    if observation != [qualified['os_id'], qualified['os_version'], qualified['python'], qualified['machine']]:
        raise ValueError('This installation environment has not been qualified by the protocol')
    if os.geteuid() == 0:
        raise ValueError('Run installation as its ordinary owner, not root')
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='Создать новую пустую установку после успешных ПСИ.')
    for name in ('protocol', 'manifest', 'python', 'source', 'repository', 'destination'):
        parser.add_argument('--' + name, required=True, type=Path)
    parser.add_argument('--project', required=True)
    parser.add_argument('--base-ref', required=True)
    parser.add_argument('--author-name', required=True)
    parser.add_argument('--author-email', required=True)
    args = parser.parse_args(argv)
    try:
        args.source = args.source.resolve(); args.repository = args.repository.resolve()
        args.destination = args.destination.absolute(); args.python = args.python.absolute()
        if args.destination.exists() or args.destination.is_symlink():
            raise ValueError('Destination must be new; existing installations are never reset')
        if not args.repository.is_dir():
            raise ValueError('Explicit target Git repository must exist')
        report = authorize(args.protocol, args.manifest, args.python, args.source)
        args.destination.mkdir(mode=0o750, parents=False)
        log = []
        env = {'PATH': str(args.python.parent) + ':/usr/bin:/bin', 'HOME': str(args.destination),
               'LANG': 'C.UTF-8', 'PYTHONDONTWRITEBYTECODE': '1'}
        def run(command):
            completed = subprocess.run(list(map(str, command)), env=env, cwd=args.destination,
                                       capture_output=True, text=True, timeout=120)
            log.append({'argv': list(map(str, command)), 'exit_code': completed.returncode,
                        'stdout': completed.stdout, 'stderr': completed.stderr})
            (args.destination / 'installation-log.json').write_text(json.dumps(log, ensure_ascii=False, indent=2) + '\n')
            if completed.returncode:
                raise ValueError(f'Public installation command failed: {completed.stdout} {completed.stderr}')
            return json.loads(completed.stdout)
        profile, state = args.destination / 'profile', args.destination / 'state'
        run([args.python, '-I', '-m', 'poise_environment.profile', '--source', args.source,
             '--output', profile, '--state', state, '--repository', args.repository,
             '--project', args.project, '--base-ref', args.base_ref,
             '--author-name', args.author_name, '--author-email', args.author_email])
        for verb in ('deps', 'infra', 'check'):
            result = run([args.python.parent / 'poise', verb, '--catalog', profile / 'requirements.json',
                          '--repairs', profile / 'repairs.json'])
            if result['status'] not in ('ready', 'repaired'):
                raise ValueError('Infrastructure did not become ready')
        databases = {}
        for relative in ('state.sqlite', 'database/requirements.sqlite', 'telemetry/events.sqlite'):
            path = state / relative
            with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True) as db:
                if db.execute('PRAGMA integrity_check').fetchall() != [('ok',)] or db.execute('PRAGMA foreign_key_check').fetchall():
                    raise ValueError('New database integrity check failed')
                if relative == 'state.sqlite' and db.execute('SELECT count(*) FROM tasks').fetchone()[0] != 0:
                    raise ValueError('New installation unexpectedly contains tasks')
            databases[relative] = sha256(path)
        installation = {'schema': 'poise/installation/v1', 'status': 'ready',
            'source_commit': report['inputs']['source_commit'], 'acceptance_profile': report['inputs']['profile'],
            'acceptance_protocol_sha256': sha256(args.protocol), 'python': str(args.python),
            'repository': str(args.repository), 'project_id': args.project,
            'config': str(profile / 'project/project.json'), 'state_root': str(state),
            'databases': databases, 'imported_development_history': False, 'task_count': 0,
            'host_deployment': False}
        (args.destination / 'INSTALLATION.json').write_text(json.dumps(installation, ensure_ascii=False, indent=2) + '\n')
        (args.destination / 'environment.sh').write_text('export POISE_CONFIG=' + shlex.quote(installation['config']) + '\n')
        print(json.dumps(installation, ensure_ascii=False)); return 0
    except (OSError, ValueError, KeyError, subprocess.SubprocessError, sqlite3.Error) as error:
        print(json.dumps({'status': 'failed', 'error': str(error)}, ensure_ascii=False)); return 2


if __name__ == '__main__':
    raise SystemExit(main())
