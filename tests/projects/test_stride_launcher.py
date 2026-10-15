"""External launcher contract; no production imports determine expected values."""
from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests.projects.helpers import setup_case

ROOT = Path(__file__).resolve().parents[2]
LAUNCHER = ROOT / 'stride.sh'


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
    return path


def arrange_manifest(path, config, workflow_source_root):
    """Place each referenced process relative to its actual manifest."""
    for relative_path in config['workflows'].values():
        target = path.parent / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(workflow_source_root / relative_path, target)
    return write(path, config)


@pytest.fixture(scope='module')
def installed(tmp_path_factory):
    """Build copied candidate sources; install a wheel, never editable or PYTHONPATH."""
    root = tmp_path_factory.mktemp('installed stride')
    source = root / 'source'
    source.mkdir()
    shutil.copytree(ROOT / 'src', source / 'src')
    metadata = (ROOT / 'pyproject.toml').read_text()
    (source / 'pyproject.toml').write_text(
        re.sub(r'(?m)^version = .*$', 'version = \"7.1.0\"', metadata))
    wheels = root / 'wheels'
    subprocess.run([sys.executable, '-m', 'pip', 'wheel', '--no-build-isolation',
                    '--no-deps', '--wheel-dir', str(wheels), str(source)],
                   check=True, capture_output=True)
    environment = root / 'environment'
    subprocess.run([sys.executable, '-m', 'venv', str(environment)],
                   check=True, capture_output=True)
    python = environment / 'bin/python'
    wheel, = wheels.glob('*.whl')
    subprocess.run([str(python), '-m', 'pip', 'install', '--no-index', '--no-deps',
                    str(wheel)], check=True, capture_output=True)
    return python


@pytest.fixture
def case(project, installed, tmp_path):
    setup, _, _ = setup_case(project)
    tools = project['root']
    data = tmp_path / 'данные с пробелами'
    data.mkdir()
    config = dict(project['cfg'])
    config['paths'] = dict(config['paths'], state=str(data / 'demo/state'))
    manifest = arrange_manifest(tools / 'selected/project.json', config, tools)
    registry = tools / 'state/project-registry.json'
    write(registry, {'schema': 'configured-project-registry-1',
                     'projects': {'demo': {'config_path': 'selected/project.json'}}})
    probe = tmp_path / 'child probe.py'
    shutil.copy2(Path(__file__).with_name('fixtures') / 'stride' / 'probe.py', probe)
    settings = {
        'schema': 'stride-launcher-1',
        'poise_root': str(tools),
        'data_root': str(data),
        'project_id': 'demo',
        'project_setup': str(setup.relative_to(tools)),
        'command': [str(installed), str(probe)],
        'overrides': {},
        'exit_codes': {'rejected': 2},
    }
    settings_path = write(tmp_path / 'launcher settings.json', settings)
    bootstrap = tmp_path / 'launcher.env'
    bootstrap.write_text(
        'STRIDE_PYTHON=' + shlex.quote(str(installed)) + '\n'
        'STRIDE_CONFIG=' + shlex.quote(str(settings_path)) + '\n', encoding='utf-8')
    cwd = tmp_path / 'unrelated working directory'
    cwd.mkdir()
    existing = data / 'existing.txt'
    existing.write_bytes(b'keep existing user data\x00\xff')
    return dict(settings=settings, settings_path=settings_path, bootstrap=bootstrap,
                manifest=manifest, registry=registry, data=data, tools=tools, cwd=cwd,
                installed=installed, existing=existing)


def invoke(case, *args, stdin='', inherited_environment=None):
    # Missing externally required entry point is an explicit capability assertion,
    # rather than a Python import/collection/environment failure.
    assert LAUNCHER.is_file(), 'Versioned Stride operation entry point is absent'
    env = {key: value for key, value in os.environ.items()
           if key not in {'PYTHONPATH', 'PYTHONHOME', 'POISE_CONFIG',
                          'STRIDE_CONFIG', 'STRIDE_PYTHON'}}
    env['PYTHONNOUSERSITE'] = '1'
    if inherited_environment is not None:
        env.update(inherited_environment)
    return subprocess.run(['bash', str(LAUNCHER), str(case['bootstrap']), *args],
                          cwd=case['cwd'], env=env, input=stdin,
                          capture_output=True, text=True)


