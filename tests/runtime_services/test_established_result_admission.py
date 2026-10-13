"""Actual consumer establishment precedes actual-path output admission."""
import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from batch.helpers import request
from conftest import write_json
from hook_transport.helpers import event, install, settings as hook_settings
from poise.application.work import WorkTools
from poise.common import PoiseError
from poise.infrastructure.hook_transport import HookService
from poise.infrastructure.runtime_adapter import RuntimeAdapter
from poise.interfaces import hook_transport, runtime_adapter
from runtime_services.test_adapter import boot, packet, settings as adapter_settings


def taskless():
    return request('bootstrap', {'task': None, 'decision': None,
                                'feedback': None, 'rework_stage': None})


def selected_budget(runtime, enough):
    structural = {'status': 'content_requirements_failed', 'response_path': ''}
    actual = {**structural, 'response_path': str(
        runtime.runtime / runtime.paths['runs'] /
        '00000000-0000-4000-8000-000000000000' / runtime.paths['response'])}
    size = lambda value: len(json.dumps(value, ensure_ascii=False,
                                       separators=(',', ':')) + '\n')
    budget = 2200 if enough else size(structural) + 1
    assert size(structural) < budget
    assert (size(actual) <= budget) is enough
    return budget


def observe_work(monkeypatch):
    calls = []
    original = WorkTools.invoke

    def invoke(owner, value):
        calls.append(value)
        return original(owner, value)

    monkeypatch.setattr(WorkTools, 'invoke', invoke)
    return calls


def attempt(action):
    try:
        return action()
    except PoiseError:
        # An incapable error envelope can reach the outer CLI boundary too.
        return 2


def full_response(output, budget):
    text = output.getvalue()
    assert text.endswith('\n') and len(text) <= budget
    view = json.loads(text)
    path = Path(view['response_path'])
    assert path.is_file()
    full = json.loads(path.read_text())
    assert full['status'] == view['status']
    return path, full


@pytest.mark.parametrize('enough', [False, True])
def test_real_runtime_adapter_admits_established_address_before_task_creation(
        project, tmp_path, monkeypatch, enough):
    config = adapter_settings(project, tmp_path)
    settings_path = write_json(tmp_path / 'adapter-settings.json', config)
    discovered = RuntimeAdapter(config)
    discovery = discovered.invoke(packet(taskless()))
    runtime = discovered.runtime
    assert runtime.task_queries.record('T1') is None
    budget = selected_budget(runtime, enough)
    project['cfg']['limits']['output_chars'] = budget
    write_json(project['config_path'], project['cfg'])
    calls = observe_work(monkeypatch)
    output, error = io.StringIO(), io.StringIO()
    code = attempt(lambda: runtime_adapter.execute(
        settings_path, io.BytesIO(json.dumps(packet(boot(project))).encode()), output, error))
    if not enough:
        assert calls == [] and code == 2
        assert runtime.task_queries.record('T1') is None
        assert runtime.current_task() is None
        assert not (runtime.state / runtime.paths['standalone_tasks'] / 'T1').exists()
    else:
        assert len(calls) == 1 and code == 0 and error.getvalue() == ''
        path, full = full_response(output, budget)
        task = runtime.task_queries.record('T1')
        assert task is not None and full['task'] == 'T1'
        assert full['runtime_adapter']['session_id'] == discovery['runtime_adapter']['session_id']
        assert task['claimed_by'] == discovery['runtime_adapter']['session_id']
        assert path.is_relative_to(runtime.state / runtime.paths['standalone_tasks'] /
                                   'T1' / runtime.paths['runs'])


@pytest.mark.parametrize('enough', [False, True])
def test_real_hook_work_admits_established_address_before_task_creation(
        project, tmp_path, monkeypatch, enough):
    settings_path = hook_settings(project, tmp_path)
    service = HookService(settings_path)
    installed = install(service)
    native = event(session='budget-native')
    native['cwd'] = str(project['root'])
    service.event(installed['definition_path'], native)
    binding = service.latest_binding('budget-native', 'primary')
    # Explicit simulated host observation belongs only to this existing fixture.
    for name in ('CODEX_SESSION_ID', 'POISE_CALLER_BINDING'):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv('CODEX_THREAD_ID', 'budget-native')
    discovery = service.work(binding['binding_path'], taskless())
    runtime = service.runtime
    assert discovery['hook_session'] == binding['session_id'] == runtime.session
    assert runtime.task_queries.record('T1') is None
    budget = selected_budget(runtime, enough)
    project['cfg']['limits']['output_chars'] = budget
    write_json(project['config_path'], project['cfg'])
    calls = observe_work(monkeypatch)
    output, error = io.StringIO(), io.StringIO()
    code = attempt(lambda: hook_transport.execute(
        'hook-work', SimpleNamespace(settings=settings_path, binding=binding['binding_path']),
        io.BytesIO(json.dumps(boot(project)).encode()), output, error))
    if not enough:
        assert calls == [] and code == service.settings.raw['exit_codes']['rejected']
        assert runtime.task_queries.record('T1') is None
        assert runtime.current_task() is None
        assert not (runtime.state / runtime.paths['standalone_tasks'] / 'T1').exists()
    else:
        assert len(calls) == 1 and code == service.settings.raw['exit_codes']['success']
        assert error.getvalue() == ''
        path, full = full_response(output, budget)
        task = runtime.task_queries.record('T1')
        assert task is not None and full['task'] == 'T1'
        assert full['hook_session'] == binding['session_id'] == task['claimed_by']
        assert path.is_relative_to(runtime.state / runtime.paths['standalone_tasks'] /
                                   'T1' / runtime.paths['runs'])
