"""R1-R3: public Task/worktree preparation, not an orchestration mock."""
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys

import pytest
from batch.helpers import request
from conftest import WorkPoise, git, write_json
from poise.application.work import WorkTools
from poise.infrastructure.artifact_factory import FileArtifactFactory
from poise.interfaces.work import execute
from runtime_services.worktree_preparation_support import (
    FIXTURES, arrange, assert_registered_materials, boot, events, material_bytes,
    operations, read_json, receipt, registered, set_decision, task_row, use_environment_handler,
)


@pytest.mark.parametrize('scope', ['standalone', 'sprint'])
def test_new_task_prepares_physical_worktree_in_order(project, monkeypatch, scope):
    case = arrange(project, monkeypatch)
    if scope == 'sprint':
        from sprints.helpers import publish_existing_contract
        case['project']['task'] = publish_existing_contract(case['runtime'], project['task'], 'S')
    response = boot(case)
    root = Path(response['worktree'])
    assert root.is_dir()
    assert git(root, 'rev-parse', 'HEAD') == case['base']
    assert operations(case), 'P004_PREPARATION_NOT_INVOKED'
    assert operations(case) == ['infra', 'deps', 'check']
    for row in events(case):
        assert row['cwd'] == row['git_root'] == row['workspace_argument'] == str(root)
        assert row['task'] == project['task']['id']
        assert row['project'] == 'demo'
        assert row['branch'] == 'tasks/T1'
        assert row['head'] == case['base']
    report = receipt(response)
    assert report['status'] == response['preparation']['status'] == 'ready'
    assert [item['name'] for item in report['operations']] == ['infra', 'deps', 'check']


def test_external_fixture_observes_real_inputs_and_full_streams(project, monkeypatch):
    case = arrange(project, monkeypatch)
    root = project['app']
    (root / 'selected').mkdir()
    (root / 'selected/input.txt').write_bytes(case['source'].read_bytes())
    command = [str(FIXTURES / 'command.py'), 'check', str(case['events']), str(case['control']),
               str(root), 'T1', 'demo', 'literal Ω $VALUE with spaces']
    result = subprocess.run([sys.executable, *command],
                            cwd=root, capture_output=True, check=False)
    assert result.returncode == 0
    assert json.loads(result.stdout)['payload'] == 'full stream Ω value\n' * 256
    assert result.stderr.decode() == 'full diagnostic π\n' * 256
    row, = events(case)
    assert row['input'] == 'selected input Ω\n'
    assert row['label'] == 'literal Ω $VALUE with spaces'
    assert row['git_root'] == str(root)


def test_declared_inputs_are_published_before_first_operation(project, monkeypatch):
    case = arrange(project, monkeypatch)
    response = boot(case)
    root = Path(response['worktree'])
    assert (root / 'selected/input.txt').read_bytes() == b'selected input \xce\xa9\n'
    assert [row['input'] for row in events(case)] == ['selected input Ω\n'] * 3
    report = receipt(response)
    entry, = report['inputs']
    assert entry['source'] == str(case['source'])
    assert entry['destination'] == str(root / 'selected/input.txt')
    assert entry['sha256'] == hashlib.sha256(b'selected input \xce\xa9\n').hexdigest()


