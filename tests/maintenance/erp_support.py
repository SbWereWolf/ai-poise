"""Transparent test arrangements for the public ERP profile/actions boundary.

This is test infrastructure, not another maintenance engine or production oracle.
The independent contract is literal fixture data. P002 owns the supply fixture.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
from string import Template
import subprocess
import sys

import pytest
from test_catalog_v2_profile import source  # Reuse the existing test-owned input.

REPO = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).parent / 'fixtures'
CONTRACT = json.loads((FIXTURES / 'erp-contract.json').read_text())
IDS = [identifier for group in CONTRACT['groups'].values() for identifier in group]
SPEC = Path('config/projects/erp/profile.example.json')


def invoke(argv, *, cwd=REPO, env=None, text=True, input=None):
    return subprocess.run(list(map(str, argv)), cwd=cwd,
                          env=dict(os.environ, PYTHONPATH=str(REPO / 'src'),
                                   PYTHONDONTWRITEBYTECODE='1', LANG='C.UTF-8') if env is None else env,
                          capture_output=True, text=text, input=input)


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n',
                    encoding='utf-8')


def require_input(name):
    value = os.environ.get(name)
    assert value, f'Select explicit test input {name}; absence is setup failure, not RED'
    return value


def targets(case):
    return [{**item, 'path': case['repository'] / item['relative']}
            for item in CONTRACT['configuration_targets']]


@pytest.fixture
def erp_case(source, tmp_path):
    repository = tmp_path / 'ERP repo Ω'
    repository.mkdir()
    for args in (('init', '-b', 'main'),
                 ('-c', 'user.name=ERP Acceptance', '-c',
                  'user.email=erp@example.invalid', 'commit', '--allow-empty',
                  '-m', 'Test-owned ERP input')):
        result = invoke(['git', '-C', repository, *args])
        assert result.returncode == 0, (result.stdout, result.stderr)
    return {'root': tmp_path, 'supply': source, 'repository': repository,
            'profile': tmp_path / 'Poise profile Ω',
            'state': tmp_path / 'separate-poise-state'}


def selected_parameters(case):
    selected = {
        'php_executable': require_input('P003_PHP_EXECUTABLE'),
        'node_executable': require_input('P003_NODE_EXECUTABLE'),
        'composer_executable': require_input('P003_COMPOSER_EXECUTABLE'),
        'npm_entry_point': require_input('P003_NPM_ENTRY_POINT'),
        'environment_file': str(case['repository'] /
                                'platform-infrastructure/config/dev/local-development.env'),
    }
    for target in targets(case):
        selected[target['parameter']] = str(target['path'])
        selected[target['source_parameter']] = str(
            case['profile'] / 'documents' / (target['id'] + '.json'))
    return selected


def materialize(case, *, project='erp-acceptance', overrides=None):
    """Configure a disposable selected supply, never the product/working config."""
    specification = case['supply'] / SPEC
    if specification.is_file():
        declared = read_json(specification)
        catalog_path = case['supply'] / declared['catalog']
        catalog = read_json(catalog_path)
        inputs = selected_parameters(case)
        inputs.update(overrides or {})
        for name, value in inputs.items():
            assert name in catalog['parameters'], f'ERP public parameter is missing: {name}'
            catalog['parameters'][name]['default'] = value
        write_json(catalog_path, catalog)
    return invoke([sys.executable, '-B', '-m', 'poise_environment.profile',
                   '--source', case['supply'], '--profile-spec', specification,
                   '--output', case['profile'], '--state', case['state'],
                   '--repository', case['repository'], '--project', project,
                   '--base-ref', 'main', '--author-name', 'ERP Acceptance',
                   '--author-email', 'erp@example.invalid', '--module-path', REPO / 'src'])


@pytest.fixture
def erp_deployed(erp_case):
    result = materialize(erp_case)
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert (erp_case['profile'] / 'catalog.json').is_file(), 'ERP profile is mandatory'
    return erp_case  # No dependent skips can turn a future GREEN into success.


def values(case, overrides=None):
    catalog = read_json(case['profile'] / 'catalog.json')
    effective = {name: item['default'] for name, item in catalog['parameters'].items()
                 if 'default' in item}
    effective.update(read_json(case['profile'] / 'values.json'))
    effective.update(overrides or {})
    return effective


def action_argv(case, identifier, role, overrides=None):
    catalog = read_json(case['profile'] / 'catalog.json')
    row = next(row for row in catalog['requirements'] if row['id'] == identifier)
    item = row[role]
    effective = values(case, overrides)
    tokens = {name: value if isinstance(value, str) else json.dumps(value)
              for name, value in effective.items()}
    argv = [Template(value).substitute(tokens) for value in item['argv']]
    cwd = Path(Template(item['cwd']).substitute(tokens))
    if not cwd.is_absolute():
        cwd = case['profile'] / cwd
    env = {name: os.environ[name] for name in catalog['inherit_env'] if name in os.environ}
    env.update({name: Template(value).substitute(tokens) for name, value in item['env'].items()})
    return argv, cwd, env


def action(case, identifier, role, overrides=None, wrapper=None):
    argv, cwd, env = action_argv(case, identifier, role, overrides)
    if wrapper:
        argv = [*wrapper, '--', *argv]
    result = invoke(argv, cwd=cwd, env=env)
    response = json.loads(result.stdout) if result.returncode == 0 else None
    if response is not None:
        expected = {'schema', 'requirement_id', 'action',
                    'state' if role == 'check' else 'outcome'}
        if role == 'check' and response['state'] == 'unavailable':
            expected |= {'cause', 'recommendations'}
        assert set(response) == expected, 'External action stdout must keep its strict contract'
        assert response['requirement_id'] == identifier and response['action'] == role
    return result, response


def consumer(case, operation='check', overrides=None):
    python = require_input('P003_CONSUMER_PYTHON')
    selected = case['root'] / 'selected-values.json'
    write_json(selected, values(case, overrides))
    result = invoke([python, '-I', '-B', '-m', 'environment_maintenance', operation,
                     '--catalog-dir', case['profile'], '--values', selected])
    report = json.loads(result.stdout)
    assert report['schema'] == 'environment-maintenance/result/v2'
    assert result.returncode == report['exit_code']
    return result, report


def bootstrap_configuration(case):
    configured = read_json(case['profile'] / 'bootstrap.json')
    configured['parameters']['python'] = require_input('P003_CONSUMER_PYTHON')
    configured['parameters']['tool_config'] = str(case['profile'] / 'values.json')
    configured['handler']['options']['configuration_targets'] = [
        {'id': t['id'], 'kind': t['kind'], 'path': str(t['path'])} for t in targets(case)]
    return configured


def bootstrap(case, operation='check', configured=None, catalog=None):
    path = case['root'] / 'bootstrap-working.json'
    write_json(path, bootstrap_configuration(case) if configured is None else configured)
    result = invoke([sys.executable, '-B', REPO / 'bootstrap', operation,
                     '--config', path, '--catalog-dir', catalog or case['profile']])
    return result, json.loads(result.stdout)


def observations(report):
    rows = report['configuration_observations']
    assert len(rows) == len({row['id'] for row in rows}), 'No ambiguous record selection'
    return {row['id']: row for row in rows}


def observe_exact(record, *, identifier, kind, content):
    assert record['id'] == identifier and record['kind'] == kind
    assert record['state'] == 'present'
    assert record['sha256'] == hashlib.sha256(content).hexdigest()
    assert record['cause'] == ''


def snapshot(root):
    return {str(p.relative_to(root)): p.read_bytes()
            for p in root.rglob('*') if p.is_file()}


def arrange_target_bytes(case, content=None):
    content = (FIXTURES / 'erp-json-new.json').read_bytes() if content is None else content
    for target in targets(case):
        target['path'].parent.mkdir(parents=True, exist_ok=True)
        target['path'].write_bytes(content)
    return content


def source_override(case, target):
    desired = case['root'] / (target['id'] + '-desired.json')
    desired.write_bytes((FIXTURES / 'erp-json-new.json').read_bytes())
    return {target['source_parameter']: str(desired)}


def wire_configuration(case, *, reply=None, stderr=None, exit_code=0):
    configured = bootstrap_configuration(case)
    command = [sys.executable, '-B', str(FIXTURES / 'erp_process_boundary.py'),
               '--reply-file', str(reply or FIXTURES / 'erp-ready-result.json'),
               '--exit-code', str(exit_code)]
    if stderr is not None:
        command += ['--stderr-file', str(stderr)]
    configured['commands']['check'] = command
    return configured


def selected_catalog(case, identifier, *, role=None, prefix=None):
    """Test input selection, not an imitation of the external engine."""
    catalog = read_json(case['profile'] / 'catalog.json')
    selected = next(row for row in catalog['requirements'] if row['id'] == identifier)
    if prefix:
        selected[role]['argv'] = [*map(str, prefix), '--', *selected[role]['argv']]
    catalog['requirements'] = [selected]
    directory = case['root'] / 'selected-catalog'
    write_json(directory / 'catalog.json', catalog)
    return directory


def publish_configuration_inputs(case):
    for target in targets(case):
        result, response = action(case, 'erp-' + target['id'], 'install')
        assert result.returncode == 0 and response['outcome'] == 'completed', (
            target['id'], result.stdout, result.stderr)


def export_erp_source(case):
    """Pinned tracked sources only: never working vendor/.env/WIP or credentials."""
    source = require_input('P003_ERP_SOURCE')
    reference = require_input('P003_ERP_REFERENCE')
    assert reference == CONTRACT['reference'], 'Select the independently reviewed ERP input'
    selected = ['planning-workspace', 'calculation-service', 'system-admin',
                'planning-workspace-core', 'calculation-core', 'erp-calendar-optimizer',
                'excel-input', 'document-ui', 'platform-infrastructure']
    inventory = invoke(['git', '-C', source, 'ls-tree', '-r', '--name-only', reference,
                        '--', *selected])
    assert inventory.returncode == 0, (inventory.stdout, inventory.stderr)
    # The pinned tree also tracks test specimens named vendor/.env. They are
    # unnecessary for this source closure; export only the exact allowed files.
    names = [name for name in inventory.stdout.splitlines()
             if Path(name).name != '.env' and '/vendor/' not in name
             and '/node_modules/' not in name]
    assert names, 'The explicitly selected source closure must be nonempty'
    archive = invoke(['git', '-C', source, 'archive', reference, '--', *names], text=False)
    assert archive.returncode == 0, archive.stderr
    exported = invoke(['tar', '-x', '-C', case['repository']], text=False, input=archive.stdout)
    assert exported.returncode == 0, exported.stderr
    case['reference'] = reference
    return case


def copy_executable_fixture(case, name):
    target = case['root'] / name
    shutil.copyfile(FIXTURES / name, target)
    target.chmod(0o755)
    return target


def cjm_consumer_inputs(case):
    """Pinned sanitized configuration inputs; never use a live ERP .env."""
    export_erp_source(case)
    reference = case['repository'] / 'platform-infrastructure/tests/playwright'
    inputs = reference / 'contracts/fixtures/cjm-configuration'
    root = case['root'] / 'CJM consumer Ω'
    (root / 'config').mkdir(parents=True)
    (root / 'planning-workspace').mkdir()
    (root / 'tests/cjm').mkdir(parents=True)
    for source, destination in [
        ('local-development.example.env', 'config/local-development.env'),
        ('planning-workspace.example.env', 'planning-workspace/.env'),
        ('test-execution.json', 'config/test-execution.json'),
    ]:
        shutil.copyfile(inputs / source, root / destination)
    relative = 'platform-infrastructure/tests/playwright/cjm.settings.json'
    selected = case['root'] / 'selected-cjm-source.json'
    shutil.copyfile(inputs / 'settings.json', selected)
    command = [require_input('P003_NODE_EXECUTABLE'), FIXTURES / 'erp_cjm_consumer.mjs',
               reference / 'support/cjm-configuration.mjs', root, relative]
    return root, relative, selected, command
