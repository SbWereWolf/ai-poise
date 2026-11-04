"""Actual built/installed dependency, two independent consumers, external cwd."""
from copy import deepcopy
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tomllib
import zipfile

import pytest

from tests.skills.instruction_delivery_helpers import Scenario, fixture as old_literal
from tests.skills.portable_delivery_support import (
    REPOSITORY, canonical, literal, read_request, replace_body, request,
)
from tests.skills.test_context_instruction_delivery import fill


def run(argv, cwd, packet=None):
    environment = dict(os.environ)
    environment.pop('PYTHONPATH', None)
    environment['PIP_NO_INDEX'] = '1'
    result = subprocess.run(
        argv, cwd=cwd, input=None if packet is None else json.dumps(packet),
        text=True, capture_output=True, env=environment, check=False,
    )
    print(json.dumps({'argv': argv, 'cwd': str(cwd), 'exit_code': result.returncode}))
    print(result.stdout, end='')
    print(result.stderr, end='', file=sys.stderr)
    return result


@pytest.fixture(scope='module')
def installed(tmp_path_factory):
    root = tmp_path_factory.mktemp('instruction-installed')
    build_source = root / 'build-source'
    build_source.mkdir()
    # Copy exact current package inputs to keep setuptools working outputs out
    # of the Task checkout. No built artifact or generated module is substituted.
    for name in ('src', 'apps/bootstrap'):
        shutil.copytree(REPOSITORY / name, build_source / name,
                        ignore=shutil.ignore_patterns('__pycache__', '*.egg-info'))
    shutil.copy2(REPOSITORY / 'pyproject.toml', build_source / 'pyproject.toml')
    source_hashes = {}
    for path in build_source.rglob('*'):
        if path.is_file():
            relative = path.relative_to(build_source)
            assert path.read_bytes() == (REPOSITORY / relative).read_bytes()
            source_hashes[str(relative)] = hashlib.sha256(path.read_bytes()).hexdigest()
    (root / 'build-inputs.json').write_text(json.dumps(source_hashes, sort_keys=True))
    wheels = root / 'wheels'
    build = run([sys.executable, '-I', '-m', 'pip', 'wheel', '--no-deps',
                 '--no-build-isolation', '--no-index', '--wheel-dir', str(wheels),
                 str(build_source)], root)
    assert build.returncode == 0, 'Package build is setup, not behavioral RED'
    wheel, = wheels.glob('*.whl')
    prefix = root / 'installed'
    installation = run([sys.executable, '-I', '-m', 'pip', 'install', '--no-deps',
                        '--no-index', '--target', str(prefix), str(wheel)], root)
    assert installation.returncode == 0, 'Installation failure is not behavioral RED'
    expected_version = tomllib.loads((REPOSITORY / 'pyproject.toml').read_text())['project']['version']
    dist, = [d for d in importlib.metadata.distributions(path=[str(prefix)])
             if d.metadata['Name'] == 'ai-poise']
    assert dist.version == expected_version
    with zipfile.ZipFile(wheel) as archive:
        metadata_name, = [n for n in archive.namelist() if n.endswith('.dist-info/METADATA')]
        assert ('Version: ' + expected_version + '\n') in archive.read(metadata_name).decode()
    print(json.dumps({'wheel': wheel.name, 'sha256': hashlib.sha256(wheel.read_bytes()).hexdigest(),
                      'version': dist.version, 'build_inputs': source_hashes}, sort_keys=True))
    unrelated = root / 'unrelated-cwd'
    unrelated.mkdir()
    return prefix, unrelated