def read_probe(result):
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def snapshot(root):
    return {str(path.relative_to(root)): path.read_bytes()
            for path in root.rglob('*') if path.is_file()}


def rejected(case, *args, reason):
    before = snapshot(case['data'])
    result = invoke(case, *args)
    assert result.returncode == 2
    assert result.stderr.strip()
    assert reason.casefold() in result.stderr.casefold(), result.stderr
    assert result.stdout == ''
    assert snapshot(case['data']) == before


def test_registry_selects_manifest_and_rereads_changed_address(case):
    first = read_probe(invoke(case, 'read'))
    assert first['config'] == str(case['manifest'])
    moved = case['tools'] / 'changed/project.json'
    arrange_manifest(moved, json.loads(case['manifest'].read_text()),
                     case['manifest'].parent)
    write(case['registry'], {'schema': 'configured-project-registry-1',
                            'projects': {'demo': {'config_path': 'changed/project.json'}}})
    assert read_probe(invoke(case, 'read'))['config'] == str(moved)


def test_exact_project_selection(case):
    other = json.loads(case['manifest'].read_text())
    other['project'] = 'second'
    other_path = arrange_manifest(case['tools'] / 'second/project.json', other,
                                  case['manifest'].parent)
    write(case['registry'], {'schema': 'configured-project-registry-1', 'projects': {
        'demo': {'config_path': 'selected/project.json'},
        'second': {'config_path': 'second/project.json'}}})
    case['settings']['project_id'] = 'second'
    write(case['settings_path'], case['settings'])
    assert read_probe(invoke(case, 'read'))['config'] == str(other_path)


def test_roots_are_independent_and_data_placement_is_enforced(case):
    assert read_probe(invoke(case, 'read'))['config'] == str(case['manifest'])
    case['settings']['data_root'] = str(case['cwd'])
    write(case['settings_path'], case['settings'])
    rejected(case, 'read', reason='data_root')
    assert case['settings']['poise_root'] == str(case['tools'])


def test_tool_root_changes_without_changing_data_root(case):
    new_tools = case['cwd'] / 'other tools'
    shutil.copytree(case['tools'], new_tools)
    case['settings']['poise_root'] = str(new_tools)
    write(case['settings_path'], case['settings'])
    assert read_probe(invoke(case, 'read'))['config'] == str(new_tools / 'selected/project.json')
    assert case['settings']['data_root'] == str(case['data'])


def test_transport_preserves_arguments_cwd_and_stdin(case):
    args = ('', 'two words', 'Юникод', '$(touch forbidden)', ';exit 9', '--literal')
    observed = read_probe(invoke(case, *args, stdin='line one\nстрока два\n'))
    assert observed['args'] == list(args)
    assert observed['cwd'] == str(case['cwd'])
    assert observed['config'] == str(case['manifest'])
    assert observed['stdin'] == 'line one\nстрока два\n'
    assert set(observed) == {'args', 'cwd', 'config', 'stdin'}
    assert not (case['cwd'] / 'forbidden').exists()


def test_transport_preserves_stdout_stderr_and_nonzero_exit(case):
    result = invoke(case, 'streams')
    assert result.stdout == 'child stdout\n'
    assert result.stderr == 'child stderr\n'
    assert result.returncode == 17


@pytest.mark.parametrize('field', ['poise_root', 'data_root', 'project_id',
                                   'project_setup', 'command', 'overrides', 'exit_codes'])
def test_missing_required_settings_preserve_data(case, field):
    del case['settings'][field]
    write(case['settings_path'], case['settings'])
    rejected(case, 'read', reason=field)


@pytest.mark.parametrize('mutation', ['unknown-project', 'missing-manifest',
                                     'bad-manifest', 'wrong-identity',
                                     'missing-registry', 'bad-registry'])