@pytest.mark.parametrize('failed,expected', [
    ('infra', ['infra']), ('deps', ['infra', 'deps']), ('check', ['infra', 'deps', 'check']),
])
def test_failure_preserves_task_workspace_and_complete_receipt(project, monkeypatch, failed, expected):
    case = arrange(project, monkeypatch)
    set_decision(case, failed)
    response = boot(case)
    assert response['status'] == 'action_failed'
    assert response['preparation']['status'] == 'error'
    assert response['preparation']['reason']
    assert operations(case) == expected
    root = Path(response['worktree'])
    assert root.is_dir() and git(root, 'symbolic-ref', '--short', 'HEAD') == 'tasks/T1'
    assert task_row(case)[0] == 'T1'
    assert task_row(case)[1] == response['task_status']
    assert task_row(case)[1] not in ('cancelled', 'completed')
    report = receipt(response)
    assert_registered_materials(case, report)
    command = report['operations'][-1]['commands'][0]
    assert command['returncode'] == 17
    assert json.loads(Path(command['stdout_path']).read_bytes())['payload'] == 'full stream Ω value\n' * 256
    assert Path(command['stderr_path']).read_text() == 'full diagnostic π\n' * 256
    assert dict(registered(case))[response['preparation']['receipt_path']] == response['preparation']['receipt_sha256']


def test_new_attempt_reads_changed_bytes_and_retains_prior_failure(project, monkeypatch):
    case = arrange(project, monkeypatch)
    set_decision(case, 'deps')
    first = boot(case)
    first_report = receipt(first)
    before = material_bytes(case)
    root = Path(first['worktree'])
    (root / 'retained Ω.txt').write_text('user work', encoding='utf-8')
    config = read_json(case['configuration'])
    config['parameters']['label'] = 'new Ω $LITERAL value'
    write_json(case['configuration'], config)
    set_decision(case, 'deps', status='ready', code=0)
    second = boot(case, again=True, request_id='fixed-1')
    second_report = receipt(second)
    assert second['worktree'] == first['worktree']
    assert second['preparation']['status'] == 'ready'
    assert second_report['attempt_id'] != first_report['attempt_id']
    assert second_report['configuration']['path'] == first_report['configuration']['path']
    assert second_report['configuration']['sha256'] != first_report['configuration']['sha256']
    assert operations(case) == ['infra', 'deps', 'infra', 'deps', 'check']
    assert all(row['label'] == 'new Ω $LITERAL value' for row in events(case)[2:])
    assert (root / 'retained Ω.txt').read_text() == 'user work'
    assert all(Path(path).read_bytes() == content for path, content in before.items())


@pytest.mark.parametrize('outcome', ['ready', 'error'])
def test_completed_request_replay_does_not_run_again(project, monkeypatch, outcome):
    case = arrange(project, monkeypatch)
    initial = boot(case)
    if outcome == 'error':
        set_decision(case, 'infra')
    first = boot(case, again=True, request_id='completed-1')
    assert first['preparation']['status'] == outcome
    before = material_bytes(case), events(case), task_row(case)
    config = read_json(case['configuration'])
    config['parameters']['label'] = 'unused changed config'
    write_json(case['configuration'], config)
    again = boot(case, again=True, request_id='completed-1')
    assert again['preparation'] == first['preparation']
    assert (material_bytes(case), events(case), task_row(case)) == before
    assert again['worktree'] == initial['worktree']


def test_handoff_resume_is_not_a_new_preparation_attempt(project, monkeypatch):
    case = arrange(project, monkeypatch)
    initial = boot(case)
    before = events(case)
    handoff = case['tools'].invoke(request('handoff', read_json(FIXTURES / 'handoff-input.json')))
    assert handoff['status'] == 'handed_off'
    other = WorkTools(WorkPoise(project['config_path'], 'P004-REVIEWER'))
    resumed = boot(case, again=True, tools=other)
    assert resumed['preparation'] == initial['preparation']
    assert resumed['worktree'] == initial['worktree']
    assert events(case) == before


