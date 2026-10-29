"""Real wheel/noneditable consumer; no MCP, check or output implementation."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tomllib
import zipfile

from tests.named_checks.support import ROOT, write_json


def installed(case):
    root = case.root / 'installed'
    root.mkdir()
    proof = {'native_receipt': False, 'live_host_delivery': False, 'commands': [], 'members': []}
    environment = {k: v for k, v in case.environment.items() if k != 'PYTHONPATH'}
    environment.update(PIP_CACHE_DIR=str(root / 'cache'), TMPDIR=str(root))

    def run(argv):
        value = subprocess.run(argv, cwd=root, env=environment, capture_output=True, text=True, timeout=120)
        proof['commands'].append({'argv': argv, 'cwd': str(root), 'exit_code': value.returncode,
                                  'stdout': value.stdout, 'stderr': value.stderr})
        write_json(root / 'proof.json', proof)
        assert value.returncode == 0, proof['commands'][-1]
        return value.stdout

    source = root / 'build-source'
    source.mkdir()
    for directory in ['src', 'apps']:
        shutil.copytree(ROOT / directory, source / directory,
                        ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    shutil.copy2(ROOT / 'pyproject.toml', source / 'pyproject.toml')
    wheels = root / 'wheels'; wheels.mkdir()
    run([sys.executable, '-B', '-m', 'pip', 'wheel', '--no-deps', '--no-index',
         '--no-build-isolation', '--wheel-dir', str(wheels), str(source)])
    wheel, = wheels.glob('*.whl')
    environment_root = root / 'venv'
    run([sys.executable, '-B', '-m', 'venv', '--without-pip', str(environment_root)])
    python = str(environment_root / 'bin/python')
    run([sys.executable, '-B', '-m', 'pip', '--python', python, 'install', '--no-deps', '--no-index', str(wheel)])
    origin = json.loads(run([python, '-I', '-B', '-c',
        'import json,sys,poise,importlib.metadata as m;d=m.distribution("ai-poise");'
        'print(json.dumps({"prefix":sys.prefix,"module":poise.__file__,"version":d.version,'
        '"direct_url":d.read_text("direct_url.json")}))']))
    assert Path(origin['module']).is_relative_to(environment_root)
    assert not Path(origin['module']).is_relative_to(ROOT)
    assert not json.loads(origin['direct_url']).get('dir_info', {}).get('editable', False)
    assert origin['version'] == tomllib.loads((ROOT / 'pyproject.toml').read_text())['project']['version']
    expected = {str(path.relative_to(ROOT / 'src')): path.read_bytes()
                for package in ['poise', 'poise_environment']
                for path in (ROOT / 'src' / package).rglob('*')
                if path.is_file() and '__pycache__' not in path.parts and path.suffix != '.pyc'}
    expected.update({str(path.relative_to(ROOT)): path.read_bytes()
                     for path in (ROOT / 'apps/bootstrap').rglob('*')
                     if path.is_file() and '__pycache__' not in path.parts and path.suffix != '.pyc'})
    with zipfile.ZipFile(wheel) as archive:
        actual = {name: archive.read(name) for name in archive.namelist()
                  if name.startswith(('poise/', 'poise_environment/', 'apps/bootstrap/')) and not name.endswith('/')}
    assert sorted(actual) == sorted(expected)
    destination = Path(origin['module']).parent.parent
    for name, body in sorted(expected.items()):
        assert actual[name] == body == (destination / name).read_bytes()
        proof['members'].append({'path': name, 'size': len(body), 'sha256': hashlib.sha256(body).hexdigest()})
    proof.update(origin=origin, wheel_path=str(wheel), wheel_sha256=hashlib.sha256(wheel.read_bytes()).hexdigest())
    write_json(root / 'proof.json', proof)
    return python
