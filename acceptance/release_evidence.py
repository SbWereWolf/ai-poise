"""Shared, application-independent attribution of acceptance inputs and results."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess


def sha256(path: Path | str) -> str:
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def source_tree(root: Path | str) -> str:
    """Hash build inputs, including uncommitted bytes and ordinary mode bits."""
    root = Path(root).resolve()
    records = []
    excluded = {'.git', '__pycache__', '.pytest_cache', 'build', 'dist'}
    for directory, dirs, names in os.walk(root, followlinks=False):
        dirs[:] = sorted(d for d in dirs if d not in excluded and not d.endswith('.egg-info'))
        for name in sorted(names + [d for d in dirs if (Path(directory) / d).is_symlink()]):
            p = Path(directory) / name
            rel = str(p.relative_to(root))
            if p.is_symlink():
                records.append([rel, 'symlink', os.readlink(p)])
            elif p.is_file():
                records.append([rel, p.stat().st_mode & 0o777, sha256(p)])
            else:
                raise ValueError(f'Unsupported build input: {rel}')
    return hashlib.sha256(json.dumps(sorted(records), separators=(',', ':')).encode()).hexdigest()


def installed_tree(python: Path | str) -> dict:
    """Observe actual installed files, not just a potentially stale wheel manifest."""
    code = '''import hashlib,importlib.metadata as m,json,pathlib,sys
root=pathlib.Path(sys.prefix).resolve(); result={}
for name in ('ai-poise','environment-maintenance'):
 d=m.distribution(name); files={}
 for rel in d.files or []:
  if str(rel).endswith(('.pyc','/RECORD')): continue
  p=pathlib.Path(d.locate_file(rel)).resolve()
  if not p.is_relative_to(root): raise ValueError('Package file outside installation: '+str(p))
  if p.is_file(): files[str(p.relative_to(root))]=hashlib.file_digest(p.open('rb'),'sha256').hexdigest()
 result[name]={'version':d.version,'files':files}
print(json.dumps(result,sort_keys=True))'''
    return json.loads(subprocess.check_output([str(python), '-I', '-c', code], text=True, timeout=30))


def validate_delivery(python: Path | str, source: Path | str) -> dict:
    python = Path(python).absolute()  # Do not resolve the venv interpreter symlink.
    root = python.parent.parent.parent
    record = json.loads((root / 'delivery.json').read_text())
    if record.get('source_fingerprint') != source_tree(source):
        raise ValueError('Source differs from the source used to build the installed delivery')
    wheels = {f.name: sha256(f) for f in (root / 'wheelhouse').glob('*.whl')}
    if not wheels or wheels != record.get('wheels'):
        raise ValueError('Delivered wheel membership or bytes changed')
    if installed_tree(python) != record.get('installed'):
        raise ValueError('Installed application bytes differ from the delivery record')
    return {'record_sha256': sha256(root / 'delivery.json'), 'wheels': wheels,
            'source_fingerprint': record['source_fingerprint'],
            'installed_fingerprint': hashlib.sha256(json.dumps(record['installed'], sort_keys=True).encode()).hexdigest()}


def validate_acceptance(report: dict, manifest: dict) -> None:
    expected = [s['id'] for s in manifest['steps']]
    observed = report.get('steps', [])
    if report.get('status') != 'PASS' or report.get('environment_errors'):
        raise ValueError('Only a complete successful acceptance protocol authorizes installation')
    if not expected or [s.get('id') for s in observed] != expected or len(set(expected)) != len(expected):
        raise ValueError('Acceptance step membership/order is incomplete or duplicated')
    for step in observed:
        if step.get('status') != 'PASS' or step.get('exit_code') != 0 or step.get('timed_out'):
            raise ValueError('An acceptance step has not passed')
        for key in ('result', 'stdout', 'stderr'):
            if sha256(step[key]) != step[key + '_sha256']:
                raise ValueError('Acceptance evidence changed: ' + step['id'])
        if json.loads(Path(step['result']).read_text()).get('status') != 'PASS':
            raise ValueError('PSI result is not PASS')
