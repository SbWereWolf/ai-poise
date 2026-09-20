"""Real Git checks must prove the published commit, not just an equal tree."""
from pathlib import Path
import sys

import pytest

from batch.helpers import request, result, verify
from conftest import git
from poise.common import PoiseError
from runtime_services import test_failed_check_rework as scenario


def candidate(project, monkeypatch, command=None):
    original = scenario.bootstrap
    if command is None:
        command = "import subprocess; print(subprocess.check_output(['git','rev-parse','HEAD'], text=True).strip())"

    def configured(tools, fixture):
        fixture['task']['methods'][0]['argv'] = [sys.executable, '-c', command]
        return original(tools, fixture)

    monkeypatch.setattr(scenario, 'bootstrap', configured)
    tools, context = scenario._scenario(project)
    workspace = Path(tools.runtime._task()['worktree'])
    (workspace / 'src' / 'commit-proof.py').write_text('VALUE = 1\n')
    return tools, context, workspace


def new_hash(workspace):
    git(workspace, 'commit', '--allow-empty', '-m', 'test: new commit identity')


def test_checks_run_on_the_commit_that_is_published(project, monkeypatch):
    tools, context, workspace = candidate(project, monkeypatch)
    payload = result(context)
    report = verify(tools, payload)
    assert report['status'] == 'verified'
    receipt = report['checks'][0]
    observed = Path(receipt['stdout']).read_text().strip()
    assert observed == report['commit'] == git(workspace, 'rev-parse', 'HEAD')
    assert receipt['commit'] == report['commit']
    replay = verify(tools, payload)
    assert replay['replayed'] is True
    assert replay['checks'] == report['checks']
    assert git(workspace, 'rev-parse', 'HEAD') == report['commit']


def test_same_tree_new_commit_requires_fresh_failed_observation(project, monkeypatch):
    tools, context, workspace = candidate(project, monkeypatch, 'raise SystemExit(1)')
    payload = result(context)
    first = verify(tools, payload)
    assert first['status'] == 'checks_failed'
    old_tree = git(workspace, 'rev-parse', 'HEAD^{tree}')
    new_hash(workspace)
    assert git(workspace, 'rev-parse', 'HEAD^{tree}') == old_tree
    second = verify(tools, payload)
    assert second['status'] == 'checks_failed'
    assert first['checks'][0]['id'] != second['checks'][0]['id']
    assert first['checks'][0]['commit'] != second['checks'][0]['commit']


@pytest.mark.parametrize('operation', ['verify', 'accept', 'continue', 'advance'])
def test_new_hash_after_verified_cannot_replay_accept_or_advance(project, monkeypatch, operation):
    tools, context, workspace = candidate(project, monkeypatch)
    payload = result(context)
    report = verify(tools, payload)
    assert report['status'] == 'verified'
    before = tools.runtime._task()
    new_hash(workspace)
    with pytest.raises(PoiseError, match='[Cc]ommit|коммит|[Cc]ode'):
        if operation == 'verify':
            verify(tools, payload)
        elif operation == 'accept':
            tools.runtime.accept()
        elif operation == 'continue':
            tools.invoke(request('bootstrap', {
                'task': None, 'decision': 'continue', 'feedback': None, 'rework_stage': None,
            }))
        else:
            tools.runtime.advance('proof-stale', before['id'], 'test_remediation')
    after = tools.runtime._task()
    assert after['status'] == before['status']
    assert after['last_report'] == before['last_report']


def test_check_cannot_change_head_and_publish_its_old_observation(project, monkeypatch):
    command = "import subprocess; subprocess.check_call(['git','commit','--allow-empty','-m','test: changed inside check'])"
    tools, context, workspace = candidate(project, monkeypatch, command)
    with pytest.raises(PoiseError, match='[Cc]ommit|коммит'):
        verify(tools, result(context))
    assert tools.runtime._task()['status'] == 'active'
    assert tools.runtime._task()['last_report'] is None


