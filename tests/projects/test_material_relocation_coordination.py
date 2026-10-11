"""Real relocation coordination, native liveness and placement admission."""
from copy import deepcopy
from pathlib import Path

import pytest

from conftest import WorkPoise, write_json
from hook_transport.helpers import event, install
from poise.application.work import WorkTools
from poise.common import PoiseError, load_config
from poise.modules.ownership.domain import Liveness
from projects.test_update import update_tools
from .material_relocation_helpers import (
    STDOUT, assert_candidate_authority_denial, configured_hook, fresh, immutable, placed, prepare, relocate, required,
)


def native(service, definition, case, kind='SessionStart', session='fixture-hook-owner'):
    packet = event(kind, session=session)
    packet['cwd'] = str(case['project']['root'])
    return service.event(definition, packet)


def test_hook_relocation_settings_are_valid_before_apply(project, tmp_path):
    from poise.infrastructure.hook_transport import HookSettings
    case = prepare(project, tmp_path, native_hooks=True)
    service = configured_hook(case, tmp_path)
    proposal = case['move']['hook_destinations'][0]
    assert all(not Path(proposal['settings'][key]).is_absolute() for key in (
        'project_config', 'hooks_file', 'definitions', 'state', 'lock',
    ))
    target = HookSettings.proposed(Path(proposal['settings_path']), proposal['settings'])
    assert target.project_config == service.settings.project_config
    assert target.state == case['destination'] / 'hook-control'
    assert target.lock == case['destination'] / 'hook-control.lock'


@pytest.mark.parametrize('timing', ['before', 'after'])
@pytest.mark.parametrize('publication', ['artifact-registry', 'hook-owner'])
def test_actual_apply_reaches_publication_boundary_and_recovers(
        project, tmp_path, monkeypatch, timing, publication):
    from poise.application.hook_transport import HookCommands
    from poise.infrastructure.sqlite.artifacts import SqliteArtifactRepository
    case = prepare(project, tmp_path, native_hooks=True)
    service = configured_hook(case, tmp_path)
    required(install, service)
    hooks_before = service.settings.hooks_file.read_bytes()
    owner, name = ((SqliteArtifactRepository, 'rebind') if publication == 'artifact-registry'
                   else (HookCommands, 'relocate'))
    # A proposed owner may be absent before implementation. Do not invent it:
    # the assertion below requires actual public apply to reach the real owner.
    effect = getattr(owner, name, None)
    observed = []
    before = immutable(case['tools'].runtime)

    def interrupted(self, *args, **kwargs):
        observed.append(timing)
        if timing == 'after':
            effect(self, *args, **kwargs)
        raise OSError('Interrupted ' + timing + ' actual relocation ' + publication)

    with monkeypatch.context() as patch:
        if effect is not None:
            patch.setattr(owner, name, interrupted)
        error = None
        try:
            update_tools(case['settings']).apply(case['packet'])
        except (PoiseError, OSError) as exc:
            error = exc
    assert observed == [timing], 'Actual relocation never reached ' + publication
    assert error is not None
    assert case['source'].is_dir()
    assert Path(case['receipt']['stdout']).read_bytes() == STDOUT
    assert immutable(case['tools'].runtime) == before
    moved = relocate(case)
    assert immutable(moved.runtime) == before
    assert moved.runtime._receipt_files_intact(case['receipt'], case['method'])
    confirmed = moved.runtime.store.artifact_records('T1')
    replay = required(update_tools(case['settings']).apply, case['packet'])
    assert replay['replayed'] is True
    assert fresh(case).runtime.store.artifact_records('T1') == confirmed
    from poise.infrastructure.hook_transport import HookService
    target = HookService(case['hook_target'])
    assert target.settings.state.is_relative_to(case['destination'])
    assert target.settings.hooks_file.read_bytes() != hooks_before
    import shlex
    assert shlex.quote(str(case['hook_target'])) in target.settings.hooks_file.read_text()


@pytest.mark.parametrize('liveness', ['live', 'uncertain'])
def test_moved_hook_root_requires_dead_native_owner(project, tmp_path, liveness):
    case = prepare(project, tmp_path, native_hooks=True)
    service = configured_hook(case, tmp_path)
    installed = required(install, service)
    definition = installed['definition_path']
    if liveness == 'live':
        native(service, definition, case)
    else:
        packet = event(session='fixture-hook-owner')
        packet['cwd'] = str(case['project']['root'])
        service._bind(definition, packet)
    binding = service.latest_binding('fixture-hook-owner', 'primary')
    expected = Liveness.LIVE if liveness == 'live' else Liveness.UNCERTAIN
    assert service.registry.liveness(binding['session_id']) is expected
    # Task release does not terminate an independently observed native session.
    assert case['tools'].runtime.task_queries.record('T1')['claimed_by'] is None
    config = case['config_path'].read_bytes()
    before = immutable(case['tools'].runtime)
    error = None
    try:
        update_tools(case['settings']).apply(case['packet'])
    except PoiseError as exc:
        error = exc
    assert error is not None
    assert any(word in str(error).lower() for word in ('hook', 'session', 'live', 'dead', 'uncertain'))
    assert case['config_path'].read_bytes() == config and case['source'].is_dir()
    assert not case['destination'].exists()
    assert service.registry.get(binding['session_id'])[0] == binding
    assert service.registry.liveness(binding['session_id']) is expected
    assert immutable(case['tools'].runtime) == before


