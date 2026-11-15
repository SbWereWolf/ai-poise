"""Real external entries, disposable manifests, and supplied timer declarations."""
from __future__ import annotations

import configparser
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from backups.test_retention_lifecycle import FIXTURES, OLD_A, OLD_B, arrange, database


ROOT = Path(__file__).resolve().parents[2]


def cli(*arguments):
    result = subprocess.run([sys.executable, '-B', '-m', 'poise', 'backup', *arguments],
                            cwd=ROOT, env={**os.environ, 'PYTHONPATH': str(ROOT / 'src')},
                            capture_output=True, text=True, timeout=15)
    assert 'ModuleNotFoundError' not in result.stderr
    assert 'Traceback' not in result.stderr
    return result


def test_loaded_backup_cli_exposes_prune_and_removes_only_selected_project_expiry(tmp_path):
    config, live, material, _ = arrange(tmp_path / 'selected')
    other_config, other_live, _, _ = arrange(tmp_path / 'other')
    selected = config.parent / 'backups' / OLD_A
    other = other_config.parent / 'backups' / OLD_A
    database(selected, 'expired selected')
    database(other, 'other project')
    before, other_before = live.read_bytes(), other_live.read_bytes()
    result = cli('prune', '--config', str(config))
    assert result.returncode == 0, 'loaded backup CLI lacks prune capability'
    payload = json.loads(result.stdout)
    assert payload['success'] is True
    assert not selected.exists()
    assert other.exists() and other_live.read_bytes() == other_before
    assert live.read_bytes() == before and material.read_bytes() == b'live task material'


def test_loaded_backup_cli_remove_is_exact_and_replay_is_noop(tmp_path):
    config, live, _, _ = arrange(tmp_path)
    selected, other = tmp_path / 'backups' / OLD_A, tmp_path / 'backups' / OLD_B
    database(selected, 'remove')
    database(other, 'keep')
    before = live.read_bytes()
    for _ in range(2):
        result = cli('remove', '--config', str(config), OLD_A)
        assert result.returncode == 0, 'loaded backup CLI lacks exact removal capability'
        assert json.loads(result.stdout)['success'] is True
        assert not selected.exists() and other.exists()
        assert live.read_bytes() == before


def test_retain_entry_refuses_missing_real_authority_without_creating_grant(project):
    case = recorded_authority_case(project)
    config, archive, live = case['config'], case['archive'], case['live']
    help_result = cli('help')
    assert help_result.returncode == 0
    assert 'retain' in help_result.stdout, 'loaded backup CLI lacks retain capability'
    before = live.read_bytes()
    archive_before = archive.read_bytes()
    result = cli('retain', '--config', str(config), archive.name,
                 '--until', case['deadline'].isoformat(),
                 '--authorization-ref', 'task-feedback:T1:00000000-0000-0000-0000-000000000000',
                 '--expected-digest', case['digest'], '--request-id', 'fixture-denied')
    assert result.returncode != 0
    payload = json.loads(result.stdout)
    assert payload['status'] == 'rejected'
    assert payload['reason']
    assert any(word in payload['reason'].lower() for word in ('author', 'decision', 'source')), (
        'retention refusal did not address the authority source')
    assert not archive.with_name(archive.name + '.retention.json').exists()
    assert archive.read_bytes() == archive_before and live.read_bytes() == before


def test_cli_negative_oracle_rejects_digest_only_refusal_with_valid_archive_input(project, monkeypatch):
    from backups.helpers import backup_directory
    original = cli
    observed = []

    def digest_only(*arguments):
        observed.append(arguments[0])
        if arguments[0] == 'help':
            return subprocess.CompletedProcess(['fixture'], 0, 'backup retain', '')
        name = arguments[arguments.index('--config') + 2]
        correct = hashlib.sha256((backup_directory(project) / name).read_bytes()).hexdigest()
        assert arguments[arguments.index('--expected-digest') + 1] == correct
        payload = json.dumps({'status': 'rejected', 'reason': 'digest mismatch'})
        return subprocess.CompletedProcess(['fixture'], 2, payload, '')

    with monkeypatch.context() as control:
        control.setattr(sys.modules[__name__], 'cli', digest_only)
        with pytest.raises(AssertionError, match='did not address the authority source'):
            test_retain_entry_refuses_missing_real_authority_without_creating_grant(project)
    assert observed == ['help', 'retain'] and cli is original