def test_uncommitted_change_after_proof_cannot_be_accepted(project, monkeypatch):
    tools, context, workspace = candidate(project, monkeypatch)
    assert verify(tools, result(context))['status'] == 'verified'
    (workspace / 'src' / 'commit-proof.py').write_text('VALUE = 2\n')
    with pytest.raises(PoiseError, match='[Cc]ode changed'):
        tools.runtime.accept()
    assert tools.runtime._task()['status'] == 'verified'


def test_hook_changed_tree_is_not_called_tested_and_retry_can_prove_it(project, monkeypatch):
    tools, context, workspace = candidate(project, monkeypatch)
    common = Path(git(workspace, 'rev-parse', '--path-format=absolute', '--git-common-dir'))
    hook = common / 'hooks' / 'pre-commit'
    hook.write_text("#!/bin/sh\nprintf 'VALUE = 2\\n' > src/commit-proof.py\ngit add src/commit-proof.py\n")
    hook.chmod(0o755)
    calls = []
    run = tools.runtime.check_runner.run
    def counted(*args, **kwargs):
        calls.append(args[0])
        return run(*args, **kwargs)
    monkeypatch.setattr(tools.runtime.check_runner, 'run', counted)
    with pytest.raises(PoiseError, match='[Cc]ommit or code changed'):
        verify(tools, result(context))
    assert calls == []
    assert tools.runtime._task()['pending'] is None
    assert (workspace / 'src' / 'commit-proof.py').read_text() == 'VALUE = 2\n'
    hook.unlink()  # Remove only this test's injected hook, never task files.
    report = verify(tools, result(context))
    assert report['status'] == 'verified'
    assert len(calls) == 1
    assert report['checks'][0]['commit'] == report['commit']


def test_ack_loss_after_candidate_commit_does_not_create_an_extra_commit(project, monkeypatch):
    tools, context, workspace = candidate(project, monkeypatch)
    original = tools.runtime._git
    committed = []
    def lost_ack(cwd, *args, **kwargs):
        value = original(cwd, *args, **kwargs)
        if args[0] == 'commit' and not committed:
            committed.append(original(cwd, 'rev-parse', 'HEAD'))
            raise OSError('candidate commit acknowledgement lost')
        return value
    monkeypatch.setattr(tools.runtime, '_git', lost_ack)
    with pytest.raises(OSError, match='acknowledgement lost'):
        verify(tools, result(context))
    assert tools.runtime._task()['attempts'] == 0
    report = verify(tools, result(context))
    assert report['status'] == 'verified'
    assert report['checks'][0]['commit'] == committed[0] == report['commit']


def test_old_verified_report_cannot_invent_commit_provenance(project, monkeypatch):
    tools, context, workspace = candidate(project, monkeypatch)
    report = verify(tools, result(context))
    current = tools.runtime._task()
    current['last_report'].pop('verification_commit')
    tools.runtime.store.save(current)  # Model an existing stored report, not a production migration.
    with pytest.raises(PoiseError, match='Legacy report.*restart'):
        tools.runtime.accept()
    assert git(workspace, 'rev-parse', 'HEAD') == report['commit']


def test_new_commit_after_failed_check_can_restart_without_manual_state_repair(project, monkeypatch):
    from runtime_services.test_task_restart import restart
    tools, context, workspace = candidate(project, monkeypatch, 'raise SystemExit(1)')
    first = verify(tools, result(context))
    assert first['status'] == 'checks_failed'
    new_hash(workspace)
    previous = tools.runtime._task()
    newborn = restart(tools, previous['id'], previous['version'],
                      authorization='User approved restarting and changing this local verification method.')
    assert newborn['status'] == 'newborn'
    assert tools.runtime._task()['pending'] is None
    assert (workspace / 'src' / 'commit-proof.py').read_text() == 'VALUE = 1\n'
    assert tools.runtime.evidence_commands.list_for(previous['id'])[0]['id'] == first['checks'][0]['id']
