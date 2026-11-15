"""Tiny SQLite population; independent time, permission and byte oracles."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import os
import sqlite3
import threading

import pytest

from poise.application.backups import BackupCommands
from poise.common import PoiseError
from poise.infrastructure.backups import LocalTaskDatabaseBackups


FIXTURES = Path(__file__).resolve().parents[1] / 'fixtures/disk_retention'
NOW = datetime(2026, 11, 15, tzinfo=timezone.utc)
DEADLINE = datetime(2026, 11, 16, tzinfo=timezone.utc)
OLD_A = 'tasks-backup-20261113T000000000000Z.sqlite'
OLD_B = 'tasks-backup-20261113T010000000000Z.sqlite'
YOUNG = 'tasks-backup-20261114T020000000000Z.sqlite'
BOUNDARY = 'tasks-backup-20261114T010000000000Z.sqlite'


def database(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE marker(value TEXT NOT NULL)')
        db.execute('INSERT INTO marker(value) VALUES(?)', (value,))


def arrange(tmp_path, now=NOW):
    tmp_path.mkdir(parents=True, exist_ok=True)
    config = tmp_path / 'project.json'
    manifest = json.loads((FIXTURES / 'project.json').read_text())
    manifest['paths']['state'] = str(tmp_path)
    config.write_text(json.dumps(manifest))
    live = tmp_path / 'tasks.sqlite'
    database(live, 'LIVE')
    material = tmp_path / 'tasks/current/keep.txt'
    material.parent.mkdir(parents=True)
    material.write_bytes(b'live task material')
    return config, live, material, BackupCommands(
        LocalTaskDatabaseBackups(config), clock=lambda: now)


def required(commands, name):
    method = getattr(commands, name, None)
    assert callable(method), f'backup owner lacks {name} retention capability'
    return method


def assert_live(live, material, before):
    assert live.read_bytes() == before
    assert material.read_bytes() == b'live task material'
    with sqlite3.connect(f'file:{live}?mode=ro', uri=True) as db:
        assert db.execute('SELECT value FROM marker').fetchall() == [('LIVE',)]
        assert db.execute('PRAGMA integrity_check').fetchall() == [('ok',)]


def consumer_grant(archive, **overrides):
    # This fixture is independent of the retain producer and its serializer.
    raw = json.loads((FIXTURES / 'grant.json').read_text())
    raw['backup_name'] = archive.name
    raw['sha256'] = hashlib.sha256(archive.read_bytes()).hexdigest()
    raw['authorization']['backup_name'] = archive.name
    raw['authorization']['sha256'] = raw['sha256']
    # Independent canonical input identity; never call a production digest helper.
    identity = {key: raw[key] for key in ('project', 'backup_name', 'sha256',
                                         'valid_until', 'authorization', 'request_id')}
    raw['input_digest'] = hashlib.sha256(json.dumps(
        identity, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    raw.update(overrides)
    sidecar = archive.with_name(archive.name + '.retention.json')
    sidecar.write_text(json.dumps(raw), encoding='utf-8')
    return sidecar


def assert_protected_archive(archive, expected_bytes):
    assert archive.exists(), 'authorized backup was removed by age-only cleanup'
    assert archive.read_bytes() == expected_bytes, 'authorized backup payload was changed'


def assert_unbound_archive_rejected(archive, expected_bytes, result):
    assert result['success'] is False, 'archive bytes were not bound to the intact grant'
    assert_protected_archive(archive, expected_bytes)
    assert any(item['name'] == archive.name and item.get('continuation')
               for item in result['rejected']), 'binding refusal lacks addressed continuation'


def assert_fixed_deadline(sidecar):
    value = json.loads(sidecar.read_text())['valid_until']
    assert datetime.fromisoformat(value.replace('Z', '+00:00')) == DEADLINE, (
        'same-input retry extended the authorized deadline')


def test_prune_removes_only_expired_owned_archives_and_preserves_live_state(tmp_path):
    _, live, material, commands = arrange(tmp_path)
    directory = tmp_path / 'backups'
    database(directory / OLD_A, 'expired')
    database(directory / YOUNG, 'young')
    pending = directory / '.fixture.pending'
    pending.write_bytes(b'incomplete copy')
    legacy = directory / 'unknown.sqlite'
    database(legacy, 'needs addressed decision')
    before = live.read_bytes()
    result = required(commands, 'prune')()
    assert not (directory / OLD_A).exists()
    assert (directory / YOUNG).exists()
    assert pending.read_bytes() == b'incomplete copy'
    assert legacy.exists()
    assert result['success'] is False  # unknown legacy identity is explicitly unresolved
    assert result['rejected']
    assert_live(live, material, before)


@pytest.mark.parametrize(('delta', 'removed'), [(-1, False), (0, True), (1, True)],
                         ids=['before', 'at', 'after'])
def test_expiry_uses_generated_utc_time_at_23_hour_boundary(tmp_path, delta, removed):
    _, live, material, commands = arrange(tmp_path, NOW + timedelta(seconds=delta))
    archive = tmp_path / 'backups' / BOUNDARY
    database(archive, 'boundary')
    before = live.read_bytes()
    result = required(commands, 'prune')()
    assert result['success'] is True
    assert archive.exists() is (not removed)
    assert_live(live, material, before)


@pytest.mark.parametrize(('now', 'keep'), [(NOW, True), (DEADLINE, False),
                                         (DEADLINE + timedelta(seconds=1), False)],
                         ids=['before-deadline', 'at-deadline', 'after-deadline'])
def test_consumer_distinguishes_equally_old_ordinary_and_authorized_copies(tmp_path, now, keep):
    _, live, material, commands = arrange(tmp_path, now)
    directory = tmp_path / 'backups'
    database(directory / OLD_A, 'ordinary')
    archive = directory / OLD_B
    database(archive, 'protected')
    sidecar = consumer_grant(archive)
    protected_bytes = archive.read_bytes()
    before = live.read_bytes()
    result = required(commands, 'prune')()
    assert result['success'] is True
    assert not (directory / OLD_A).exists()
    assert archive.exists() is keep
    assert sidecar.exists() is keep
    if keep:
        assert_protected_archive(archive, protected_bytes)
        assert any(item['name'] == OLD_B and item['reason'] == 'retained_by_user_decision'
                   for item in result['retained'])
    assert_live(live, material, before)


@pytest.mark.parametrize('state', ['pending', 'corrupt', 'digest-mismatch', 'agent-only'])
def test_undecidable_grant_preserves_exact_archive_and_reports_non_success(tmp_path, state):
    _, live, material, commands = arrange(tmp_path)
    archive = tmp_path / 'backups' / OLD_B
    database(archive, 'needed')
    sidecar = consumer_grant(archive)
    if state == 'pending':
        sidecar.rename(sidecar.with_name('.' + sidecar.name + '.pending'))
    elif state == 'corrupt':
        sidecar.write_bytes(b'{incomplete')
    else:
        raw = json.loads(sidecar.read_text())
        if state == 'digest-mismatch':
            raw['sha256'] = '0' * 64
        else:
            raw['authorization']['role'] = 'assistant'
        sidecar.write_text(json.dumps(raw))
    before = live.read_bytes()
    original = archive.read_bytes()
    result = required(commands, 'prune')()
    assert result['success'] is False
    assert result['rejected'] and any(item['name'] == OLD_B for item in result['rejected'])
    assert archive.read_bytes() == original
    assert_live(live, material, before)


def test_remove_is_exact_idempotent_and_protects_active_grant(tmp_path):
    _, live, material, commands = arrange(tmp_path)
    directory = tmp_path / 'backups'
    ordinary, protected = directory / OLD_A, directory / OLD_B
    database(ordinary, 'ordinary')
    database(protected, 'needed')
    consumer_grant(protected)
    protected_bytes = protected.read_bytes()
    before = live.read_bytes()
    remove = required(commands, 'remove')
    assert remove(OLD_A)['success'] is True
    assert not ordinary.exists()
    assert remove(OLD_A)['success'] is True
    with pytest.raises(PoiseError, match='retention|decision|protect'):
        remove(OLD_B)
    assert_protected_archive(protected, protected_bytes)
    assert_live(live, material, before)


def test_partial_prune_and_orphan_metadata_replay_are_truthful(tmp_path, monkeypatch):
    _, live, material, commands = arrange(tmp_path, DEADLINE)
    directory = tmp_path / 'backups'
    database(directory / OLD_A, 'ordinary')
    archive = directory / OLD_B
    database(archive, 'expired exception')
    sidecar = consumer_grant(archive)
    before = live.read_bytes()
    original = Path.unlink

    def fail_sidecar(path, *args, **kwargs):
        if path == sidecar:
            raise PermissionError('fixture metadata cleanup denied')
        return original(path, *args, **kwargs)

    prune = required(commands, 'prune')
    with monkeypatch.context() as control:
        control.setattr(Path, 'unlink', fail_sidecar)
        first = prune()
    assert first['success'] is False
    assert not archive.exists() and sidecar.exists()
    assert not (directory / OLD_A).exists()
    second = prune()
    assert second['success'] is True
    assert not archive.exists() and not sidecar.exists()
    assert_live(live, material, before)
    assert Path.unlink is original


def test_empty_backup_directory_is_noop_without_creating_archive(tmp_path):
    _, live, material, commands = arrange(tmp_path)
    before = live.read_bytes()
    assert required(commands, 'prune')()['success'] is True
    assert not (tmp_path / 'backups').exists()
    assert_live(live, material, before)


def test_grant_metadata_is_not_a_restorable_backup_listing_entry(tmp_path):
    _, _, _, commands = arrange(tmp_path)
    archive = tmp_path / 'backups' / OLD_B
    database(archive, 'protected')
    consumer_grant(archive)
    assert [row['name'] for row in commands.list()] == [OLD_B], (
        'retention metadata was advertised as a backup')


def admitted_decision(archive):
    raw = json.loads((FIXTURES / 'grant.json').read_text())['authorization']
    raw['backup_name'] = archive.name
    raw['sha256'] = hashlib.sha256(archive.read_bytes()).hexdigest()
    return raw


def retain(commands, archive, decision, *, request='fixture-retain-1', until=DEADLINE):
    method = required(commands, 'retain')
    # Authority is a test-owned resolution seam, not an arbitrary approval string.
    # The CLI must provide its genuine recorded-source owner at this boundary.
    def resolve(reference):
        assert reference == 'fixture:user-decision:approved'
        return decision
    return method(archive.name, valid_until=until,
                  expected_digest=hashlib.sha256(archive.read_bytes()).hexdigest(),
                  authorization_ref='fixture:user-decision:approved',
                  request_id=request, authority=resolve)


def test_retain_producer_persists_fixed_binding_and_exact_retry_never_extends(tmp_path):
    config, live, material, commands = arrange(tmp_path)
    archive = tmp_path / 'backups' / OLD_B
    database(archive, 'needed')
    decision = admitted_decision(archive)
    archive_bytes = archive.read_bytes()
    archive_digest = hashlib.sha256(archive_bytes).hexdigest()
    before = live.read_bytes()
    retain(commands, archive, decision)
    assert_protected_archive(archive, archive_bytes)
    sidecar = archive.with_name(archive.name + '.retention.json')
    persisted = sidecar.read_bytes()
    binding = json.loads(persisted)
    assert binding['backup_name'] == OLD_B
    assert binding['sha256'] == archive_digest
    assert_fixed_deadline(sidecar)
    later = BackupCommands(LocalTaskDatabaseBackups(config), clock=lambda: NOW + timedelta(hours=1))
    retain(later, archive, decision)
    assert_protected_archive(archive, archive_bytes)
    assert_fixed_deadline(sidecar)
    assert sidecar.read_bytes() == persisted
    with pytest.raises(PoiseError, match='conflict|request|decision|deadline'):
        retain(later, archive, decision, until=DEADLINE + timedelta(hours=1))
    assert sidecar.read_bytes() == persisted
    assert_protected_archive(archive, archive_bytes)
    assert_live(live, material, before)


def test_authority_oracles_reject_age_only_deletion_and_retry_extension_and_restore(tmp_path):
    _, _, _, _ = arrange(tmp_path)
    archive = tmp_path / 'backups' / OLD_B
    database(archive, 'protected')
    sidecar = consumer_grant(archive)
    original_archive, original_grant = archive.read_bytes(), sidecar.read_bytes()
    assert_protected_archive(archive, original_archive)
    assert_fixed_deadline(sidecar)
    archive.unlink()
    with pytest.raises(AssertionError, match='age-only cleanup'):
        assert_protected_archive(archive, original_archive)
    archive.write_bytes(original_archive)
    for wrong_payload in (b'', b'changed but still present'):
        archive.write_bytes(wrong_payload)
        with pytest.raises(AssertionError, match='payload was changed'):
            assert_protected_archive(archive, original_archive)
        archive.write_bytes(original_archive)
        assert_protected_archive(archive, original_archive)
    wrong = json.loads(original_grant)
    wrong['valid_until'] = '2026-11-16T01:00:00+00:00'
    sidecar.write_text(json.dumps(wrong))
    with pytest.raises(AssertionError, match='extended'):
        assert_fixed_deadline(sidecar)
    sidecar.write_bytes(original_grant)
    assert_protected_archive(archive, original_archive)
    assert_fixed_deadline(sidecar)
    assert archive.read_bytes() == original_archive and sidecar.read_bytes() == original_grant


def test_intact_grant_does_not_authorize_changed_archive_bytes(tmp_path):
    _, live, material, commands = arrange(tmp_path)
    archive = tmp_path / 'backups' / OLD_B
    database(archive, 'original admitted bytes')
    sidecar = consumer_grant(archive)
    grant_bytes = sidecar.read_bytes()
    replacement = tmp_path / 'replacement.sqlite'
    database(replacement, 'different valid database')
    archive.write_bytes(replacement.read_bytes())
    current_bytes = archive.read_bytes()
    assert hashlib.sha256(current_bytes).hexdigest() != json.loads(grant_bytes)['sha256']
    live_bytes = live.read_bytes()
    result = required(commands, 'prune')()
    assert_unbound_archive_rejected(archive, current_bytes, result)
    assert sidecar.read_bytes() == grant_bytes  # metadata and its input checksum were never edited
    assert_live(live, material, live_bytes)


def test_binding_oracle_rejects_metadata_only_consumer_and_restores_control(tmp_path):
    _, _, _, _ = arrange(tmp_path)
    archive = tmp_path / 'backups' / OLD_B
    database(archive, 'admitted')
    sidecar = consumer_grant(archive)
    grant_bytes, original = sidecar.read_bytes(), archive.read_bytes()
    replacement = tmp_path / 'replacement.sqlite'
    database(replacement, 'changed valid SQLite')
    archive.write_bytes(replacement.read_bytes())
    current = archive.read_bytes()
    wrong = {'success': True, 'rejected': []}  # intact metadata accepted without checking its archive
    with pytest.raises(AssertionError, match='not bound'):
        assert_unbound_archive_rejected(archive, current, wrong)
    corrected_observation = json.loads((FIXTURES / 'binding_rejection.json').read_text())
    corrected_observation['rejected'][0]['name'] = OLD_B
    assert_unbound_archive_rejected(archive, current, corrected_observation)
    archive.write_bytes(original)
    assert archive.read_bytes() == original and sidecar.read_bytes() == grant_bytes


@pytest.mark.parametrize('kind', ['missing', 'agent-only', 'wrong-project',
                                 'wrong-backup', 'wrong-digest', 'wrong-deadline'])
def test_retain_requires_real_exact_admitted_authority(tmp_path, kind):
    _, live, material, commands = arrange(tmp_path)
    archive = tmp_path / 'backups' / OLD_B
    database(archive, 'needed')
    decision = admitted_decision(archive)
    if kind == 'missing':
        decision = None
    elif kind == 'agent-only':
        decision['role'] = 'assistant'
    elif kind == 'wrong-backup':
        decision['backup_name'] = OLD_A
    elif kind == 'wrong-project':
        decision['project'] = 'another-project'
    elif kind == 'wrong-deadline':
        decision['valid_until'] = '2026-11-15T01:00:00+00:00'
    else:
        decision['sha256'] = '0' * 64
    before = live.read_bytes()
    required(commands, 'retain')  # missing capability is the intended RED, not AttributeError
    with pytest.raises(PoiseError, match='author|decision|scope|digest|permission|match'):
        retain(commands, archive, decision)
    assert not archive.with_name(archive.name + '.retention.json').exists()
    assert archive.exists()
    assert_live(live, material, before)


def test_interrupted_grant_publication_resumes_same_intent_without_extending(tmp_path, monkeypatch):
    config, live, material, commands = arrange(tmp_path)
    archive = tmp_path / 'backups' / OLD_B
    database(archive, 'needed')
    decision = admitted_decision(archive)
    before = live.read_bytes()
    required(commands, 'retain')
    original = os.replace

    def interrupt_metadata(source, destination, *args, **kwargs):
        if str(destination).endswith('.retention.json'):
            raise OSError('fixture publication interrupted')
        return original(source, destination, *args, **kwargs)

    with monkeypatch.context() as control:
        control.setattr(os, 'replace', interrupt_metadata)
        with pytest.raises(PoiseError, match='publish|pending|retention|interrupted'):
            retain(commands, archive, decision)
    assert archive.exists()
    assert not archive.with_name(archive.name + '.retention.json').exists()
    pending = list(archive.parent.glob('*.pending'))
    assert pending, 'interrupted grant lost the owner-resumable intent'
    blocked = required(commands, 'prune')()
    assert blocked['success'] is False and archive.exists()
    later = BackupCommands(LocalTaskDatabaseBackups(config), clock=lambda: NOW + timedelta(hours=1))
    retain(later, archive, decision)
    sidecar = archive.with_name(archive.name + '.retention.json')
    assert_fixed_deadline(sidecar)
    assert not list(archive.parent.glob('*.pending'))
    assert_live(live, material, before)
    assert os.replace is original


def test_retain_retry_after_archive_removal_never_recreates_effective_grant(tmp_path):
    _, live, material, commands = arrange(tmp_path)
    archive = tmp_path / 'backups' / OLD_B
    database(archive, 'needed')
    decision = admitted_decision(archive)
    required(commands, 'retain')
    retain(commands, archive, decision)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    archive.unlink()  # controlled disposable absent-object input, not live cleanup
    before = live.read_bytes()
    with pytest.raises(PoiseError, match='absent|missing|not found'):
        commands.retain(archive.name, valid_until=DEADLINE, expected_digest=digest,
                        authorization_ref='fixture:user-decision:approved',
                        request_id='fixture-retain-1', authority=lambda ref: decision)
    assert not archive.exists()
    assert_live(live, material, before)


def test_bundle_expiry_preserves_live_database_and_project_materials(tmp_path):
    config, live, material, _ = arrange(tmp_path)
    manifest = json.loads(config.read_text())
    manifest['paths']['task_materials'] = str(tmp_path / 'user-materials')
    config.write_text(json.dumps(manifest))
    namespace = tmp_path / 'user-materials' / hashlib.sha256(b'fixture').hexdigest()
    namespace.mkdir(parents=True)
    attachment = namespace / 'attachment.bin'
    attachment.write_bytes(b'\x00\xfflive attachment')
    old_clock = datetime(2026, 11, 13, tzinfo=timezone.utc)
    # Existing create is arrangement, not the prune subject or expected-name oracle.
    creator = BackupCommands(LocalTaskDatabaseBackups(config), clock=lambda: old_clock)
    created = creator.create()
    assert created['name'] == 'tasks-backup-20261113T000000000000Z.zip'
    archive = tmp_path / 'backups' / created['name']
    commands = BackupCommands(LocalTaskDatabaseBackups(config), clock=lambda: NOW)
    before = live.read_bytes()
    assert required(commands, 'prune')()['success'] is True
    assert not archive.exists()
    assert attachment.read_bytes() == b'\x00\xfflive attachment'
    assert_live(live, material, before)


def test_prune_preserves_archive_while_restore_is_using_it(tmp_path, monkeypatch):
    config, live, _, commands = arrange(tmp_path)
    prune = required(commands, 'prune')
    archive = tmp_path / 'backups' / OLD_A
    database(archive, 'RESTORED')
    entered, release_restore, prune_done = threading.Event(), threading.Event(), threading.Event()
    port = LocalTaskDatabaseBackups(config)
    original = port._snapshot

    def hold_snapshot(source, destination):
        if source == archive:
            entered.set()
            assert release_restore.wait(5), 'fixture restore release timed out'
        return original(source, destination)

    monkeypatch.setattr(port, '_snapshot', hold_snapshot)
    restore = BackupCommands(port, clock=lambda: NOW)
    errors = []

    def restoring():
        try:
            restore.restore(OLD_A)
        except BaseException as error:
            errors.append(error)

    def pruning():
        try:
            prune()
        except PoiseError:
            pass  # A bounded busy refusal is legitimate; deleting in-use data is not.
        except BaseException as error:
            errors.append(error)
        finally:
            prune_done.set()

    restore_thread = threading.Thread(target=restoring)
    prune_thread = threading.Thread(target=pruning)
    restore_thread.start()
    try:
        assert entered.wait(5), 'fixture did not reach the real restore snapshot boundary'
        prune_thread.start()
        prune_done.wait(0.15)
        assert archive.exists(), 'prune deleted a backup while restore still consumed it'
    finally:
        release_restore.set()
        restore_thread.join(5)
        if prune_thread.ident is not None:
            prune_thread.join(5)
    assert not restore_thread.is_alive() and not prune_thread.is_alive()
    assert not errors
    with sqlite3.connect(f'file:{live}?mode=ro', uri=True) as db:
        assert db.execute('SELECT value FROM marker').fetchall() == [('RESTORED',)]
