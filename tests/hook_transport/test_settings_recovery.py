"""Explicit settings recovery retains the existing native session episode."""
from copy import deepcopy
import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from conftest import write_json
from poise.common import PoiseError, digest, file_digest
from poise.infrastructure.hook_transport import HookService, HookSettings
from poise.interfaces.hook_transport import execute
from hook_transport.helpers import event, install, settings


def legacy_installation(project, tmp_path, monkeypatch):
    path = settings(project, tmp_path)
    raw = json.loads(path.read_text())
    summary = raw.pop('capability_summary')
    raw['operator_note'] = 'Preserve this explicitly configured option.'
    write_json(path, raw)
    # Model the earlier producer that did not require this settings section.
    # Seed the installation and genuine fixture events through their normal owners.
    with monkeypatch.context() as historical:
        historical.setattr(HookSettings, 'require_summary', lambda self: summary)
        service = HookService(path)
        foreign = {'description': 'Foreign native hook', 'hooks': {'SessionStart': [
            {'matcher': 'startup', 'hooks': [{'type': 'command', 'command': 'echo foreign'}]}]}}
        write_json(service.settings.hooks_file, foreign)
        installed = install(service, revision=service.revision())
        native = event(session='preserved-parent')
        native['cwd'] = str(project['app'])
        service.event(installed['definition_path'], native)
        prompt = event('UserPromptSubmit', session='preserved-parent')
        prompt['cwd'] = str(project['app'])
        service.event(installed['definition_path'], prompt)
    binding = service.latest_binding('preserved-parent', 'primary')
    previous_command = service.settings.prior_launcher_commands(
        'hook-work', '--binding', binding['binding_path'])[0]
    Path(binding['launcher']).write_text('#!/bin/sh\nexec ' + previous_command + '\n')
    target = {**deepcopy(raw), 'capability_summary': summary}
    packet = {'request_id': 'recover-existing-settings',
              'expected_settings_digest': digest(raw),
              'expected_revision': file_digest(service.settings.hooks_file),
              'target_revision': file_digest(service.settings.hooks_file),
              'settings': target,
              'definition': json.loads(Path(installed['definition_path']).read_text())}
    return service, packet, binding, foreign


def saved_state(service, binding):
    paths = [service.settings.path, service.settings.hooks_file,
             Path(binding['binding_path']), Path(binding['launcher'])]
    with service.registry.read_transaction() as db:
        rows = {table: db.execute('SELECT * FROM ' + table + ' ORDER BY 1').fetchall()
                for table in ('bindings', 'installations', 'hook_events', 'operations')}
    return {str(path): path.read_bytes() for path in paths}, rows


def lifecycle_events(rows):
    return [row for row in rows if row[3] in ('SessionStart', 'UserPromptSubmit', 'Stop', 'SessionEnd')]


def current_installation(project, tmp_path):
    service = HookService(settings(project, tmp_path))
    installed = install(service)
    native = event(session='preserved-parent')
    native['cwd'] = str(project['app'])
    service.event(installed['definition_path'], native)
    return service, installed, service.latest_binding('preserved-parent', 'primary')


