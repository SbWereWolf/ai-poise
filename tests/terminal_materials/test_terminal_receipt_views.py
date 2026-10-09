"""Terminal receipt presentation through real storage and the public CLI adapter."""
from copy import deepcopy
import io
import json
from pathlib import Path
import sys

import pytest

from batch.helpers import request
from conftest import WorkPoise, git
from poise.application.work import WorkTools
from poise.common import PoiseError
from poise.interfaces.work import execute, write_result
from result_integration.helpers import (
    integration_input, prepare_completed_task, source_change,
)
from sprints.helpers import setup, task
from .helpers import agree, declaration, file_output, finish
from .test_retired_evidence import checked


DESTROYED = 'Служебные журналы уничтожены'


def display(runtime, packet):
    output = io.StringIO()
    code = execute(runtime, io.BytesIO(json.dumps(packet).encode()), output)
    raw = output.getvalue()
    brief = json.loads(raw)
    full = json.loads(Path(brief['response_path']).read_text(encoding='utf-8'))
    return code, raw, brief, full


def task_ids(value):
    if isinstance(value, dict):
        return ([value['task']] if isinstance(value.get('task'), str) else []) + [
            item for child in value.values() for item in task_ids(child)]
    if isinstance(value, list):
        return [item for child in value for item in task_ids(child)]
    return []


def historical_outcome(value, receipt_id, method, exit_code, passed):
    """Accept existing receipt records at any public JSON container depth."""
    records = []
    def collect(item):
        if isinstance(item, dict):
            if item.get('id') == receipt_id:
                records.append(item)
            for child in item.values():
                collect(child)
        elif isinstance(item, list):
            for child in item:
                collect(child)
    collect(value)
    assert any(row.get('method') == method
               and row.get('actual_exit_code') == exit_code
               and row.get('passed') is passed for row in records), (
        'Historical receipt identity and outcome must remain visible')


def notice(value):
    text = json.dumps(value, ensure_ascii=False)
    assert DESTROYED in text, 'Terminal presentation must report destroyed service logs'
    assert 'Служебные материалы' in text and 'удалены' in text, (
        'Terminal presentation must report deleted service materials')


def integration_arrangement(project, failed=False, identifier='T1'):
    counter = project['root'] / (identifier + '-receipt-executions.txt')
    method = {
        'id': 'RECEIPT_PROBE',
        'argv': [sys.executable, '-B', '-c',
                 (Path(__file__).parent / 'fixtures/integration_receipt_probe.py').read_text()],
        'cwd': '.', 'environment': {'RECEIPT_COUNTER': str(counter)},
        'source_under_test': {'kind': 'repository', 'bindings': [{'kind': 'cwd', 'path': '.'}]},
        'expected_exit_code': 0, 'stdout_contains': ['receipt probe'], 'stderr_contains': [],
    }
    change = source_change if identifier == 'T1' else lambda tree: (
        tree / 'src/feature.py').write_text('VALUE = 2\n')
    tools, worktree, commit = prepare_completed_task(
        project, change, task_id=identifier, methods=[method], checks=['RECEIPT_PROBE'])
    if failed:
        (project['app'] / 'reject-integration').write_text('fixture rejection\n')
        git(project['app'], 'add', 'reject-integration')
        git(project['app'], 'commit', '-m', 'Arrange a failing integration probe')
    packet = request('integrate', integration_input(project, commit, task_id=identifier))
    return tools, worktree, counter, packet