def invoke(installed, packet, mode, scenario=None):
    prefix, cwd = installed
    origins = cwd / 'origins.json'
    probe = Path(__file__).with_name('installed_instruction_probe.py')
    argv = [sys.executable, '-I', '-B', str(probe), str(prefix), str(origins), mode]
    if scenario is not None:
        argv += ['--catalog', str(scenario.catalog), '--root', str(scenario.checkout)]
    result = run(argv, cwd, packet)
    # The API returns protocol rejections as values; CLI has explicit 0/1/2.
    assert result.returncode == 0, result.stderr
    assert result.stderr == ''
    modules = json.loads(origins.read_text())
    print(json.dumps({'installed_module_origins': modules}, sort_keys=True))
    assert modules and 'poise' in modules
    assert all(Path(p).is_relative_to(prefix) for p in modules.values()), modules
    return json.loads(result.stdout)


@pytest.mark.parametrize('name', ['alpha', 'beta'])
def test_two_file_configurations_use_installed_common_supply(installed, project, tmp_path, name):
    scene = Scenario(project, tmp_path, name)
    packet = fill(literal('file-bounded-request.json'), scene)
    expected = old_literal('billing-expected.json' if name == 'alpha' else 'beta-expected.json')
    first = invoke(installed, packet, 'cli', scene)
    assert first['schema'] == 'poise-portable-instruction-result-1'
    assert first['status'] == 'complete'
    assert first['delivery']['project'] == name
    assert first['delivery']['materials'] == expected
    assert first['observation']['materials'] == expected
    assert first['delivery']['remaining'] == first['delivery']['reused'] == []
    assert len(canonical(first['delivery'])) <= 65536
    repeated = deepcopy(packet)
    repeated['context']['reason'] = 'continue'
    repeated['previous'] = first['receipt']
    # These keys are independently enumerated by the test-owned material fixture.
    repeated['retained'] = [':'.join(m[k] for k in ('kind', 'id', 'root', 'path')) for m in expected]
    second = invoke(installed, repeated, 'cli', scene)
    assert second['delivery']['materials'] == []
    assert second['delivery']['reused'] == [{k: v for k, v in m.items() if k != 'content'} for m in expected]
    repeated['context']['reason'] = 'compaction'
    restored = invoke(installed, repeated, 'cli', scene)
    assert restored['delivery']['materials'] == expected
    assert restored['delivery']['reused'] == []


@pytest.mark.parametrize('mode', ['api', 'cli'])
def test_installed_normalized_public_entry_exact_contract(installed, mode):
    packet = request()
    first = invoke(installed, packet, mode)
    assert first['delivery'] == literal('complete-frame.json')
    assert first['receipt'] == literal('receipt.json')
    assert first['observation'] == literal('selection.json')
    packet['previous'] = first['receipt']
    packet['retained'] = literal('keys.json')
    packet['context']['reason'] = 'continue'
    repeated = invoke(installed, packet, mode)
    assert repeated['delivery']['materials'] == []
    packet['context']['reason'] = 'resume'
    resumed = invoke(installed, packet, mode)
    assert resumed['delivery']['materials'] == literal('complete-frame.json')['materials']
    assert resumed['delivery']['reused'] == []


@pytest.mark.parametrize('mode', ['api', 'cli'])
def test_installed_bounded_and_addressed_delivery(installed, mode):
    packet = request()
    replace_body(packet, 1, literal('read-body.json')['content'])
    packet['budget']['max_bytes'] = 5000
    first = invoke(installed, packet, mode)
    assert first['status'] == 'partial'
    assert first['delivery']['materials'] == [
        literal('complete-frame.json')['materials'][i] for i in (0, 2)
    ]
    remaining, = first['delivery']['remaining']
    assert remaining['key'] == literal('keys.json')[1]
    assert remaining['reason'] == 'budget'
    assert len(canonical(first['delivery'])) <= 5000
    read = read_request(packet, remaining['address'])
    full = invoke(installed, read, mode)
    assert full['delivery'] == literal('read-frame.json')
    assert full['observation'] == packet['selection']
    read['range'] = dict(unit='bytes', start=16, end=33)
    selected = invoke(installed, read, mode)
    expected = literal('read-frame.json')
    expected.update(range=read['range'], content='日本語 café\r\n', complete_material=False)
    assert selected['delivery'] == expected
