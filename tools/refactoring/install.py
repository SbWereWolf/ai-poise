#!/usr/bin/env python3
"""Install explicitly configured application-local analyzer dependencies."""
import argparse
import hashlib
import io
import json
import os
import subprocess
import urllib.request
import zipfile
from pathlib import Path

parser = argparse.ArgumentParser(description=__doc__)
for option in ['root', 'configuration', 'node', 'npm', 'python', 'php', 'composer', 'runtime-path']:
    parser.add_argument('--' + option, required=True)
parser.add_argument('--startup-only', action='store_true')
args = parser.parse_args()
root = Path(args.root).resolve(strict=True)
configuration = json.loads(Path(args.configuration).read_text())
if configuration['schema_version'] != 1:
    raise SystemExit('Unsupported refactoring installation schema')
for relative in configuration['toolchains']:
    config_path = (root / relative).resolve(strict=True)
    config_path.relative_to(root)
    config = json.loads(config_path.read_text())
    if config['schema_version'] != 1:
        raise SystemExit(f'Unsupported toolchain schema: {relative}')
    folder = config_path.parent
    cache = folder / '.cache'
    env = dict(os.environ, PATH=args.runtime_path, COMPOSER_HOME=str(cache / 'composer-home'), COMPOSER_CACHE_DIR=str(cache / 'composer'))

    def run(argv):
        subprocess.run(argv, cwd=folder, env=env, check=True)

    if not args.startup_only:
        run([args.node, args.npm, 'ci', '--no-audit', '--no-fund', '--cache', str(cache / 'npm')])
    if 'composer_manifest' in config and not args.startup_only:
        run([args.php, args.composer, 'install', '--no-interaction', '--prefer-dist', '--no-progress', '--no-scripts'])
    if 'python_requirements' in config and not args.startup_only:
        if not (folder / '.venv').exists():
            run([args.python, '-m', 'venv', str(folder / '.venv')])
        run([str(folder / '.venv/bin/python'), '-m', 'pip', 'install', '--cache-dir', str(cache / 'pip'), '-r', 'requirements.txt'])
    if 'codescene_distribution' in config and not args.startup_only:
        distribution = config['codescene_distribution']
        executable = folder / 'bin/cs'
        if not executable.exists() or hashlib.sha256(executable.read_bytes()).hexdigest() != distribution['binary_sha256']:
            with urllib.request.urlopen(distribution['url'], timeout=60) as response:
                archive_bytes = response.read()
            if hashlib.sha256(archive_bytes).hexdigest() != distribution['archive_sha256']:
                raise SystemExit(f'CodeScene archive identity changed: {relative}; select and record an explicit updated distribution')
            with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
                binary = archive.read(distribution['member'])
            if hashlib.sha256(binary).hexdigest() != distribution['binary_sha256']:
                raise SystemExit(f'CodeScene binary integrity mismatch: {relative}')
            executable.parent.mkdir(exist_ok=True)
            executable.write_bytes(binary)
            executable.chmod(0o755)
    for tool in config['tools']:
        argv = [value.replace('${php}', args.php).replace('${node}', args.node).replace('${python}', args.python) for value in config['startup'][tool]]
        if any('${' in value for value in argv):
            raise SystemExit(f'Unresolved startup configuration: {tool}')
        executable = str((folder / argv[0]).resolve(strict=True))
        run([executable] + argv[1:])
    print(json.dumps({'application': config['application'], 'startup': 'passed'}), flush=True)