@pytest.mark.parametrize('handler,marker,status', [
    ('accept', 'literal Ω $VALUE with spaces', 'fixture-accepted'),
    ('alternate', 'second-handler', 'another-success-vocabulary'),
])
def test_selected_handler_is_replaceable_and_receives_complete_process(project, monkeypatch, handler, marker, status):
    case = arrange(project, monkeypatch)
    config = read_json(case['configuration'])
    config['handler']['callable'] = 'worktree_handlers:' + handler
    write_json(case['configuration'], config)
    response = boot(case)
    assert response['preparation']['status'] == 'ready'
    report = receipt(response)
    assert_registered_materials(case, report)
    for operation in report['operations']:
        result = operation['result']
        assert result['marker'] == marker and result['status'] == status
        assert result['options']['marker'] == 'literal Ω $VALUE with spaces'
        assert result['options']['owner'] == 'T1'
        assert result['options']['workspace'] == response['worktree']
        assert result['context']['operation'] == operation['name']
        if handler == 'accept':
            assert json.loads(result['stdout'])['payload'] == 'full stream Ω value\n' * 256
            assert result['stderr'] == 'full diagnostic π\n' * 256


@pytest.mark.parametrize('mode', ['unknown', 'second-error'])
def test_unknown_or_second_error_returns_without_outer_repair(project, monkeypatch, mode):
    case = arrange(project, monkeypatch)
    use_environment_handler(case)
    set_decision(case, 'infra', malformed=mode == 'unknown')
    if mode == 'second-error':
        set_decision(case, 'verification')
    response = boot(case)
    assert response['status'] == 'action_failed'
    assert response['preparation']['status'] == 'error'
    assert operations(case) == (['infra'] if mode == 'unknown' else ['infra', 'repair', 'verification'])
    report = receipt(response)
    assert_registered_materials(case, report)
    assert len(report['operations']) == 1
    assert len(report['operations'][0]['commands']) == (1 if mode == 'unknown' else 3)
    assert report['reason']


@pytest.mark.parametrize('missing', ['configuration', 'source'])
def test_missing_selected_input_is_an_explained_preserving_failure(project, monkeypatch, missing):
    case = arrange(project, monkeypatch)
    path = case[missing]
    original = path.read_bytes()
    path.unlink()
    response = boot(case)
    assert response['status'] == 'action_failed'
    assert response['preparation']['status'] == 'error'
    assert str(path) in response['preparation']['reason']
    assert Path(response['worktree']).is_dir()
    assert operations(case) == []
    path.write_bytes(original)
    fixed = boot(case, again=True, request_id='fixed-input')
    assert fixed['worktree'] == response['worktree']
    assert fixed['preparation']['status'] == 'ready'


@pytest.mark.parametrize('worktree_required', [True, False])
def test_unconfigured_project_keeps_existing_bootstrap(project, monkeypatch, worktree_required):
    case = arrange(project, monkeypatch)
    project['cfg'].pop('worktree_preparation')
    write_json(project['config_path'], project['cfg'])
    if not worktree_required:
        workflow = deepcopy(project['workflow'])
        workflow['worktree_required'] = False
        write_json(project['root'] / 'config/workflows/development.json', workflow)
    tools = WorkTools(WorkPoise(project['config_path'], 'P004-NO-PREPARATION'))
    response = boot(case, tools=tools)
    assert 'preparation' not in response
    assert operations(case) == []
    if worktree_required:
        assert Path(response['worktree']).is_dir()
    else:
        listed = git(project['app'], 'worktree', 'list', '--porcelain')
        assert listed.count('worktree ') == 1


@pytest.mark.parametrize('failure', [False, True])
def test_work_interface_reports_preparation_failure_as_incomplete(project, monkeypatch, failure):
    case = arrange(project, monkeypatch)
    if failure:
        set_decision(case, 'infra')
    body = read_json(FIXTURES / 'bootstrap-input.json')
    body['task'] = deepcopy(project['task'])
    output = io.StringIO()
    code = execute(case['runtime'], io.BytesIO(json.dumps(request('bootstrap', body)).encode()), output)
    envelope = json.loads(output.getvalue())
    full = read_json(envelope['response_path'])
    assert code == (1 if failure else 0)
    assert full['status'] == ('action_failed' if failure else 'active')
    assert full['preparation']['status'] == ('error' if failure else 'ready')
    assert receipt(full)['task_id'] == 'T1'
    assert len(events(case)) == (1 if failure else 3)