def test_public_same_file_recovery_preserves_episode_and_resumes(project, tmp_path, monkeypatch):
    service, packet, binding, foreign = legacy_installation(project, tmp_path, monkeypatch)
    _, before = saved_state(service, binding)
    initial = service.diagnose({'definition_path': binding['definition_path'],
        'session_id': 'preserved-parent', 'event': 'SessionStart', 'event_id': None,
        'run_ids': [], 'probe_cwd': None})
    assert initial['binding'] == {'status': 'valid', 'session_id': 'preserved-parent'}
    assert initial['native_event']['status'] == 'observed'
    assert saved_state(service, binding)[1] == before
    output, error = io.StringIO(), io.StringIO()
    code = execute('runtime-config', SimpleNamespace(settings=str(service.settings.path)),
                   io.BytesIO(json.dumps({'operation': 'reconfigure', 'input': packet}).encode()), output, error)
    assert code == 0, error.getvalue() + output.getvalue()
    response = json.loads(Path(json.loads(output.getvalue())['response_path']).read_text())
    assert response['status'] == 'installed'
    assert response['capability_checks']['ready'] is True
    assert response['binding_count'] == 1
    current = HookService(service.settings.path)
    record, message = current.registry.get('preserved-parent')
    assert record['session_id'] == 'preserved-parent'
    assert record['external_session'] == 'preserved-parent'
    assert record['agent_id'] == 'primary'
    assert record['binding_path'] == binding['binding_path']
    assert record['launcher'] == binding['launcher']
    assert message == json.loads(before['bindings'][0][2])
    assert json.loads(service.settings.path.read_text()) == packet['settings']
    assert Path(record['launcher']).read_text().endswith(' "$@"\n')
    native = json.loads(service.settings.hooks_file.read_text())
    assert native['description'] == foreign['description']
    assert foreign['hooks']['SessionStart'][0] in native['hooks']['SessionStart']
    _, after = saved_state(current, record)
    assert lifecycle_events(after['hook_events']) == lifecycle_events(before['hook_events'])
    original_start = [row[0] for row in before['hook_events'] if row[3] == 'SessionStart'][-1]
    observed = current.diagnose({'definition_path': response['definition_path'],
        'session_id': 'preserved-parent', 'event': 'SessionStart', 'event_id': None,
        'run_ids': [], 'probe_cwd': None})
    assert observed['binding'] == {'status': 'valid', 'session_id': 'preserved-parent'}
    assert observed['native_event']['status'] == 'observed'
    assert observed['native_event']['id'] == original_start
    assert observed['native_event']['session_id'] == 'preserved-parent'
    assert observed['native_event']['source'] == 'startup'
    assert lifecycle_events(saved_state(current, record)[1]['hook_events']) == lifecycle_events(before['hook_events'])
    assert current.reconfigure(packet) == {k: v for k, v in response.items() if k != 'capability_checks'}
    resume = event(session='preserved-parent')
    resume.update(source='resume', cwd=str(project['app']))
    current.event(response['definition_path'], resume)
    assert current.latest_binding('preserved-parent', 'primary') == record


def test_same_id_resume_accepts_a_current_binding(project, tmp_path):
    service, installed, binding = current_installation(project, tmp_path)
    resume = event(session='preserved-parent')
    resume.update(source='resume', cwd=str(project['app']))
    service.event(installed['definition_path'], resume)
    assert service.latest_binding('preserved-parent', 'primary') == binding


@pytest.mark.parametrize('field,value', [
    ('external_session', 'another-session'), ('settings', '/another/hook-settings.json'),
    ('project', 'another-project'), ('definition_path', '/another/hook-definition.json')])
def test_resume_rejects_different_binding_data_without_effects(project, tmp_path, field, value):
    service, installed, binding = current_installation(project, tmp_path)
    write_json(Path(binding['binding_path']), {**binding, field: value})
    before = saved_state(service, binding)
    observed = service.diagnose({'definition_path': installed['definition_path'],
        'session_id': 'preserved-parent', 'event': 'SessionStart', 'event_id': None,
        'run_ids': [], 'probe_cwd': None})
    assert observed['binding']['status'] == 'changed'
    assert saved_state(service, binding) == before
    resume = event(session='preserved-parent')
    resume.update(source='resume', cwd=str(project['app']))
    with pytest.raises(PoiseError):
        service.event(installed['definition_path'], resume)
    assert saved_state(service, binding) == before


@pytest.mark.parametrize('summary', [None, {'file': 'capabilities.json'}])
def test_invalid_recovery_summary_has_no_effects(project, tmp_path, monkeypatch, summary):
    service, packet, binding, _ = legacy_installation(project, tmp_path, monkeypatch)
    if summary is None:
        del packet['settings']['capability_summary']
    else:
        packet['settings']['capability_summary'] = summary
    before = saved_state(service, binding)
    with pytest.raises(PoiseError):
        service.reconfigure(packet)
    assert saved_state(service, binding) == before


