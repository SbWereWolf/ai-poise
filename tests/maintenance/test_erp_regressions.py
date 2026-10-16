"""R1/R2/R3 public regressions for independently confirmed P003 findings.

Only explicit test-owned arrangements and external public commands are used.
The old accepted tests/helpers are unchanged; setup failures never count as RED.
"""
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time

import pytest

pytest.register_assert_rewrite('erp_support')
from erp_support import (
    FIXTURES, REPO, SPEC, action, action_argv, export_erp_source, invoke,
    read_json, require_input, targets, write_json,
)

pytest_plugins = ['erp_support']


def direct_materialization(case, *, project='erp-acceptance',
                           author='ERP Acceptance', email='erp@example.invalid'):
    """Keep shipped path defaults untouched, unlike the older helper."""
    return invoke([
        sys.executable, '-B', '-m', 'poise_environment.profile',
        '--source', case['supply'], '--profile-spec', case['supply'] / SPEC,
        '--output', case['profile'], '--state', case['state'],
        '--repository', case['repository'], '--project', project,
        '--base-ref', 'main', '--author-name', author,
        '--author-email', email, '--module-path', REPO / 'src',
    ])


@pytest.mark.parametrize('literal', ['$cash', '$$pair'])
@pytest.mark.parametrize('parameter', [
    'repository', 'profile_root', 'state_root', 'project_id', 'author_name', 'author_email',
])
def test_public_profile_preserves_literal_selected_data(erp_case, parameter, literal):
    case = erp_case
    arguments = {}
    if parameter == 'repository':
        destination = case['root'] / ('ERP ' + literal)
        case['repository'].rename(destination)
        case['repository'] = destination
        expected = str(destination)
    elif parameter in ('profile_root', 'state_root'):
        key = 'profile' if parameter == 'profile_root' else 'state'
        case[key] = case['root'] / (key + ' ' + literal)
        expected = str(case[key])
    elif parameter == 'project_id':
        expected = 'erp-' + literal
        arguments['project'] = expected
    elif parameter == 'author_name':
        expected = 'Author ' + literal
        arguments['author'] = expected
    else:
        expected = literal + '@example.invalid'
        arguments['email'] = expected
    result = direct_materialization(case, **arguments)
    assert result.returncode == 0, ('Literal CLI data must remain literal', result.stdout, result.stderr)
    catalog = read_json(case['profile'] / 'catalog.json')
    assert catalog['parameters'][parameter]['default'] == expected, 'Literal data was reinterpreted'
    if parameter == 'repository':
        projected = read_json(case['profile'] / 'project-edits.json')
        assert any(edit['path'] == ['git', 'repository'] and edit['value'] == expected
                   for edit in projected), 'Authored substitution must preserve literal input'
    if parameter == 'project_id':
        projected = read_json(case['profile'] / 'project-edits.json')
        assert any(edit['path'] == ['project'] and edit['value'] == expected
                   for edit in projected), 'Authored project substitution must preserve literal input'


@pytest.mark.parametrize('literal', ['$cash', '$$pair'])
def test_public_profile_preserves_selected_literal_default(erp_case, literal):
    case = erp_case
    specification = read_json(case['supply'] / SPEC)
    path = case['supply'] / specification['catalog']
    catalog = read_json(path)
    expected = str(case['repository'] / (literal + '.env'))
    catalog['parameters']['environment_file']['default'] = expected
    write_json(path, catalog)
    result = direct_materialization(case)
    assert result.returncode == 0, ('Literal default must remain literal', result.stdout, result.stderr)
    assert read_json(case['profile'] / 'catalog.json')['parameters']['environment_file']['default'] == expected


@pytest.mark.parametrize('absolute_override', [False, True])
def test_profile_defaults_and_absolute_overrides_publish_selected_documents(erp_case, absolute_override):
    case = erp_case
    if absolute_override:
        specification = read_json(case['supply'] / SPEC)
        path = case['supply'] / specification['catalog']
        catalog = read_json(path)
        for target in targets(case):
            catalog['parameters'][target['parameter']]['default'] = str(target['path'])
            catalog['parameters'][target['source_parameter']]['default'] = str(
                case['profile'] / 'documents' / (target['id'] + '.json'))
        write_json(path, catalog)
    result = direct_materialization(case)
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert not case['state'].exists()
    for target in targets(case):
        expected = (case['profile'] / 'documents' / (target['id'] + '.json')).read_bytes()
        result, response = action(case, 'erp-' + target['id'], 'install')
        assert result.returncode == 0 and response['outcome'] == 'completed', (result.stdout, result.stderr)
        assert target['path'].read_bytes() == expected


def selected_process(case, directory, mode):
    """Replace only a test-owned selected resource, not the shipped source."""
    resource = read_json(case['profile'] / 'resources.json')
    selected = read_json(FIXTURES / 'erp_process_resource.json')
    selected['argv'][0] = sys.executable
    selected['argv'][2] = str(FIXTURES / 'erp_owned_process.py')
    selected['argv'][3] = mode
    selected['argv'][4] = str(directory)
    selected['cwd'] = str(directory)
    resource['resources']['erp-php-runtime'] = selected
    write_json(case['profile'] / 'resources.json', resource)
    return action_argv(case, 'erp-php-runtime', 'check')


