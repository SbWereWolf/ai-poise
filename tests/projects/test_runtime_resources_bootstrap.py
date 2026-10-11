"""Explicit initial publication through installed and existing public owners."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from poise.common import PoiseError
from .test_update import project_revision, update_tools
from .runtime_resources_bootstrap_helpers import (
    apply_required, arrange_bootstrap, assert_only_publication,
    bootstrap_cli, bootstrap_python, files,
)


def test_installed_bootstrap_adds_only_association_and_replays(project, tmp_path, bootstrap_python):
    case = arrange_bootstrap(project, tmp_path)
    before = files(case['root'])
    assert 'runtime_resources' not in json.loads(case['config_path'].read_text())
    process, done = bootstrap_cli(bootstrap_python, case)
    assert process.returncode == 0, done
    assert done['status'] == 'completed'
    assert done['schema'] == 'project-runtime-resources-bootstrap-result-1'
    assert done['before_revision'] == case['packet']['expected_revision']
    assert done['after_revision'] == project_revision(case['config_path'])
    assert done['after_revision'] != done['before_revision']
    assert_only_publication(case, before)
    published = files(case['root'])
    process, replay = bootstrap_cli(bootstrap_python, case)
    assert process.returncode == 0 and replay['replayed'] is True
    for name in ('before_revision', 'after_revision', 'request_id'):
        assert replay[name] == done[name]
    assert files(case['root']) == published


@pytest.mark.parametrize('fault', [
    'selected-settings', 'setup-association', 'manifest-binding', 'revision',
    'hook-settings', 'hook-identity',
])
def test_installed_bootstrap_rejects_bad_authority_without_side_effects(
        project, tmp_path, bootstrap_python, fault):
    case = arrange_bootstrap(project, tmp_path, native_hooks=fault == 'hook-identity')
    packet = deepcopy(case['packet'])
    if fault == 'selected-settings':
        case['settings'] = tmp_path / 'absent-project-settings.json'
    elif fault == 'setup-association':
        packet['runtime_resources']['project_setup'] = str(tmp_path / 'foreign-setup.json')
    elif fault == 'manifest-binding':
        decoy = case['root'] / 'unregistered-decoy.json'
        decoy.write_bytes(case['config_path'].read_bytes())
        packet['config_path'] = str(decoy)
    elif fault == 'revision':
        packet['expected_revision'] = '0' * 64
    else:
        hook_path = tmp_path / 'unregistered-hook-settings.json'
        if fault == 'hook-identity':
            # Valid settings, but no installation owns the proposed identity.
            # Missing settings must not mask the association check. Ordinary
            # hook installation cannot initialize a pre-bootstrap project.
            import tempfile
            from hook_transport.helpers import settings
            arrangement = deepcopy(project)
            arrangement.update(root=case['root'], config_path=case['config_path'], cfg=case['config'])
            with tempfile.TemporaryDirectory(dir=tmp_path) as directory:
                arrangement['config_path'] = Path(directory) / 'unmanaged.json'
                hook_path = settings(arrangement, tmp_path)
        packet['runtime_resources']['hooks'] = [{
            'installation_id': 'foreign-installation' if fault == 'hook-identity' else 'primary-hooks',
            'settings_path': str(hook_path),
        }]
    before = files(case['root'])
    process, rejected = bootstrap_cli(bootstrap_python, case, packet)
    assert process.returncode == 2 and rejected['status'] == 'rejected'
    assert rejected['reason'].strip()
    # Distinguish a real authority refusal from blanket unsupported-schema denial.
    probe = deepcopy(packet)
    probe['schema'] = 'test-owned-unknown-bootstrap-schema'
    _, unsupported = bootstrap_cli(bootstrap_python, case, probe)
    if fault != 'selected-settings':
        assert rejected['reason'] != unsupported['reason'], 'No bootstrap authority check was reached'
    assert files(case['root']) == before


@pytest.mark.parametrize('fault', ['same-request-changed-input', 'second-request', 'changed-live-bytes'])
def test_installed_bootstrap_conflicts_preserve_confirmed_publication(
        project, tmp_path, bootstrap_python, fault):
    case = arrange_bootstrap(project, tmp_path)
    process, done = bootstrap_cli(bootstrap_python, case)
    assert process.returncode == 0, done
    packet = deepcopy(case['packet'])
    if fault == 'same-request-changed-input':
        packet['runtime_resources']['preserved_unknown_resource']['future'].append('changed')
    elif fault == 'second-request':
        packet['request_id'] = 'second-initial-publication'
        packet['receipt_path'] = 'operations/second-initial-publication.json'
        packet['expected_revision'] = project_revision(case['config_path'])
    else:
        # Public owner changes an unrelated existing field after confirmation.
        from .test_update import update_request
        change = update_request(case['config_path'], project_revision(case['config_path']),
            request_id='operator-edit-after-bootstrap', manifest_edits=[{
                'path': ['preserved_unknown'], 'value': {'operator': 'new independent work'},
            }])
        change['receipt_path'] = 'operations/operator-edit.json'
        update_tools(case['settings']).apply(change)
    before = files(case['root'])
    process, rejected = bootstrap_cli(bootstrap_python, case, packet)
    assert process.returncode == 2 and rejected['status'] == 'rejected'
    assert files(case['root']) == before


@pytest.mark.parametrize('timing', ['before', 'after'])
@pytest.mark.parametrize('publication', ['manifest', 'receipt'])
def test_bootstrap_partial_publication_resumes_exact_intent(
        project, tmp_path, monkeypatch, timing, publication):
    import poise.infrastructure.project_config as owner
    case = arrange_bootstrap(project, tmp_path)
    before = files(case['root'])
    effect = owner.atomic_write
    observed = []

    def interrupt(path, *args, **kwargs):
        selected = (Path(path) == case['config_path'] if publication == 'manifest'
                    else Path(path).name == 'initial-resource-publication.json')
        if not selected:
            return effect(path, *args, **kwargs)
        observed.append(timing)
        if timing == 'after':
            effect(path, *args, **kwargs)
        raise OSError('Interrupted ' + timing + ' bootstrap ' + publication)

    with monkeypatch.context() as patch:
        patch.setattr(owner, 'atomic_write', interrupt)
        error = None
        try:
            update_tools(case['settings']).apply(case['packet'])
        except (PoiseError, OSError) as exc:
            error = exc
    assert observed == [timing], 'Actual bootstrap publication boundary was never reached'
    assert error is not None
    for name, content in before.items():
        if name != str(case['config_path'].relative_to(case['root'])):
            assert (case['root'] / name).read_bytes() == content
    done = apply_required(case)
    assert done['status'] == 'completed'
    assert_only_publication(case, before)
    completed = files(case['root'])
    replay = apply_required(case)
    assert replay['replayed'] is True
    assert files(case['root']) == completed


def test_bootstrap_never_constructs_ordinary_mutable_runtime(project, tmp_path, monkeypatch):
    import poise.runtime as runtime
    import poise.infrastructure.hook_transport as hook
    case = arrange_bootstrap(project, tmp_path)
    before = files(case['root'])

    def forbidden(*args, **kwargs):
        raise AssertionError('Bootstrap constructed an ordinary mutable Task/hook runtime')

    monkeypatch.setattr(runtime.Poise, '__init__', forbidden)
    monkeypatch.setattr(hook.HookService, '__init__', forbidden)
    done = apply_required(case)
    assert done['status'] == 'completed'
    assert_only_publication(case, before)
