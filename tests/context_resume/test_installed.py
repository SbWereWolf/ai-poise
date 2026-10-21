"""Two real noneditable consumers, full package membership and native effects."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tomllib
import zipfile

import pytest

from tests.context_resume.helpers import (
    ContextFeatureUnavailable, InstalledPackageIncomplete, Recovery, card, fixture, future_definition,
)
from tests.conftest import write_json


SOURCE = Path(__file__).resolve().parents[2]
DRIVER = Path(__file__).with_name('installed_driver.py')


@pytest.mark.parametrize('port', ['cli', 'python'])
def test_installed_context(port, project, tmp_path, monkeypatch):
    root = Path(os.environ['POISE_RUN_OUTPUT_DIR']) / ('context-installed-' + port)
    root.mkdir()
    work = root / 'source'; work.mkdir()
    shutil.copytree(SOURCE / 'src', work / 'src',
                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    shutil.copytree(SOURCE / 'apps', work / 'apps',
                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    shutil.copy2(SOURCE / 'pyproject.toml', work / 'pyproject.toml')
    env = {k: v for k, v in os.environ.items() if k not in {
        'PYTHONPATH', 'CODEX_SESSION_ID', 'CODEX_THREAD_ID', 'POISE_CALLER_BINDING',
        'POISE_SESSION', 'POISE_CONFIG'}}
    env.update(PYTHONDONTWRITEBYTECODE='1', PIP_CACHE_DIR=str(root / 'pip-cache'), TMPDIR=str(root))
    proof = {'native_receipt': False, 'port': port, 'commands': [], 'members': [],
             'native_effects_complete': False}

    def save():
        write_json(root / 'proof.json', proof)

    def run(argv, payload=None, *, native=False):
        selected = dict(env, CODEX_THREAD_ID='f06-installed-' + port) if native else env
        cp = subprocess.run(argv, input=None if payload is None else json.dumps(payload).encode(),
                            capture_output=True, cwd=root, env=selected, check=False)
        proof['commands'].append({'argv': argv, 'cwd': str(root), 'exit_code': cp.returncode,
                                  'stdout': cp.stdout.decode(), 'stderr': cp.stderr.decode()})
        save()
        print(cp.stdout.decode(), end=''); print(cp.stderr.decode(), end='', file=sys.stderr)
        assert cp.returncode == 0, proof['commands'][-1]
        return cp.stdout

    wheels = root / 'wheels'; wheels.mkdir()
    run([sys.executable, '-B', '-m', 'pip', 'wheel', '--no-deps', '--no-build-isolation',
         '--no-index', '--wheel-dir', str(wheels), str(work)])
    wheel, = wheels.glob('*.whl')
    shutil.copy2(wheel, root / 'package.whl')
    venv = root / 'venv'
    run([sys.executable, '-B', '-m', 'venv', '--without-pip', str(venv)])
    python = str(venv / 'bin/python')
    run([sys.executable, '-B', '-m', 'pip', '--python', python, 'install', '--no-deps',
         '--no-index', str(wheel)])
    provenance = json.loads(run([python, '-I', '-B', '-c',
        'import json,sys,poise,importlib.metadata as m;d=m.distribution("ai-poise");'
        'print(json.dumps({"prefix":sys.prefix,"module":poise.__file__,"version":d.version,'
        '"direct_url":d.read_text("direct_url.json")}))']))
    assert Path(provenance['module']).is_relative_to(venv)
    assert not Path(provenance['module']).is_relative_to(SOURCE)
    assert not json.loads(provenance['direct_url']).get('dir_info', {}).get('editable', False)
    assert provenance['version'] == tomllib.loads((work / 'pyproject.toml').read_text())['project']['version']
    module_root = Path(provenance['module']).parent.parent
    source_members = {str(path.relative_to(SOURCE / 'src')): path.read_bytes()
                      for package in ('poise', 'poise_environment')
                      for path in (SOURCE / 'src' / package).rglob('*')
                      if path.is_file() and '__pycache__' not in path.parts and path.suffix != '.pyc'}
    source_members.update({str(path.relative_to(SOURCE)): path.read_bytes()
                           for path in (SOURCE / 'apps/bootstrap').rglob('*')
                           if path.is_file() and '__pycache__' not in path.parts and path.suffix != '.pyc'})
    with zipfile.ZipFile(wheel) as archive:
        wheel_members = {name: archive.read(name) for name in archive.namelist()
                         if name.startswith(('poise/', 'poise_environment/', 'apps/bootstrap/'))
                         and not name.endswith('/')}
    missing = sorted(set(source_members) - set(wheel_members))
    unexpected = sorted(set(wheel_members) - set(source_members))
    for name in sorted(set(source_members) & set(wheel_members)):
        raw = source_members[name]
        installed = module_root / name
        assert wheel_members[name] == raw == installed.read_bytes()
        proof['members'].append({'path': name, 'sha256': hashlib.sha256(raw).hexdigest(),
                                 'size': len(raw)})
    proof['provenance'] = provenance
    proof['wheel_sha256'] = hashlib.sha256(wheel.read_bytes()).hexdigest()
    proof['wheel_file'] = str(root / 'package.whl')
    proof.update(source_member_count=len(source_members), wheel_member_count=len(wheel_members),
                 missing_members=missing, unexpected_members=unexpected,
                 membership_complete=not missing and not unexpected)
    save()
    if missing == fixture()['required_runtime_assets'] and unexpected == []:
        # Exact diagnosed contract gap only. All present members were independently
        # checked byte-for-byte before this failure. Its acceptance requires review.
        raise InstalledPackageIncomplete(json.dumps({'missing': missing, 'unexpected': []}, sort_keys=True))
    assert missing == [] and unexpected == [], (missing, unexpected)

    def sdk(action, **fields):
        return json.loads(run([python, '-I', '-B', str(DRIVER)], {'action': action, **fields}, native=True))

    actual = sdk('capability', definition=future_definition())
    if actual == {'status': 'rejected', 'reason': fixture()['native_missing_error']}:
        raise ContextFeatureUnavailable(actual['reason'])
    assert actual == {'status': 'supported'}
    # Only after proving the real installed capability arrange its native consumer.
    r = Recovery(project, tmp_path, monkeypatch).enable()
    r.event('SessionEnd')
    assert r.s.invoke({'operation': 'recover'})[1]['status'] == 'cleaned'
    settings = deepcopy(r.settings)
    settings.update(python=python, source_root=str(module_root))
    write_json(r.service.settings.path, settings)
    selected = {'settings': str(r.service.settings.path)}
    revision = sdk('revision', **selected)['revision']
    installed = sdk('install', **selected, request={'request_id': 'context-installed-' + port,
        'expected_revision': revision, 'definition': future_definition()})
    policy = deepcopy(r.s.data)
    policy['admission']['definition_path'] = installed['definition_path']
    policy['session']['registry_template'] = str(r.s.root / ('installed-context-' + port + '.sqlite'))
    write_json(r.s.policy, policy)

    def native(kind, **fields):
        value = {'session_id': 'f06-installed-' + port, 'cwd': str(r.s.checkout),
                 'hook_event_name': kind, **fields}
        if port == 'python':
            return sdk('event', **selected, definition_path=installed['definition_path'], event=value)
        return json.loads(run([python, '-I', '-B', '-m', 'poise', 'hook', '--settings',
            selected['settings'], '--definition', installed['definition_path']], value, native=True))

    def reader(request):
        if port == 'python':
            result = sdk('reader', config=str(r.s.config), checkout=str(r.s.checkout),
                         policy=str(r.s.policy), request=request)
            assert result['exit_code'] == 0 and result['stderr'] == ''
            return json.loads(result['stdout'])
        return json.loads(run([python, '-I', '-B', '-m', 'poise', 'content-read', '--config',
            str(r.s.config), '--checkout', str(r.s.checkout), '--policy', str(r.s.policy),
            '--format', 'json'], request, native=True))

    reader({'operation': 'prepare_template'})
    native('SessionStart', source='startup')
    prepared = reader({'operation': 'prepare'})
    target = Path(prepared['details']['path'])
    assert reader({'operation': 'advance_context', 'request_id': 'installed-seed',
                   'reason': 'manual'})['context']['generation'] == 1
    native('PreCompact', turn_id='installed-turn', trigger='manual')
    native('PostCompact', turn_id='installed-turn', trigger='manual')
    first = card(native('SessionStart', source='compact'))
    assert first['checkpoint']['state'] == 'restored'
    assert first['generation'] == {'before': 1, 'current': 2}
    assert card(native('SessionStart', source='resume'))['checkpoint']['state'] == 'used'
    assert reader({'operation': 'context'})['context']['generation'] == 2
    native('SessionEnd', reason='other')
    assert reader({'operation': 'recover'})['status'] == 'cleaned' and not target.exists()
    proof.update(native_effects_complete=True, source_member_count=len(source_members),
                 wheel_member_count=len(wheel_members), actual_card=first, live_host_delivery='not_observed')
    save()