def test_registry_failures_preserve_data(case, mutation):
    if mutation == 'unknown-project':
        case['settings']['project_id'] = 'unknown'
        write(case['settings_path'], case['settings'])
    elif mutation == 'missing-manifest':
        case['manifest'].unlink()
    elif mutation == 'bad-manifest':
        case['manifest'].write_text('{broken')
    elif mutation == 'wrong-identity':
        cfg = json.loads(case['manifest'].read_text())
        cfg['project'] = 'unexpected'
        write(case['manifest'], cfg)
    elif mutation == 'missing-registry':
        case['registry'].unlink()
    else:
        case['registry'].write_text('{broken')
    reason = {'unknown-project': 'unknown', 'missing-manifest': 'project.json',
              'bad-manifest': 'project.json', 'wrong-identity': 'identity',
              'missing-registry': 'project-registry.json',
              'bad-registry': 'project-registry.json'}[mutation]
    rejected(case, 'read', reason=reason)


@pytest.mark.parametrize('value', ['', None, [], 123])
def test_invalid_project_identity_preserves_data(case, value):
    case['settings']['project_id'] = value
    write(case['settings_path'], case['settings'])
    rejected(case, 'read', reason='project_id')


def test_explicit_override_replaces_prefix_without_shell_interpretation(case):
    case['settings']['overrides'] = {'check': [str(case['installed']), '-c',
                                              'import sys;print(sys.argv[1:])']}
    write(case['settings_path'], case['settings'])
    result = invoke(case, 'check', 'two words')
    assert result.returncode == 0
    assert result.stdout == "['two words']\n"
    assert result.stderr == ''


def test_installed_package_runs_real_readonly_project_operation(case):
    case['settings']['command'] = [str(case['installed']), '-B', '-m', 'poise']
    write(case['settings_path'], case['settings'])
    before = snapshot(case['data'])
    result = invoke(case, 'project', 'list', '--settings', str(case['tools'] / 'setup.json'))
    assert result.returncode == 0, result.stderr
    observed = json.loads(result.stdout)
    assert observed['projects'] == [{'project': 'demo', 'config_path': str(case['manifest'])}]
    assert snapshot(case['data']) == before


@pytest.mark.parametrize('mutation', ['missing-settings', 'bad-settings', 'schema',
                                     'empty-command', 'relative-root', 'missing-tools'])
def test_unusable_launcher_settings_preserve_data(case, mutation):
    if mutation == 'missing-settings':
        case['settings_path'].unlink()
    elif mutation == 'bad-settings':
        case['settings_path'].write_text('{broken')
    else:
        key, value = {
            'schema': ('schema', 'unsupported'),
            'empty-command': ('command', []),
            'relative-root': ('poise_root', 'relative'),
            'missing-tools': ('poise_root', str(case['cwd'] / 'absent')),
        }[mutation]
        case['settings'][key] = value
        write(case['settings_path'], case['settings'])
    reason = {'missing-settings': 'launcher settings.json',
              'bad-settings': 'launcher settings.json', 'schema': 'schema',
              'empty-command': 'command', 'relative-root': 'poise_root',
              'missing-tools': 'poise_root'}[mutation]
    rejected(case, 'read', reason=reason)


def test_unknown_configuration_options_do_not_change_selected_operation(case):
    case['settings']['future_option'] = {'preserve': True}
    write(case['settings_path'], case['settings'])
    assert read_probe(invoke(case, 'read'))['args'] == ['read']
    assert json.loads(case['settings_path'].read_text())['future_option'] == {'preserve': True}


def test_missing_interpreter_fails_without_changing_data(case):
    case['bootstrap'].write_text(
        'STRIDE_PYTHON=' + shlex.quote(str(case['cwd'] / 'absent-python')) + '\n'
        'STRIDE_CONFIG=' + shlex.quote(str(case['settings_path'])) + '\n')
    before = snapshot(case['data'])
    result = invoke(case, 'read')
    assert result.returncode != 0
    assert result.stderr.strip()
    assert snapshot(case['data']) == before


def test_exec_preserves_child_signal_termination(case):
    result = invoke(case, 'terminate')
    assert result.returncode == -15
    assert result.stdout == ''
    assert result.stderr == ''