@pytest.mark.parametrize('status', ['completed', 'cancelled'])
@pytest.mark.parametrize('removed', [False, True], ids=['retained', 'removed'])
def test_stage_receipts_report_terminal_materials_on_first_and_repeat(project, status, removed):
    tools, root, source, commit, receipt, _ = checked(project)
    if removed:
        agree(tools, declaration([file_output(source, project['root'] / 'result.txt')]))
        finish(project, tools, commit, status)
        assert not root.exists()
    else:
        operation = request('accept', {}) if status == 'completed' else request(
            'cancel', {'reason': 'Keep fixture logs for terminal presentation.'})
        assert tools.invoke(operation)['status'] == status
        assert Path(receipt['stdout']).is_file()
    reader = WorkPoise(project['config_path'], 'terminal-stage-reader')
    packet = request('bootstrap', {
        'task': {'id': 'T1'}, 'decision': None, 'feedback': None, 'rework_stage': None})
    observations = [display(reader, packet), display(reader, packet)]
    for code, raw, brief, full in observations:
        assert code == 0 and full['status'] == status and full['task'] == 'T1'
        record = next(row for row in full['evidence']['records'] if row['id'] == receipt['id'])
        assert record['method'] == 'CHECK'
        assert record['actual_exit_code'] == 0 and record['passed'] is True
        historical_outcome(brief, receipt['id'], 'CHECK', 0, True)
        notice(brief)
        assert 'T1' in task_ids(brief), 'Short historical response must identify its Task'
        notice(full)
    if removed:
        assert not root.exists(), 'Historical display must preserve the retired Task location'


def test_integration_first_display_and_exact_replay_keep_history_and_notice(project):
    tools, worktree, counter, packet = integration_arrangement(project)
    observations = [display(tools.runtime, packet), display(tools.runtime, packet)]
    assert counter.read_text().splitlines() == ['executed', 'executed']
    # One source verification and one actual integration verification; exact replay is historical.
    fulls = [item[3] for item in observations]
    assert fulls[0]['checks'] == fulls[1]['checks']
    assert fulls[1]['replayed'] is True
    assert len(fulls[0]['checks']) == 1
    for code, raw, brief, full in observations:
        assert code == 0 and full['status'] == 'integrated' and full['task'] == 'T1'
        receipt = full['checks'][0]
        assert receipt['method'] == 'RECEIPT_PROBE'
        assert receipt['actual_exit_code'] == 0 and receipt['passed'] is True
        historical_outcome(brief, receipt['id'], 'RECEIPT_PROBE', 0, True)
        notice(brief)
        assert 'T1' in task_ids(brief), 'Short historical response must identify its Task'
        notice(full)


def test_receipt_owner_resolves_stage_and_integration_provenance(project):
    owners = []
    for identifier in ('T1', 'T2'):
        tools, worktree, counter, packet = integration_arrangement(project, identifier=identifier)
        stage_receipts = tools.runtime.evidence_commands.list_for(identifier)
        assert len(stage_receipts) == 1
        done = tools.invoke(packet)
        assert done['status'] == 'integrated' and len(done['checks']) == 1
        assert done['task'] == identifier
        owners.append((stage_receipts[0], done['checks'][0], identifier))
    first_stage, first_integration, _ = owners[0]
    second_stage, second_integration, _ = owners[1]
    assert len({first_stage['id'], first_integration['id'],
                second_stage['id'], second_integration['id']}) == 4
    expected = [(first_stage['id'], 'T1'), (second_stage['id'], 'T2'),
                (first_integration['id'], 'T1'), (second_integration['id'], 'T2')]
    for receipt_id, identifier in expected:
        assert tools.runtime.task_queries.receipt_task(receipt_id) == identifier, (
            'Receipt provenance must identify its actual Task')


def test_failed_historical_integration_receipt_keeps_outcome_in_repeated_query(project):
    tools, worktree, counter, packet = integration_arrangement(project, failed=True)
    failed = tools.invoke(packet)
    assert failed['phase'] == 'checks_failed'
    original = deepcopy(failed['checks'][0])
    assert original['actual_exit_code'] == 7 and original['passed'] is False
    before = counter.read_text()
    query = request('show', {'queries': [{
        'id': 'historical', 'kind': 'integration', 'task_id': 'T1', 'request_id': 'integrate-1'}]})
    reader = WorkPoise(project['config_path'], 'historical-integration-reader')
    observations = [display(reader, query), display(reader, query)]
    assert counter.read_text() == before
    for code, raw, brief, full in observations:
        assert code == 0
        stored = full['results'][0]['value']
        assert stored['task'] == 'T1' and stored['checks'][0] == original
        historical_outcome(brief, original['id'], 'RECEIPT_PROBE', 7, False)
        notice(brief)
        assert 'T1' in task_ids(brief), 'Short historical response must identify its Task'
        notice(full)