def test_dead_hook_owner_is_preserved_and_new_session_uses_new_root(project, tmp_path):
    from poise.infrastructure.hook_transport import HookService
    case = prepare(project, tmp_path, native_hooks=True)
    service = configured_hook(case, tmp_path)
    installed = required(install, service)
    definition = installed['definition_path']
    native(service, definition, case)
    binding = service.latest_binding('fixture-hook-owner', 'primary')
    native(service, definition, case, 'SessionEnd')
    assert service.registry.liveness(binding['session_id']) is Liveness.DEAD
    moved = relocate(case)
    target = HookService(case['hook_target'])
    assert target.registry.get(binding['session_id'])[0] == binding
    assert target.registry.liveness(binding['session_id']) is Liveness.DEAD
    native(target, definition, case, session='fixture-new-hook-owner')
    new = target.latest_binding('fixture-new-hook-owner', 'primary')
    assert Path(new['binding_path']).is_relative_to(case['destination'])
    assert Path(new['binding_path']).is_file()
    assert new['session_id'] != binding['session_id']
    assert target.registry.get(binding['session_id'])[0] == binding
    assert not case['source'].exists()
    assert moved.runtime._receipt_files_intact(case['receipt'], case['method'])


def test_independent_unchanged_live_hook_owner_survives_task_state_move(project, tmp_path):
    case = prepare(project, tmp_path, native_hooks=True)
    service = configured_hook(case, tmp_path, independent=True)
    installed = required(install, service)
    native(service, installed['definition_path'], case)
    binding = service.latest_binding('fixture-hook-owner', 'primary')
    assert service.registry.liveness(binding['session_id']) is Liveness.LIVE
    hook_settings = service.settings.path.read_bytes()
    moved = relocate(case)
    assert service.settings.path.read_bytes() == hook_settings
    assert service.registry.get(binding['session_id'])[0] == binding
    assert service.registry.liveness(binding['session_id']) is Liveness.LIVE
    assert Path(binding['binding_path']).is_file()
    assert moved.runtime._receipt_files_intact(case['receipt'], case['method'])


def test_conflicting_registry_placement_refuses_same_id_decoy(project, tmp_path):
    import shutil
    case = prepare(project, tmp_path)
    reader = relocate(case)
    positive = reader.runtime.result_views.command_views([case['receipt']])
    assert 'presentation_error' not in positive[0]
    assert STDOUT.decode().strip() in positive[0]['primary']
    # The actual published registry still authorizes the first destination.
    # A separate unregistered candidate points to a copied same-ID registry.
    conflict = tmp_path / 'conflicting-placement'
    shutil.copytree(case['destination'], conflict)
    root, cfg, _ = load_config(case['config_path'])
    candidate = deepcopy(cfg)
    candidate['paths']['state'] = str(conflict)
    path = write_json(root / 'conflicting-reader.json', candidate)
    decoy = conflict / placed(case, case['receipt']['stdout']).relative_to(case['destination'])
    decoy.write_bytes(b'conflicting same-id material\n')
    assert_candidate_authority_denial(case, path, 'conflicting-reader', reader.runtime)
    assert reader.runtime.evidence_commands.list_for('T1')[0] == case['receipt']
    assert placed(case, case['receipt']['stdout']).read_bytes() == STDOUT
    assert decoy.read_bytes() == b'conflicting same-id material\n'


def test_cached_hook_writer_rereads_after_external_project_fence(project, tmp_path, monkeypatch):
    import fcntl
    import os
    import threading
    from poise.infrastructure.projects import ProjectSettings
    case = prepare(project, tmp_path, native_hooks=True)
    cached = configured_hook(case, tmp_path)
    installed = required(install, cached)
    fence = ProjectSettings(case['settings']).lock
    inode = fence.stat()
    paused, proceed = threading.Event(), threading.Event()
    observed, errors = [], []
    effect = fcntl.flock

    def acquire(fd, operation):
        actual = os.fstat(fd)
        if (threading.current_thread().name == 'cached-hook-writer'
                and operation & fcntl.LOCK_EX
                and (actual.st_dev, actual.st_ino) == (inode.st_dev, inode.st_ino)):
            observed.append('external-project-fence')
            paused.set()
            if not proceed.wait(10):
                raise AssertionError('Fixture did not release paused hook writer')
        return effect(fd, operation)

    def write():
        try:
            native(cached, installed['definition_path'], case, session='stale-fixture-writer')
        except Exception as exc:
            errors.append(exc)

    with monkeypatch.context() as patch:
        patch.setattr(fcntl, 'flock', acquire)
        worker = threading.Thread(target=write, name='cached-hook-writer')
        worker.start()
        try:
            assert paused.wait(3), 'Cached real hook writer never acquired the project fence'
            moved = relocate(case)
        finally:
            proceed.set()
            worker.join(10)
    assert not worker.is_alive()
    assert observed == ['external-project-fence']
    assert len(errors) == 1 and isinstance(errors[0], PoiseError)
    assert not case['source'].exists(), 'Stale writer recreated retired state'
    assert moved.runtime._receipt_files_intact(case['receipt'], case['method'])