def test_recovery_preserves_newer_source_configuration(project, tmp_path, monkeypatch):
    service, packet, binding, _ = legacy_installation(project, tmp_path, monkeypatch)
    changed = json.loads(service.settings.path.read_text())
    changed['operator_note'] = 'A newer explicit operator edit.'
    write_json(service.settings.path, changed)
    service = HookService(service.settings.path)
    before = saved_state(service, binding)
    with pytest.raises(PoiseError):
        HookService(service.settings.path).reconfigure(packet)
    assert saved_state(service, binding) == before


@pytest.mark.parametrize('point', ['binding', 'settings'])
def test_same_file_recovery_retries_owned_partial_publication(project, tmp_path, monkeypatch, point):
    service, packet, binding, _ = legacy_installation(project, tmp_path, monkeypatch)
    import poise.infrastructure.hook_transport as owner
    original = owner.atomic_write
    fault_path = Path(binding['binding_path']) if point == 'binding' else service.settings.path
    failed = False

    def interrupt_once(path, *args, **kwargs):
        nonlocal failed
        if Path(path) == fault_path and not failed:
            failed = True
            raise OSError('settings recovery publication interrupted')
        return original(path, *args, **kwargs)

    monkeypatch.setattr(owner, 'atomic_write', interrupt_once)
    with pytest.raises(OSError, match='publication interrupted'):
        service.reconfigure(packet)
    assert failed
    receipt = HookService(service.settings.path).reconfigure(packet)
    assert receipt['status'] == 'installed'
    current = HookService(service.settings.path)
    assert current.reconfigure(packet) == receipt


def test_operational_config_metadata_does_not_block_observation_or_resume(project, tmp_path):
    service, installed, binding = current_installation(project, tmp_path)
    before_events = lifecycle_events(saved_state(service, binding)[1]['hook_events'])
    original_start = [row[0] for row in before_events if row[3] == 'SessionStart'][-1]
    raw = json.loads(service.settings.path.read_text())
    raw['operator_note'] = 'An ignored operational annotation, not another session.'
    write_json(service.settings.path, raw)
    current = HookService(service.settings.path)
    before_read = saved_state(current, binding)
    record, _ = current._record(Path(binding['binding_path']))
    assert {k: v for k, v in record.items() if k != 'settings_digest'} == {
        k: v for k, v in binding.items() if k != 'settings_digest'}
    observed = current.diagnose({'definition_path': installed['definition_path'],
        'session_id': 'preserved-parent', 'event': 'SessionStart', 'event_id': None,
        'run_ids': [], 'probe_cwd': None})
    assert observed['binding'] == {'status': 'valid', 'session_id': 'preserved-parent'}
    assert observed['native_event']['id'] == original_start
    assert saved_state(current, binding) == before_read
    resume = event(session='preserved-parent')
    resume.update(source='resume', cwd=str(project['app']))
    current.event(installed['definition_path'], resume)
    updated = current.latest_binding('preserved-parent', 'primary')
    assert {k: v for k, v in updated.items() if k != 'settings_digest'} == {
        k: v for k, v in binding.items() if k != 'settings_digest'}


def test_optional_file_bookkeeping_does_not_change_the_registered_session(project, tmp_path):
    service, installed, binding = current_installation(project, tmp_path)
    write_json(Path(binding['binding_path']), {**binding, 'settings_digest': 'old diagnostic metadata'})
    before_read = saved_state(service, binding)
    record, _ = service._record(Path(binding['binding_path']))
    assert record == binding
    observed = service.diagnose({'definition_path': installed['definition_path'],
        'session_id': 'preserved-parent', 'event': 'SessionStart', 'event_id': None,
        'run_ids': [], 'probe_cwd': None})
    assert observed['binding'] == {'status': 'valid', 'session_id': 'preserved-parent'}
    assert saved_state(service, binding) == before_read
    resume = event(session='preserved-parent')
    resume.update(source='resume', cwd=str(project['app']))
    service.event(installed['definition_path'], resume)
    assert service.latest_binding('preserved-parent', 'primary')['session_id'] == 'preserved-parent'
    after_resume = saved_state(service, binding)
    assert after_resume[0] == before_read[0]
    assert after_resume[1]['bindings'] == before_read[1]['bindings']