def test_receipt_state_read_failure_is_explicit_in_saved_and_short_response(project, monkeypatch):
    tools, worktree, counter, packet = integration_arrangement(project)
    done = tools.invoke(packet)
    receipt = done['checks'][0]
    runtime = WorkPoise(project['config_path'], 'state-failure-reader')
    def unavailable(identifier):
        raise PoiseError('Receipt state unavailable')
    monkeypatch.setattr(runtime.task_queries, 'record', unavailable)
    output = io.StringIO()
    write_result(runtime, {'status': 'read_only', 'checks': [receipt], 'detail': 'x' * 20000}, output)
    brief = json.loads(output.getvalue())
    full = json.loads(Path(brief['response_path']).read_text())
    for value in (brief, full):
        assert 'Receipt state unavailable' in json.dumps(value), (
            'Receipt state lookup failure must be displayed explicitly')


def test_bounded_terminal_notice_survives_omission_of_all_command_views(project):
    tools, worktree, counter, packet = integration_arrangement(project)
    done = tools.invoke(packet)
    runtime = WorkPoise(project['config_path'], 'bounded-terminal-reader')
    predicted = runtime.runtime / runtime.paths['runs'] / ('0' * 36) / runtime.paths['response']
    minimal = {'status': 'read_only', 'response_path': str(predicted)}
    # Space for notice, Task and one minimal historical outcome, independently of container names.
    budget = len(json.dumps(minimal, ensure_ascii=False, separators=(',', ':')) + '\n') + 350
    runtime.cfg['limits']['output_chars'] = budget
    query = request('show', {'queries': [{
        'id': 'historical', 'kind': 'integration', 'task_id': 'T1', 'request_id': 'integrate-1'}]})
    code, raw, brief, full = display(runtime, query)
    assert code == 0 and len(raw) <= budget
    assert brief.get('command_views', []) == []
    assert full['results'][0]['value']['checks'] == done['checks']
    historical_outcome(brief, done['checks'][0]['id'], 'RECEIPT_PROBE', 0, True)
    notice(brief)
    assert 'T1' in task_ids(brief), 'Short historical response must identify its Task'
    notice(full)


def test_failed_ordinary_stage_receipt_keeps_failure_on_first_and_repeated_display(project):
    setup(project)
    tools = WorkTools(WorkPoise(project['config_path'], 'failed-stage-owner'))
    contract = task(project, 'FAILED', command="print('failed ordinary proof');raise SystemExit(7)")
    contract['sprint_id'] = None
    context = tools.invoke(request('bootstrap', {
        'task': contract, 'decision': None, 'feedback': None, 'rework_stage': None}))
    worktree = Path(context['worktree'])
    (worktree / 'src/delivered.py').write_text('VALUE = 7\n')
    result = deepcopy(context['result_template'])
    result['sections']['report'] = 'A real failed ordinary check is retained.'
    result['commit_message'] = 'Observe an ordinary verification failure'
    failed = tools.invoke(request('verify', {'result': result, 'artifacts': []}))
    assert failed['status'] == 'checks_failed'
    receipts = tools.runtime.evidence_commands.list_for('FAILED')
    assert len(receipts) == 1
    receipt = receipts[0]
    assert receipt['method'] == 'CHECK'
    assert receipt['actual_exit_code'] == 7 and receipt['passed'] is False
    cancelled = tools.invoke(request('cancel', {'reason': 'Retain a failed check as terminal history.'}))
    assert cancelled['status'] == 'cancelled'
    reader = WorkPoise(project['config_path'], 'failed-stage-reader')
    packet = request('bootstrap', {
        'task': {'id': 'FAILED'}, 'decision': None, 'feedback': None, 'rework_stage': None})
    observations = [display(reader, packet), display(reader, packet)]
    for code, raw, brief, full in observations:
        assert code == 0 and full['task'] == 'FAILED' and full['status'] == 'cancelled'
        for value in (brief, full):
            historical_outcome(value, receipt['id'], 'CHECK', 7, False)
            assert 'FAILED' in task_ids(value)
            notice(value)
