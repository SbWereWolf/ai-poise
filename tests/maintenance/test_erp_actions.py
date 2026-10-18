"""R1/R3 independent action observations; no aggregate doctor or live ERP."""
import json

import pytest
pytest.register_assert_rewrite('erp_support')
from erp_support import (
    CONTRACT, action, copy_executable_fixture, invoke, require_input,
)

pytest_plugins = ['erp_support']


@pytest.mark.parametrize('identifier', [
    'erp-git-repository', 'erp-service-tools', 'erp-php-runtime',
    'erp-node-runtime', 'erp-composer', 'erp-npm',
])
def test_selected_real_tools_are_independently_available(erp_deployed, identifier):
    result, response = action(erp_deployed, identifier, 'check')
    assert result.returncode == 0 and response['state'] == 'satisfied', (
        result.stdout, result.stderr)


@pytest.mark.parametrize('extension', CONTRACT['php_extensions'])
def test_each_php_extension_has_a_real_observation(erp_deployed, extension):
    runtime = invoke([require_input('P003_PHP_EXECUTABLE'), '-r',
                      'echo json_encode(get_loaded_extensions());'])
    assert runtime.returncode == 0, runtime.stderr
    loaded = {name.lower() for name in json.loads(runtime.stdout)}
    assert extension in loaded, 'Missing declared test-runtime extension is setup failure'
    result, response = action(erp_deployed, 'erp-php-extension-' + extension, 'check')
    assert result.returncode == 0 and response['state'] == 'satisfied', (
        result.stdout, result.stderr)


@pytest.mark.parametrize('parameter,identifier', [
    ('php_executable', 'erp-php-runtime'),
    ('node_executable', 'erp-node-runtime'),
    ('composer_executable', 'erp-composer'),
    ('npm_entry_point', 'erp-npm'),
])
def test_missing_configured_tool_has_cause_and_actionable_advice(
        erp_deployed, parameter, identifier):
    missing = erp_deployed['root'] / 'selected missing executable Ω'
    result, response = action(erp_deployed, identifier, 'check', {parameter: str(missing)})
    assert result.returncode == 0 and response['state'] == 'unavailable'
    assert response['cause'].strip()
    assert response['recommendations'] and all(response['recommendations'])


def test_controlled_php_extension_absence_is_not_runtime_success(erp_deployed):
    """Invocation fixture, not evidence of PHP installation or compatibility."""
    executable = copy_executable_fixture(erp_deployed, 'erp_empty_extensions.py')
    result, response = action(erp_deployed, 'erp-php-extension-bcmath', 'check',
                              {'php_executable': str(executable)})
    assert result.returncode == 0 and response['state'] == 'missing'


def test_runtime_directory_install_and_repeat_preserve_existing_content(erp_deployed):
    case = erp_deployed
    expected = [case['repository'] / app / relative
                for app in CONTRACT['php_applications']
                for relative in CONTRACT['runtime_directories']]
    expected += [case['repository'] / relative for relative in CONTRACT['root_directories']]
    result, response = action(case, 'erp-runtime-directories', 'check')
    assert result.returncode == 0 and response['state'] == 'missing'
    result, response = action(case, 'erp-runtime-directories', 'install')
    assert result.returncode == 0 and response['outcome'] == 'completed'
    assert all(path.is_dir() for path in expected)
    retained = expected[0] / 'retained.txt'
    retained.write_text('Unrelated operator data Ω\n', encoding='utf-8')
    result, response = action(case, 'erp-runtime-directories', 'repair')
    assert result.returncode == 0 and response['outcome'] == 'completed'
    assert retained.read_text() == 'Unrelated operator data Ω\n'
    result, response = action(case, 'erp-runtime-directories', 'check')
    assert result.returncode == 0 and response['state'] == 'satisfied'


def test_selected_missing_git_reference_is_diagnosed(erp_deployed):
    result, response = action(erp_deployed, 'erp-git-repository', 'check',
                              {'base_ref': 'missing-selected-ref'})
    assert result.returncode == 0 and response['state'] == 'unavailable'
    assert response['cause'].strip() and response['recommendations']