def bind_during_preparation(service, project, selected_settings, definition_path, session):
    original = service.settings.path.read_bytes()
    try:
        write_json(service.settings.path, selected_settings)
        producer = HookService(service.settings.path)
        native = event('UserPromptSubmit', session=session)
        native['cwd'] = str(project['app'])
        producer.event(definition_path, native)
        return producer.latest_binding(session, 'primary')
    finally:
        service.settings.path.write_bytes(original)


def test_recovery_preserves_a_binding_already_at_the_explicit_target(project, tmp_path, monkeypatch):
    service, packet, binding, _ = legacy_installation(project, tmp_path, monkeypatch)
    # Seed through the genuine fixture producer, never by constructing registry rows.
    target_binding = bind_during_preparation(service, project, packet['settings'],
                                            binding['definition_path'], 'already-target')
    service = HookService(service.settings.path)
    before = lifecycle_events(saved_state(service, binding)[1]['hook_events'])
    receipt = service.reconfigure(packet)
    assert receipt['status'] == 'installed'
    assert receipt['binding_count'] == 2
    current = HookService(service.settings.path)
    assert current.latest_binding('already-target', 'primary') == target_binding
    assert current.latest_binding('preserved-parent', 'primary')['session_id'] == 'preserved-parent'
    assert lifecycle_events(saved_state(current, binding)[1]['hook_events']) == before
    observed = current.diagnose({'definition_path': receipt['definition_path'],
        'session_id': 'already-target', 'event': 'SessionStart', 'event_id': None,
        'run_ids': [], 'probe_cwd': None})
    assert observed['binding'] == {'status': 'valid', 'session_id': 'already-target'}
    assert observed['native_event']['status'] == 'not_observed'
    assert current.reconfigure(packet) == receipt


def test_recovery_preserves_both_actors_on_target_binding_identity_disagreement(project, tmp_path, monkeypatch):
    service, packet, binding, _ = legacy_installation(project, tmp_path, monkeypatch)
    target = bind_during_preparation(service, project, packet['settings'],
                                     binding['definition_path'], 'already-target')
    write_json(Path(target['binding_path']), {**target, 'external_session': 'another-session'})
    service = HookService(service.settings.path)
    before = saved_state(service, binding)
    target_paths = [Path(target['binding_path']), Path(target['launcher'])]
    before_target = {str(path): path.read_bytes() for path in target_paths}
    with pytest.raises(PoiseError):
        service.reconfigure(packet)
    assert saved_state(service, binding) == before
    assert {str(path): path.read_bytes() for path in target_paths} == before_target


def test_explicit_update_preserves_same_object_with_other_settings_metadata(project, tmp_path, monkeypatch):
    service, packet, binding, _ = legacy_installation(project, tmp_path, monkeypatch)
    third = deepcopy(packet['settings'])
    third['capability_summary']['header_template'] = 'Custom {ready} {checked_at} {workspace} {snapshot_path}'
    other = bind_during_preparation(service, project, third,
                                    binding['definition_path'], 'other-settings')
    service = HookService(service.settings.path)
    before = lifecycle_events(saved_state(service, binding)[1]['hook_events'])
    receipt = service.reconfigure(packet)
    assert receipt['status'] == 'installed'
    assert receipt['binding_count'] == 2
    current = HookService(service.settings.path)
    updated = current.latest_binding('other-settings', 'primary')
    assert {k: v for k, v in updated.items() if k != 'settings_digest'} == {
        k: v for k, v in other.items() if k != 'settings_digest'}
    assert updated['settings_digest'] == current.latest_binding('preserved-parent', 'primary')['settings_digest']
    assert json.loads(service.settings.path.read_text()) == packet['settings']
    assert lifecycle_events(saved_state(current, binding)[1]['hook_events']) == before
    observed = current.diagnose({'definition_path': receipt['definition_path'],
        'session_id': 'other-settings', 'event': 'SessionStart', 'event_id': None,
        'run_ids': [], 'probe_cwd': None})
    assert observed['binding'] == {'status': 'valid', 'session_id': 'other-settings'}
    assert observed['native_event']['status'] == 'not_observed'
    assert current.reconfigure(packet) == receipt
