"""Source-profile admission through the supplied Bash entry and actual CLI."""
import json
from pathlib import Path

import pytest

from conftest import write_json
from hook_transport.test_selected_project_entry import (
    invoke,
    rows,
    selected_case,
)


@pytest.mark.parametrize('source_form', [
    'task-manifest', 'unsupported-schema', 'missing-task-adapter',
])
def test_unsuitable_source_profile_has_actionable_rejection(
        selected_case, tmp_path, source_form):
    case = selected_case
    source_rows = rows(case['source_db'])
    binding_path = Path(case['binding']['binding_path'])
    binding_bytes = binding_path.read_bytes()
    assert not case['selected_db'].exists()

    settings = json.loads(case['hook_settings'].read_text())
    if source_form == 'task-manifest':
        source_path = case['project']['config_path']
        source = json.loads(source_path.read_text())
        assert source['schema'] == 'ddd-accounting-12'
        assert 'task_adapter' not in source
        source['runtime_resources']['hooks'] = [{
            'installation_id': 'primary-hooks',
            'settings_path': str(case['hook_settings']),
        }]
        settings['project_config'] = str(source_path)
        write_json(case['hook_settings'], settings)
        association_key = 'config_path'
    else:
        source_path = case['service'].settings.project_config
        source = json.loads(source_path.read_text())
        assert source['schema'] == 'poise-harness-project-1'
        if source_form == 'unsupported-schema':
            source['schema'] = 'fixture-unsupported-profile'
            # The unsupported document is still explicitly registered for the
            # non-general branch; a missing association is not this scenario.
            registry = json.loads(case['registry'].read_text())
            registry['projects'][source['project']]['config_path'] = str(
                source_path.relative_to(case['tools']))
            write_json(case['registry'], registry)
            association_key = 'config_path'
        else:
            del source['task_adapter']
            association_key = 'harness_config_path'
    write_json(source_path, source)

    registry = json.loads(case['registry'].read_text())
    association = registry['projects'][source['project']]
    assert (case['tools'] / association[association_key]).resolve() == source_path
    assert source['runtime_resources']['hooks'] == [{
        'installation_id': 'primary-hooks',
        'settings_path': str(case['hook_settings']),
    }]

    result = invoke(case, '--project', 'selected-project')
    stdout = result.stdout.decode('utf-8')
    stderr = result.stderr.decode('utf-8')
    diagnostic = stdout + '\n' + stderr
    # Keep the full public reason when an ordinary envelope uses saved output.
    if stdout.strip().startswith('{'):
        envelope = json.loads(stdout)
        if 'response_path' in envelope:
            diagnostic += '\n' + Path(envelope['response_path']).read_text()
    write_json(tmp_path / 'source-profile-observation.json', {
        'source_form': source_form,
        'source_path': str(source_path),
        'argv': ['bash', case['binding']['launcher'], '--project', 'selected-project'],
        'cwd': str(case['project']['app']),
        'exit_code': result.returncode,
        'stdout': stdout,
        'stderr': stderr,
        'diagnostic': diagnostic,
    })

    assert rows(case['source_db']) == source_rows
    assert binding_path.read_bytes() == binding_bytes
    assert not case['selected_db'].exists()
    assert result.returncode == 2, diagnostic
    assert 'Traceback' not in diagnostic
    assert str(source_path) in diagnostic
    if source_form == 'missing-task-adapter':
        assert 'task_adapter' in diagnostic
        assert any(action in diagnostic.lower() for action in ('restore', 'select'))
    else:
        assert 'project_config' in diagnostic
        assert 'poise-harness-project-1' in diagnostic
        assert 'select' in diagnostic.lower()
