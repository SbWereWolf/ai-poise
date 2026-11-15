"""Real external entries, disposable manifests, and supplied timer declarations."""
from __future__ import annotations

import configparser
import json
import os
from pathlib import Path
import subprocess
import sys

from backups.test_retention_lifecycle import OLD_A, OLD_B, arrange, database


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


def test_retain_entry_refuses_missing_real_authority_without_creating_grant(tmp_path):
    config, live, _, _ = arrange(tmp_path)
    archive = tmp_path / 'backups' / OLD_B
    database(archive, 'needed')
    help_result = cli('help')
    assert help_result.returncode == 0
    assert 'retain' in help_result.stdout, 'loaded backup CLI lacks retain capability'
    before = live.read_bytes()
    result = cli('retain', '--config', str(config), OLD_B,
                 '--until', '2026-11-17T00:00:00+00:00',
                 '--authorization-ref', 'fixture:missing-user-decision',
                 '--expected-digest', '0' * 64, '--request-id', 'fixture-denied')
    assert result.returncode != 0
    payload = json.loads(result.stdout)
    assert payload['status'] == 'rejected'
    assert payload['reason']
    assert not archive.with_name(archive.name + '.retention.json').exists()
    assert archive.exists() and live.read_bytes() == before


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