def test_selected_package_stays_pinned_until_explicit_installation(case, tmp_path):
    assert invoke(case, 'package-version').stdout == '7.1.0\n'
    source = case['installed'].parents[2] / 'source'
    candidate = tmp_path / 'new package candidate'
    shutil.copytree(source, candidate)
    metadata = candidate / 'pyproject.toml'
    metadata.write_text(metadata.read_text().replace('version = "7.1.0"', 'version = "7.2.0"'))
    wheels = tmp_path / 'new wheels'
    subprocess.run([sys.executable, '-m', 'pip', 'wheel', '--no-build-isolation',
                    '--no-deps', '--wheel-dir', str(wheels), str(candidate)],
                   check=True, capture_output=True)
    # A newer available artifact has no effect on an explicitly installed choice.
    assert invoke(case, 'package-version').stdout == '7.1.0\n'
    environment = tmp_path / 'separate consumer'
    subprocess.run([sys.executable, '-m', 'venv', str(environment)],
                   check=True, capture_output=True)
    python = environment / 'bin/python'
    wheel, = wheels.glob('*.whl')
    subprocess.run([str(python), '-m', 'pip', 'install', '--no-index', '--no-deps',
                    str(wheel)], check=True, capture_output=True)
    case['settings']['command'][0] = str(python)
    write(case['settings_path'], case['settings'])
    case['bootstrap'].write_text('STRIDE_PYTHON=' + shlex.quote(str(python)) + '\n'
                                'STRIDE_CONFIG=' + shlex.quote(str(case['settings_path'])) + '\n')
    result = invoke(case, 'package-version')
    assert result.returncode == 0, result.stderr
    assert result.stdout == '7.2.0\n'


def test_database_outside_selected_data_root_is_refused(case):
    cfg = json.loads(case['manifest'].read_text())
    cfg['paths']['database'] = str(case['cwd'] / 'foreign.sqlite')
    write(case['manifest'], cfg)
    rejected(case, 'read', reason='database')
    assert not (case['cwd'] / 'foreign.sqlite').exists()


@pytest.mark.parametrize('missing', ['STRIDE_PYTHON', 'STRIDE_CONFIG'])
def test_bootstrap_requires_both_explicit_values(case, missing):
    values = {'STRIDE_CONFIG': str(case['settings_path']),
              'STRIDE_PYTHON': str(case['installed'])}
    case['bootstrap'].write_text(''.join(key + '=' + shlex.quote(value) + '\n'
                                         for key, value in values.items() if key != missing))
    before = snapshot(case['data'])
    result = invoke(case, 'read')
    assert result.returncode != 0
    assert missing in result.stderr
    assert snapshot(case['data']) == before


def test_missing_installed_library_is_not_replaced_with_worktree_sources(case, tmp_path):
    environment = tmp_path / 'empty interpreter'
    subprocess.run([sys.executable, '-m', 'venv', '--without-pip', str(environment)],
                   check=True, capture_output=True)
    case['bootstrap'].write_text(
        'STRIDE_PYTHON=' + shlex.quote(str(environment / 'bin/python')) + '\n'
        'STRIDE_CONFIG=' + shlex.quote(str(case['settings_path'])) + '\n')
    before = snapshot(case['data'])
    result = invoke(case, 'read')
    assert result.returncode != 0
    assert result.stderr.strip()
    assert snapshot(case['data']) == before


def test_data_root_changes_successfully_with_unchanged_tool_root(case):
    import sqlite3

    first = read_probe(invoke(case, 'storage'))
    assert first['state'] == str(case['data'] / 'demo/state')
    first_data = snapshot(case['data'])
    second = case['cwd'] / 'second data placement'
    second.mkdir()
    cfg = json.loads(case['manifest'].read_text())
    cfg['paths']['state'] = str(second / 'demo/state')
    write(case['manifest'], cfg)
    case['settings']['data_root'] = str(second)
    write(case['settings_path'], case['settings'])
    observed = read_probe(invoke(case, 'storage'))
    assert observed['config'] == str(case['tools'] / 'selected/project.json')
    assert observed['state'] == str(second / 'demo/state')
    database = second / 'demo/state/state.sqlite'
    assert observed['database'] == str(database)
    with sqlite3.connect(database) as connection:
        assert connection.execute('SELECT value FROM probe').fetchall() == [('selected operation',)]
    assert snapshot(case['data']) == first_data


