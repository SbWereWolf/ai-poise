"""Real Task owners determine the response location before/after retirement."""
import io
import json
from pathlib import Path

import pytest

from batch.helpers import request
from poise.application.work import WorkTools
from poise.common import PoiseError
from poise.interfaces.work import execute
from result_integration.helpers import integration_input
from terminal_materials.archive_oracle import no_new_archive, proof_cells
from terminal_materials.helpers import PROOF, agree, arrange, declaration, file_output


def delivered(runtime, packet):
    output = io.StringIO()
    code = execute(runtime, io.BytesIO(json.dumps(packet).encode()), output)
    text = output.getvalue()
    assert len(text) <= runtime.cfg['limits']['output_chars'] and text.endswith('\n')
    view = json.loads(text)
    response = Path(view['response_path'])
    return code, view, response, json.loads(response.read_text())


def test_live_task_response_uses_its_actual_owner_root(project):
    tools, root, _, _ = arrange(project)
    code, view, path, full = delivered(tools.runtime, request('show', {'queries': [
        {'id': 'task', 'kind': 'task'}]}))
    assert code == 0 and full['status'] == 'read_only'
    assert path.is_relative_to(root / tools.runtime.paths['runs'])
    assert full['results'][0]['value']['task'] == 'T1'
    assert root.is_dir() and tools.runtime.task_queries.record('T1')['status'] == 'verified'


@pytest.mark.parametrize('operation', ['integrate', 'cleanup'])
def test_terminal_operation_saves_runtime_response_without_resurrection(project, operation):
    tools, root, source, commit = arrange(project)
    agree(tools, declaration([file_output(source, project['root'] / 'result.txt')]))
    before = proof_cells(tools, (PROOF,))
    if operation == 'integrate':
        assert tools.invoke(request('accept', {}))['status'] == 'completed'
        packet = request('integrate', integration_input(project, commit))
    else:
        assert tools.invoke(request('cancel', {'reason': 'Cancel the fixture Task.'}))['status'] == 'cancelled'
        packet = request('cleanup', {
            'task_id': 'T1', 'request_id': 'response-destination-cleanup',
            'authorization': 'Discard the exact fixture commit after cancellation.',
            'commit_disposition': {'kind': 'discard_authorized', 'expected_commit': commit},
        })
    code, view, path, full = delivered(tools.runtime, packet)
    assert code == 0
    assert full['status'] == ('integrated' if operation == 'integrate' else 'cleanup_complete')
    assert path.is_relative_to(tools.runtime.runtime / tools.runtime.paths['runs'])
    assert not root.exists()
    assert not path.is_relative_to(root)
    assert (project['root'] / 'result.txt').read_bytes() == b'customer result\n'
    no_new_archive(tools, before, (PROOF,))


def test_preflight_covers_terminal_destination_before_nonterminal_effect(project, monkeypatch):
    tools, root, source, _ = arrange(project, session='response-' + 'long' * 35)
    agree(tools, declaration([file_output(source, project['root'] / 'result.txt')]))
    runtime = tools.runtime
    predicted = root / runtime.paths['runs'] / '00000000-0000-4000-8000-000000000000' / runtime.paths['response']
    current_minimum = {'status': 'content_requirements_failed', 'response_path': str(predicted)}
    runtime.cfg['limits']['output_chars'] = len(
        json.dumps(current_minimum, ensure_ascii=False, separators=(',', ':')) + '\n')
    terminal_minimum = {'status': 'cancelled', 'response_path': str(
        runtime.runtime / runtime.paths['runs'] / '00000000-0000-4000-8000-000000000000' / runtime.paths['response'])}
    assert len(json.dumps(terminal_minimum, ensure_ascii=False, separators=(',', ':')) + '\n') > runtime.cfg['limits']['output_chars']
    before = runtime.task_queries.record('T1')
    calls = []
    original = WorkTools.invoke

    def invoke(owner, packet):
        calls.append(packet)
        return original(owner, packet)

    monkeypatch.setattr(WorkTools, 'invoke', invoke)
    output = io.StringIO()
    try:
        code = execute(runtime, io.BytesIO(json.dumps(request('cancel', {
            'reason': 'Refuse before cancelling when the actual destination cannot fit.'})).encode()), output)
    except PoiseError:
        code = 2
    after = runtime.task_queries.record('T1')
    assert calls == [] and code == 2
    assert after['status'] == 'verified' and after['_version'] == before['_version']
    assert root.is_dir()
    assert runtime.current_task()['id'] == 'T1'