def owned_process_state(pid):
    """Independent Linux process observation for the exact fixture-owned PID."""
    try:
        record = (Path('/proc') / str(pid) / 'stat').read_text()
    except (FileNotFoundError, ProcessLookupError):
        return 'terminated'
    # Linux proc(5): state follows the parenthesized command, which may contain spaces.
    return record[record.rfind(')') + 2:].split()[0]


def test_terminal_timeout_settles_started_owned_descendant(erp_deployed):
    case = erp_deployed
    directory = case['root'] / 'owned timeout processes'
    directory.mkdir()
    argv, cwd, env = selected_process(case, directory, 'parent')
    unrelated = subprocess.Popen([
        sys.executable, '-B', str(FIXTURES / 'erp_owned_process.py'), 'unrelated', str(directory),
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    try:
        result = subprocess.run(argv, cwd=cwd, env=env, capture_output=True, text=True, timeout=6)
        child_pid = int((directory / 'child-pid').read_text())
        terminal_state = owned_process_state(child_pid)
        terminated_at_return = terminal_state in ('terminated', 'Z', 'X')
        assert (directory / 'started').read_text() == 'owned descendant started', 'Fixture setup must prove startup'
        response = json.loads(result.stdout)
        assert response['state'] == 'unavailable' and response['cause'] == 'process', (result.stdout, result.stderr)
        assert 'owned descendant started' in result.stderr and 'owned parent diagnostic' in result.stderr
        assert unrelated.poll() is None, 'Unrelated process must survive owned timeout'
        (directory / 'release').touch()
        # Keep this same outer test host alive: its teardown cannot prove settlement.
        time.sleep(0.5)
        late_effect = (directory / 'effect').exists()
        assert terminated_at_return, (
            'Owned descendant remains live after terminal timeout',
            'late effect observed', late_effect)
        assert not (directory / 'effect').exists(), 'Timed-out owned descendant performed a late effect'
    finally:
        (directory / 'release').touch()
        try:
            identity = directory / 'child-pid'
            if identity.exists():
                child_pid = int(identity.read_text())
                if owned_process_state(child_pid) not in ('terminated', 'Z', 'X'):
                    try:
                        os.kill(child_pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
        finally:
            unrelated.terminate()
            unrelated.wait(timeout=3)


def test_ordinary_process_success_keeps_real_effect(erp_deployed):
    directory = erp_deployed['root'] / 'owned successful process'
    directory.mkdir()
    argv, cwd, env = selected_process(erp_deployed, directory, 'success')
    result = subprocess.run(argv, cwd=cwd, env=env, capture_output=True, text=True, timeout=6)
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert json.loads(result.stdout)['state'] == 'satisfied'
    assert (directory / 'effect').read_text() == 'ordinary successful effect'


@pytest.mark.parametrize('damage', ['missing-package', 'damaged-entry'])
def test_real_node_dependency_damage_is_detected_and_repaired(erp_deployed, damage):
    case = export_erp_source(erp_deployed)
    root = case['repository'] / 'document-ui'
    node = require_input('P003_NODE_EXECUTABLE')
    npm = require_input('P003_NPM_ENTRY_POINT')
    environment = dict(os.environ, PATH=str(Path(node).parent) + os.pathsep + os.environ['PATH'])
    baseline = subprocess.run([node, npm, 'ci', '--no-audit', '--no-fund'], cwd=root,
                              env=environment, capture_output=True, text=True, timeout=120)
    assert baseline.returncode == 0, ('Real npm baseline is setup, not RED', baseline.stdout, baseline.stderr)
    entry = root / 'node_modules/vite/dist/node/index.js'
    command = [node, str(FIXTURES / 'erp_vite_entry.mjs'), str(entry)]
    usable = subprocess.run(command, cwd=root, env=environment, capture_output=True, text=True, timeout=10)
    assert usable.returncode == 0 and usable.stdout == 'function', ('Real Node baseline is setup, not RED', usable.stderr)
    locks = {name: (root / name).read_bytes() for name in ('package-lock.json', 'node_modules/.package-lock.json')}
    if damage == 'missing-package':
        shutil.rmtree(root / 'node_modules/vite')
    else:
        entry.write_text('throw new Error("test-owned damaged entry");\n')
    damaged = subprocess.run(command, cwd=root, env=environment, capture_output=True, text=True, timeout=10)
    assert damaged.returncode != 0, 'Test damage must independently make the real entry unusable'
    assert all((root / name).read_bytes() == expected for name, expected in locks.items())
    result, response = action(case, 'erp-node-document-ui', 'check')
    assert result.returncode == 0 and response['state'] != 'satisfied', 'Lock metadata must not hide unusable Node dependencies'
    result, response = action(case, 'erp-node-document-ui', 'repair')
    assert result.returncode == 0 and response['outcome'] == 'completed', (result.stdout, result.stderr)
    restored = subprocess.run(command, cwd=root, env=environment, capture_output=True, text=True, timeout=10)
    assert restored.returncode == 0 and restored.stdout == 'function', (restored.stdout, restored.stderr)
    result, response = action(case, 'erp-node-document-ui', 'check')
    assert result.returncode == 0 and response['state'] == 'satisfied', (result.stdout, result.stderr)
    result, response = action(case, 'erp-node-document-ui', 'repair')
    assert result.returncode == 0 and response['outcome'] == 'completed', (result.stdout, result.stderr)
    repeated = subprocess.run(command, cwd=root, env=environment, capture_output=True, text=True, timeout=10)
    assert repeated.returncode == 0 and repeated.stdout == 'function'