INVALID_SETTINGS = json.loads((Path(__file__).with_name('fixtures') / 'stride'
                               / 'invalid-settings.json').read_text())


@pytest.mark.parametrize('entry', INVALID_SETTINGS, ids=lambda entry: entry['id'])
def test_invalid_required_values_explain_the_affected_setting(case, entry):
    case['settings'][entry['field']] = entry['value']
    write(case['settings_path'], case['settings'])
    rejected(case, 'read', reason=entry['field'])


@pytest.mark.parametrize('mutation', ['missing', 'malformed', 'invalid'])
def test_unusable_project_setup_explains_its_location(case, mutation):
    setup = case['tools'] / 'setup.json'
    if mutation == 'missing':
        setup.unlink()
    elif mutation == 'malformed':
        setup.write_text('{broken')
    else:
        write(setup, {'schema': 'unsupported'})
    rejected(case, 'read', reason='setup.json')


@pytest.mark.parametrize('route', ['check', 'deps', 'infra', 'next'])
def test_every_configured_route_uses_current_prefix(case, route):
    prefix = case['settings']['command']
    case['settings']['overrides'] = {route: [*prefix, 'first-prefix']}
    write(case['settings_path'], case['settings'])
    first = read_probe(invoke(case, route, 'two words'))
    assert first['args'] == ['first-prefix', 'two words']
    assert first['config'] == str(case['manifest'])
    case['settings']['overrides'][route] = [*prefix, 'second-prefix']
    write(case['settings_path'], case['settings'])
    second = read_probe(invoke(case, route, 'two words'))
    assert second['args'] == ['second-prefix', 'two words']
    assert second['config'] == str(case['manifest'])


def test_default_command_prefix_is_reread_on_next_operation(case):
    case['settings']['command'].append('first-prefix')
    write(case['settings_path'], case['settings'])
    assert read_probe(invoke(case, 'read'))['args'] == ['first-prefix', 'read']
    case['settings']['command'][-1] = 'second-prefix'
    write(case['settings_path'], case['settings'])
    assert read_probe(invoke(case, 'read'))['args'] == ['second-prefix', 'read']


def shadow_package(root):
    package = root / 'poise'
    package.mkdir(parents=True)
    (package / '__init__.py').write_text('')
    (package / 'stride.py').write_text("print('UNSELECTED_TEST_PACKAGE')\n")


def test_installed_launcher_ignores_package_in_caller_cwd(case):
    shadow_package(case['cwd'])
    before = snapshot(case['data'])
    result = invoke(case, 'read', 'cwd isolation', stdin='caller input\n')
    observed = read_probe(result)
    assert observed['config'] == str(case['manifest'])
    assert observed['args'] == ['read', 'cwd isolation']
    assert observed['cwd'] == str(case['cwd'])
    assert observed['stdin'] == 'caller input\n'
    assert 'UNSELECTED_TEST_PACKAGE' not in result.stdout + result.stderr
    assert snapshot(case['data']) == before


def test_installed_launcher_ignores_inherited_pythonpath_package(case, tmp_path):
    shadow = tmp_path / 'unselected source package'
    shadow_package(shadow)
    before = snapshot(case['data'])
    result = invoke(case, 'read', 'environment isolation', stdin='caller input\n',
                    inherited_environment={'PYTHONPATH': str(shadow)})
    observed = read_probe(result)
    assert observed['config'] == str(case['manifest'])
    assert observed['args'] == ['read', 'environment isolation']
    assert observed['cwd'] == str(case['cwd'])
    assert observed['stdin'] == 'caller input\n'
    assert 'UNSELECTED_TEST_PACKAGE' not in result.stdout + result.stderr
    assert snapshot(case['data']) == before
