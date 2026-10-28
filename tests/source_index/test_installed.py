"""Real noneditable package, complete membership, CLI, Git and handler clients."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tomllib
import zipfile

from tests.conftest import git, write_json
from tests.hook_transport.architecture_support import Chain
from .helpers import FIXTURES, SOURCE, Scene
from .test_actual import assert_definition, assert_import


def test_noneditable_installed_cli_and_git_callback(project, tmp_path):
    chain = Chain(project, tmp_path / 'installed-chain', 'F12_INSTALLED_HANDLER')
    scene = Scene(project, tmp_path, real=True, general=chain.general); scene.require()
    root = Path(os.environ['POISE_RUN_OUTPUT_DIR']) / 'source-index-installed'
    root.mkdir()
    clean = {k: v for k, v in scene.environment.items() if k != 'PYTHONPATH'}
    clean.update(PIP_CACHE_DIR=str(root / 'pip-cache'), TMPDIR=str(root))
    proof = {'native_receipt': False, 'live_host_delivery': False, 'commands': [], 'members': []}

    def run(argv):
        cp = subprocess.run(argv, cwd=root, env=clean, text=True, capture_output=True)
        proof['commands'].append({'argv': argv, 'cwd': str(root), 'exit_code': cp.returncode,
                                  'stdout': cp.stdout, 'stderr': cp.stderr})
        write_json(root / 'proof.json', proof)
        assert cp.returncode == 0, proof['commands'][-1]
        return cp.stdout

    work = root / 'source'; work.mkdir()
    for directory in ('src', 'apps'):
        shutil.copytree(SOURCE / directory, work / directory,
                        ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    shutil.copy2(SOURCE / 'pyproject.toml', work / 'pyproject.toml')
    wheels = root / 'wheels'; wheels.mkdir()
    run([sys.executable, '-B', '-m', 'pip', 'wheel', '--no-deps', '--no-index',
         '--no-build-isolation', '--wheel-dir', str(wheels), str(work)])
    wheel, = wheels.glob('*.whl')
    shutil.copy2(wheel, root / 'package.whl')
    venv = root / 'venv'
    run([sys.executable, '-B', '-m', 'venv', '--without-pip', str(venv)])
    python = str(venv / 'bin/python')
    run([sys.executable, '-B', '-m', 'pip', '--python', python, 'install',
         '--no-deps', '--no-index', str(wheel)])
    origin = json.loads(run([python, '-I', '-B', '-c',
        'import json,sys,poise,importlib.metadata as m;d=m.distribution("ai-poise");'
        'print(json.dumps({"prefix":sys.prefix,"module":poise.__file__,"version":d.version,'
        '"direct_url":d.read_text("direct_url.json")}))']))
    assert Path(origin['module']).is_relative_to(venv)
    assert not Path(origin['module']).is_relative_to(SOURCE)
    assert not json.loads(origin['direct_url']).get('dir_info', {}).get('editable', False)
    assert origin['version'] == tomllib.loads((work / 'pyproject.toml').read_text())['project']['version']
    expected = {str(path.relative_to(SOURCE / 'src')): path.read_bytes()
                for package in ('poise', 'poise_environment')
                for path in (SOURCE / 'src' / package).rglob('*')
                if path.is_file() and '__pycache__' not in path.parts and path.suffix != '.pyc'}
    expected.update({str(path.relative_to(SOURCE)): path.read_bytes()
                     for path in (SOURCE / 'apps/bootstrap').rglob('*')
                     if path.is_file() and '__pycache__' not in path.parts and path.suffix != '.pyc'})
    installed_root = Path(origin['module']).parent.parent
    with zipfile.ZipFile(wheel) as archive:
        members = {name: archive.read(name) for name in archive.namelist()
                   if name.startswith(('poise/', 'poise_environment/', 'apps/bootstrap/'))
                   and not name.endswith('/')}
    assert sorted(members) == sorted(expected)
    for name, raw in sorted(expected.items()):
        assert members[name] == raw == (installed_root / name).read_bytes()
        proof['members'].append({'path': name, 'size': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()})
    proof['origin'] = origin
    proof['wheel_sha256'] = hashlib.sha256(wheel.read_bytes()).hexdigest()
    write_json(root / 'proof.json', proof)
    help_output = run([python, '-I', '-B', '-m', 'poise', 'source-index', '--help'])
    assert '--config' in help_output and '--checkout' in help_output and '--policy' in help_output

    # Materialize the actual shipped example as explicit operator configuration.
    example = json.loads((SOURCE / 'config/examples/source-index-policy.example.json').read_text())
    assert example['schema'] == 'poise-source-index-policy-1'
    example.update(enabled=True, command_argv=[str(scene.external)])
    example['paths'] = scene.policy_data['paths']
    assert 'refs' in example['query_commands']
    scene.policy_data = example; scene.save()
    scene.python = python; scene.isolated = True; scene.environment = clean
    scene.ready()
    callback = scene.parent / 'installed-post-commit.sh'
    callback.write_bytes((SOURCE / 'config/examples/source-index-post-commit.example.sh').read_bytes())
    # Shipped callback contract: Bash script receives installed Python/profile/policy.
    scene.hooks(python=python, example=callback)
    code, value = scene.invoke('query', ['search', 'net_total', '--format', 'json'])
    assert (code, value['status']) == (0, 'complete')
    assert_definition(scene.output(value), 'net_total')
    (scene.checkout / 'src/delivery.py').write_bytes((FIXTURES / 'delivery.py').read_bytes())
    git(scene.checkout, 'add', 'src/delivery.py')
    run(['git', '-C', str(scene.checkout), 'commit', '-m', 'Installed callback indexes shipping'])
    head = git(scene.checkout, 'rev-parse', 'HEAD')
    trace = [json.loads(line) for line in scene.hook_trace.read_text().splitlines()]
    row = next(row for row in trace if row['position'] == 'index' and row['head'] == head)
    assert python in row['argv'] and row['exit_code'] == 0
    assert row['complete_captures']
    code, value = scene.invoke('query', ['refs', 'net_total', '--format', 'json'])
    assert (code, value['status']) == (0, 'complete')
    assert_import(scene.output(value), 'net_total')

    example_handler = json.loads((SOURCE / 'config/examples/source-index-handler.example.json').read_text())
    handler = example_handler['handlers'][0]
    assert handler['mode'] == 'sync' and handler['implementation']['kind'] == 'command'
    assert 'source-index' in handler['implementation']['argv']
    assert '--handler' in handler['implementation']['argv']
    handler['implementation']['argv'] = [*scene.argv(), '--handler']
    path = chain.configuration('installed-optional-index', [chain.command('before'), handler, chain.command('after')])
    chain.install([path])
    try:
        result = chain.invoke()
        packets = chain.assert_order(['before', 'after'])
        assert result['systemMessage'] == 'seed|before|after'
        assert packets[1]['response']['hookSpecificOutput']['hookEventName'] == 'SessionStart'
        assert 'source-index' in packets[1]['response']['hookSpecificOutput']['additionalContext']
    finally:
        chain.finish()
    scene.policy_data['enabled'] = False; scene.save()
    code, value = scene.invoke('update')
    assert (code, value['status']) == (0, 'disabled')
    proof['cli_calls'] = scene.calls
    proof['actual_git_callback'] = row
    proof['all_required_examples_executed'] = True
    write_json(root / 'proof.json', proof)
