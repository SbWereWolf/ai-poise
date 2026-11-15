"""Owned temporary lifecycle, evidence preservation and addressed cleanup."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from poise.common import PoiseError
from poise.infrastructure.test_package_cache import AiPoiseTestPackageCache
from verification.disk_retention_support import FIXTURES, REPOSITORY, TerminalRunner, tiny_checkout


def arrange(tmp_path, **outcome):
    root, catalog = tiny_checkout(tmp_path)
    runner = TerminalRunner(**outcome)
    return root, runner, AiPoiseTestPackageCache(root, catalog, runner=runner)


def assert_primary(root, result):
    directory = root / '.poise-test-cache/evidence' / result['run_id']
    assert (directory / 'stdout.log').read_bytes() == b'fixture stdout\n'
    assert (directory / 'stderr.log').read_bytes() == b'fixture stderr\n'
    assert (directory / 'junit.xml').read_bytes() == (FIXTURES / 'terminal.xml').read_bytes()
    saved = json.loads((directory / 'result.json').read_text())
    assert saved['actual_exit_code'] == result['actual_exit_code']
    return directory


@pytest.mark.parametrize(('code', 'timeout', 'cancelled'), [
    (0, False, False), (1, False, False), (-9, True, False), (-15, False, True),
], ids=['success', 'failure', 'timeout', 'cancelled'])
def test_terminal_run_removes_only_pytest_and_preserves_primary(
        tmp_path, code, timeout, cancelled):
    root, runner, cache = arrange(tmp_path, code=code, timeout=timeout, cancelled=cancelled)
    sentinel = root / '.poise-test-cache/evidence/other-run/pytest/keep.txt'
    sentinel.parent.mkdir(parents=True)
    sentinel.write_bytes(b'another run')
    result = cache.run('verification-runner', timeout_seconds=1, fresh=True)
    directory = assert_primary(root, result)
    assert not (directory / 'pytest').exists(), 'terminal run retained disposable pytest tree'
    assert (result['actual_exit_code'], result['timed_out'], result['cancelled']) == (
        code, timeout, cancelled)
    assert sentinel.read_bytes() == b'another run'
    if code == 0:
        before = runner.calls
        reused = cache.run('verification-runner', timeout_seconds=1)
        assert reused['reused'] is True and reused['origin_run_id'] == result['run_id']
        assert runner.calls == before


@pytest.mark.parametrize(('child_reaped', 'group_stopped'), [(False, True), (True, False)],
                         ids=['child-unknown', 'group-unknown'])
def test_unknown_termination_preserves_tree_and_refuses_reusable_success(
        tmp_path, child_reaped, group_stopped):
    root, _, cache = arrange(tmp_path, child_reaped=child_reaped, group_stopped=group_stopped)
    result = cache.run('verification-runner', timeout_seconds=1, fresh=True)
    directory = assert_primary(root, result)
    assert (directory / 'pytest/work.txt').read_bytes() == b'disposable fixture\n'
    assert result['passed'] is False, 'unknown writers were advertised as reusable success'
    assert result['cache_record'] is None
    cleanup = result['temporary_cleanup']
    assert cleanup['status'] == 'blocked'
    assert cleanup['reason'] and cleanup['continuation']


def test_cleanup_failure_preserves_old_record_and_reports_actual_path(tmp_path, monkeypatch):
    root, _, cache = arrange(tmp_path)
    first = cache.run('verification-runner', timeout_seconds=1)
    record = root / first['cache_record']
    before = record.read_bytes()
    original = shutil.rmtree

    def deny_owned_tree(path, *args, **kwargs):
        if Path(path).name == 'pytest':
            raise PermissionError('fixture cleanup denied')
        return original(path, *args, **kwargs)

    with monkeypatch.context() as controlled:
        controlled.setattr(shutil, 'rmtree', deny_owned_tree)
        result = cache.run('verification-runner', timeout_seconds=1, fresh=True)
    directory = assert_primary(root, result)
    assert result['actual_exit_code'] == 0
    assert result['passed'] is False, 'cleanup failure was published as success'
    assert result['cache_record'] is None
    assert record.read_bytes() == before
    assert (directory / 'pytest/work.txt').exists()
    assert result['temporary_cleanup']['status'] == 'failed'
    assert 'pytest' in result['temporary_cleanup']['path']
    assert result['temporary_cleanup']['continuation']
    assert shutil.rmtree is original


def test_addressed_cleanup_is_exact_idempotent_and_preserves_reuse(tmp_path):
    root, _, cache = arrange(tmp_path)
    result = cache.run('verification-runner', timeout_seconds=1)
    directory = assert_primary(root, result)
    work = directory / 'pytest'
    work.mkdir(exist_ok=True)
    (work / 'historical.txt').write_bytes(b'obsolete')
    other = root / '.poise-test-cache/evidence/other/pytest/keep'
    other.parent.mkdir(parents=True)
    other.write_bytes(b'keep')
    cleanup = getattr(cache, 'cleanup_run', None)
    assert callable(cleanup), 'cache owner lacks addressed cleanup_run capability'
    cleanup(result['run_id'])
    assert not work.exists()
    cleanup(result['run_id'])
    assert not work.exists() and other.read_bytes() == b'keep'
    assert_primary(root, result)
    assert cache.run('verification-runner', timeout_seconds=1)['reused'] is True


def test_addressed_cleanup_refuses_unresolved_writers(tmp_path):
    root, _, cache = arrange(tmp_path, child_reaped=False, group_stopped=False)
    result = cache.run('verification-runner', timeout_seconds=1)
    cleanup = getattr(cache, 'cleanup_run', None)
    assert callable(cleanup), 'cache owner lacks addressed cleanup_run capability'
    with pytest.raises(PoiseError, match='termination|writer|outcome|stopp'):
        cleanup(result['run_id'])
    assert (assert_primary(root, result) / 'pytest/work.txt').exists()


def test_package_cli_exposes_addressed_cleanup_without_normal_run_arguments(tmp_path):
    root, _, cache = arrange(tmp_path)
    result = cache.run('verification-runner', timeout_seconds=1)
    work = root / '.poise-test-cache/evidence' / result['run_id'] / 'pytest'
    work.mkdir(exist_ok=True)
    environment = {**os.environ, 'PYTHONPATH': str(REPOSITORY / 'src')}
    command = [sys.executable, '-B', str(REPOSITORY / 'tools/run_ai_poise_test_package.py'),
               '--root', str(root), '--cleanup-run', result['run_id']]
    completed = subprocess.run(command, cwd=REPOSITORY, env=environment,
                               capture_output=True, text=True, timeout=10)
    assert 'ModuleNotFoundError' not in completed.stderr
    assert 'Traceback' not in completed.stderr
    assert completed.returncode == 0, 'loaded package CLI lacks addressed cleanup capability'
    payload = json.loads(completed.stdout)
    assert payload['run_id'] == result['run_id']
    assert not work.exists()
    assert_primary(root, result)