def recorded_authority_case(project):
    # Reuse the actual existing source owner, in a disposable configured project.
    # These are sandbox Task/API actors, not a native caller/event override.
    from task_feedback.helpers import append, arrange as arrange_task, client, configure_case, read
    from backups.helpers import backup_directory, live_database
    from poise.application.backups import BackupCommands
    from poise.infrastructure.backups import LocalTaskDatabaseBackups

    configure_case(project)
    arrange_task(project, state='newborn')
    config = project['config_path']
    created = BackupCommands(LocalTaskDatabaseBackups(config), clock=lambda: datetime(
        2020, 1, 1, tzinfo=timezone.utc)).create()
    assert created['name'].endswith('-backup-20200101T000000000000Z.zip')
    archive = backup_directory(project) / created['name']
    original = archive.read_bytes()
    digest = hashlib.sha256(original).hexdigest()
    ordinary = archive.with_name(archive.name.replace('T000000000000Z', 'T010000000000Z'))
    ordinary.write_bytes(original)
    deadline = datetime.now(timezone.utc) + timedelta(hours=1)
    decision = json.loads((FIXTURES / 'cli_authorization.json').read_text())
    decision.update(backup_name=archive.name, sha256=digest, valid_until=deadline.isoformat())
    text = json.dumps(decision, sort_keys=True)
    source_owner = client(project, 'fixture-user-decision')
    saved = append(source_owner, request_id='fixture-user-retention-decision', text=text)
    comment = saved['comments'][0]
    reread = read(source_owner)['comments']
    assert any(item['id'] == comment['id'] and item['text'] == text
               and item['author'] == 'fixture-user-decision' for item in reread)
    # Address a committed actual Task material, never a raw caller-made approval file.
    reference = 'task-feedback:T1:' + comment['id']
    live = live_database(project)
    return {'config': config, 'archive': archive, 'ordinary': ordinary,
            'original': original, 'digest': digest, 'deadline': deadline,
            'reference': reference, 'decision': decision, 'live': live}


def test_real_cli_retain_resolves_recorded_task_authority_and_prune_consumes_grant(project):
    case = recorded_authority_case(project)
    config, archive, live = case['config'], case['archive'], case['live']
    deadline, digest, reference = case['deadline'], case['digest'], case['reference']
    original, ordinary = case['original'], case['ordinary']
    live_before = live.read_bytes()
    result = cli('retain', '--config', str(config), archive.name,
                 '--until', deadline.isoformat(), '--authorization-ref', reference,
                 '--expected-digest', digest, '--request-id', 'fixture-cli-retain')
    assert result.returncode == 0, 'loaded retain CLI did not admit the recorded exact decision'
    assert json.loads(result.stdout)['success'] is True
    sidecar = archive.with_name(archive.name + '.retention.json')
    binding = json.loads(sidecar.read_text())
    assert binding['project'] == 'demo' and binding['backup_name'] == archive.name
    assert binding['sha256'] == digest
    assert datetime.fromisoformat(binding['valid_until'].replace('Z', '+00:00')) == deadline
    assert binding['authorization']['reference'] == reference
    assert binding['authorization']['text'] == case['decision']['text']
    assert archive.read_bytes() == original
    pruned = cli('prune', '--config', str(config))
    assert pruned.returncode == 0
    outcome = json.loads(pruned.stdout)
    assert outcome['success'] is True
    assert not ordinary.exists()
    assert archive.read_bytes() == original and sidecar.is_file()
    assert any(item['name'] == archive.name and item['reason'] == 'retained_by_user_decision'
               for item in outcome['retained'])
    assert live.read_bytes() == live_before


def test_supplied_standard_timer_invokes_same_owner_with_bounded_cadence():
    directory = ROOT / 'config/maintenance'
    service = directory / 'backup-retention.service.example'
    timer = directory / 'backup-retention.timer.example'
    assert service.is_file() and timer.is_file(), 'retention deployment declarations are absent'
    unit = configparser.ConfigParser(interpolation=None)
    schedule = configparser.ConfigParser(interpolation=None)
    unit.read(service)
    schedule.read(timer)
    assert unit['Service']['Type'] == 'oneshot'
    command = unit['Service']['ExecStart']
    assert 'backup prune' in command and '--config' in command
    assert '/worktrees/' not in command  # retained supply, not disposable Task source
    assert schedule['Timer']['Persistent'].lower() == 'true'
    assert schedule['Timer']['OnCalendar'] in ('*:0/15', '*-*-* *:00/15:00')
    assert schedule['Timer']['Unit'] == 'backup-retention.service'
