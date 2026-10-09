"""Terminal receipt presentation through real storage and the public CLI adapter."""
from copy import deepcopy
import io
import json
from pathlib import Path
import sys

import pytest

from batch.helpers import request
from conftest import WorkPoise, git
from poise.common import PoiseError
from poise.interfaces.work import execute, write_result
from result_integration.helpers import (
    integration_input, prepare_completed_task, source_change,
)
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


def notice(value):
    text = json.dumps(value, ensure_ascii=False)
    assert DESTROYED in text, 'Terminal presentation must report destroyed service logs'
    assert 'Служебные материалы' in text and 'удалены' in text, (
        'Terminal presentation must report deleted service materials')


def integration_arrangement(project, failed=False):
    counter = project['root'] / 'receipt-executions.txt'
    method = {
        'id': 'RECEIPT_PROBE',
        'argv': [sys.executable, '-B', '-c',
                 (Path(__file__).parent / 'fixtures/integration_receipt_probe.py').read_text()],
        'cwd': '.', 'environment': {'RECEIPT_COUNTER': str(counter)},
        'source_under_test': {'kind': 'repository', 'bindings': [{'kind': 'cwd', 'path': '.'}]},
        'expected_exit_code': 0, 'stdout_contains': ['receipt probe'], 'stderr_contains': [],
    }
    tools, worktree, commit = prepare_completed_task(
        project, source_change, methods=[method], checks=['RECEIPT_PROBE'])
    if failed:
        (project['app'] / 'reject-integration').write_text('fixture rejection\n')
        git(project['app'], 'add', 'reject-integration')
        git(project['app'], 'commit', '-m', 'Arrange a failing integration probe')
    packet = request('integrate', integration_input(project, commit))
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
        notice(brief)
        assert 'T1' in task_ids(brief), 'Short historical response must identify its Task'
        notice(full)


def test_receipt_owner_resolves_stage_and_integration_provenance(project):
    tools, worktree, counter, packet = integration_arrangement(project)
    stage_receipts = tools.runtime.evidence_commands.list_for('T1')
    assert len(stage_receipts) == 1
    done = tools.invoke(packet)
    assert done['status'] == 'integrated' and len(done['checks']) == 1
    stage = stage_receipts[0]
    integration = done['checks'][0]
    assert stage['id'] != integration['id']
    assert tools.runtime.task_queries.receipt_task(stage['id']) == 'T1'
    assert tools.runtime.task_queries.receipt_task(integration['id']) == 'T1', (
        'Integration receipt provenance must resolve through the result owner')


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
    budget = len(json.dumps(minimal, ensure_ascii=False, separators=(',', ':')) + '\n') + 100
    runtime.cfg['limits']['output_chars'] = budget
    query = request('show', {'queries': [{
        'id': 'historical', 'kind': 'integration', 'task_id': 'T1', 'request_id': 'integrate-1'}]})
    code, raw, brief, full = display(runtime, query)
    assert code == 0 and len(raw) <= budget
    assert brief.get('command_views', []) == []
    assert full['results'][0]['value']['checks'] == done['checks']
    notice(brief)
    assert 'T1' in task_ids(brief), 'Short historical response must identify its Task'
    notice(full)