@pytest.mark.parametrize('continuation', ['same', 'new'])
@pytest.mark.parametrize('materials', ['complete', 'unavailable'])
def test_unconfirmed_receipt_is_observed_before_same_or_new_request(project, monkeypatch, request, continuation, materials):
    case = arrange(project, monkeypatch)
    original = FileArtifactFactory.materialize
    fault = {'enabled': True, 'path': None, 'hits': 0}

    def publication(owner, prepared):
        for item in prepared:
            try:
                value = json.loads(item.content)
            except (ValueError, UnicodeError):
                continue
            if isinstance(value, dict) and value.get('schema') == 'poise/worktree-preparation/v1':
                if fault['path'] is None:
                    fault['path'] = str(item.path)
                if fault['enabled']:
                    fault['hits'] += 1
                    raise OSError('P004_FINAL_RECEIPT_PUBLICATION_UNCONFIRMED')
        return original(owner, prepared)

    monkeypatch.setattr(FileArtifactFactory, 'materialize', publication)
    first = boot(case)
    assert first['status'] == 'action_failed'
    assert first['preparation']['status'] == 'unconfirmed'
    assert first['preparation']['receipt_path'] is None
    assert first['preparation']['receipt_sha256'] is None
    assert fault['hits'] >= 1 and fault['path']
    assert operations(case) == ['infra', 'deps', 'check']
    root = Path(first['worktree'])
    assert (root / 'effect.txt').read_text() == 'effect:check'
    captures = material_bytes(case)
    # Registered original full command materials survive even without final receipt.
    assert sum(b'full stream' in content for content in captures.values()) >= 3
    assert fault['path'] not in dict(registered(case))
    before = task_row(case)
    for request_id in (None, 'new-after-unknown'):
        unresolved = boot(case, again=True, request_id=request_id)
        assert unresolved['preparation']['status'] == 'unconfirmed'
        assert unresolved['preparation']['attempt_id'] == first['preparation']['attempt_id']
        assert operations(case) == ['infra', 'deps', 'check']
        assert task_row(case) == before
    fault['enabled'] = False
    read_bytes = Path.read_bytes
    blocked_path = next(path for path, content in captures.items() if b'full stream' in content)
    if materials == 'unavailable':
        blocked = Path(blocked_path)
        original_mode = blocked.stat().st_mode & 0o777
        request.addfinalizer(lambda: blocked.chmod(original_mode))
        blocked.chmod(0)
        with pytest.raises(PermissionError):
            blocked.read_bytes()
    popen_init = subprocess.Popen.__init__
    reconciled_before_launch = []

    def observe_launch(process, args, *more, **kwargs):
        if isinstance(args, (list, tuple)) and str(FIXTURES / 'command.py') in list(map(str, args)):
            assert fault['path'] in dict(registered(case))
            assert read_json(fault['path'])['attempt_id'] == first['preparation']['attempt_id']
            reconciled_before_launch.append(True)
        return popen_init(process, args, *more, **kwargs)

    monkeypatch.setattr(subprocess.Popen, '__init__', observe_launch)
    final = boot(case, again=True, request_id=None if continuation == 'same' else 'new-after-unknown')
    assert final['worktree'] == first['worktree']
    if materials == 'unavailable':
        assert final['preparation']['status'] == 'unconfirmed'
        assert operations(case) == ['infra', 'deps', 'check']
        assert final['preparation']['reason']
    else:
        assert final['preparation']['status'] == 'ready'
        assert Path(fault['path']).is_file()
        assert fault['path'] in dict(registered(case))
        assert operations(case) == ['infra', 'deps', 'check'] * (1 if continuation == 'same' else 2)
        assert len(reconciled_before_launch) == (0 if continuation == 'same' else 3)
        assert all(read_bytes(Path(path)) == content for path, content in captures.items())
