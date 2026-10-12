"""Real persistence and adapter outcomes, independent of the summary algorithm."""
from copy import deepcopy
import io
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from poise.common import PoiseError
from poise.infrastructure.result_views import ResultViews
from poise.interfaces import catalogue, goal_config, hook_transport, runtime_adapter, work


ROOT = Path(__file__).resolve().parents[2]
FIXTURE = Path(__file__).with_name('fixtures') / 'history_15_boundary.json'


@pytest.fixture
def runtime(tmp_path):
    policy = json.loads((ROOT / 'config/runtime.example.json').read_text())['output']
    views = ResultViews(policy, lambda *args: None)
    events = []
    value = SimpleNamespace(
        cfg={'limits': {'output_chars': 2200},
             'batch': {'file_mode': 0o600, 'max_input_bytes': 20000, 'max_items': 100}},
        runtime=tmp_path, paths={'runs': 'runs', 'response': 'response.json'},
        report_task=lambda result: None, current_task=lambda: None,
        retired_receipt=lambda receipt: False,
        task_queries=SimpleNamespace(receipt_task=lambda identifier: 'T1'),
        store=SimpleNamespace(current=lambda session: None,
                              event=lambda *args: events.append(args)),
        session='bounded-transport', result_views=views, events=events,
    )
    yield value
    views.finish()


def read_response(output, expected_status, cap=2200):
    text = output.getvalue()
    assert text.endswith('\n') and len(text) <= cap
    brief = json.loads(text)
    assert brief['status'] == expected_status
    path = Path(brief['response_path'])
    assert path.is_file()
    return brief, json.loads(path.read_text())


def test_small_taskless_work_response_is_really_saved(runtime):
    payload = {'status': 'read_only', 'internal_detail': 'not a brief view'}
    before = deepcopy(payload)
    output = io.StringIO()
    work.write_result(runtime, payload, output)
    brief, full = read_response(output, 'read_only')
    assert full == before and payload == before
    assert 'internal_detail' not in brief


def test_small_hook_response_is_really_saved(tmp_path):
    payload = {'status': 'diagnosed', 'internal_detail': 'not a brief view'}
    service = SimpleNamespace(settings=SimpleNamespace(
        observations=tmp_path,
        raw={'output_chars': 2200, 'response_file': 'response.json', 'file_mode': 0o600}))
    output = io.StringIO()
    hook_transport._write(service, payload, output)
    brief, full = read_response(output, 'diagnosed')
    assert full == payload
    assert 'internal_detail' not in brief


def test_owner_errors_and_duplicate_receipts_remain_complete(runtime):
    raw = runtime.runtime / 'stdout.txt'
    raw.write_text('original command output\n')
    empty = runtime.runtime / 'stderr.txt'
    empty.write_text('')
    receipt = {'id': 'owner-missing', 'method': 'CHECK', 'actual_exit_code': 1,
               'passed': False, 'argv': ['fixture'], 'stdout': str(raw), 'stderr': str(empty),
               'stdout_digest': hashlib.sha256(raw.read_bytes()).hexdigest(),
               'stderr_digest': hashlib.sha256(empty.read_bytes()).hexdigest()}
    runtime.retired_receipt = lambda value: True
    runtime.task_queries.receipt_task = lambda identifier: None
    payload = {'status': 'checks_failed', 'checks': [receipt, deepcopy(receipt)]}
    before = deepcopy(payload)
    output = io.StringIO()
    work.write_result(runtime, payload, output)
    brief, full = read_response(output, 'checks_failed')
    assert full['checks'] == before['checks']
    assert full['presentation_errors'] == [
        {'id': 'owner-missing', 'presentation_error': 'Historical receipt owner unavailable'}]
    assert brief['summary']['presentation_errors'] == {'total': 1, 'shown': 0, 'omitted': 1}
    assert payload == before and raw.read_text() == 'original command output\n'


@pytest.mark.parametrize('outcome,expected_status,exit_code', [
    ('diagnosed', 'diagnosed', 7), ('blocked', 'blocked', 8), ('error', 'rejected', 9),
])
def test_runtime_transport_preserves_configured_exits(tmp_path, monkeypatch, outcome, expected_status, exit_code):
    settings = SimpleNamespace(observations=tmp_path, raw={
        'output_chars': 2200, 'response_file': 'response.json', 'file_mode': 0o600,
        'max_input_bytes': 20000, 'exit_codes': {'success': 7, 'incomplete': 8, 'rejected': 9}})

    def diagnose(packet):
        if outcome == 'error':
            raise PoiseError('diagnostic refused')
        return {'status': outcome, 'internal_detail': 'complete diagnostic'}

    monkeypatch.setattr(hook_transport, 'HookService',
                        lambda path: SimpleNamespace(settings=settings, diagnose=diagnose))
    output, error = io.StringIO(), io.StringIO()
    code = hook_transport.execute('runtime-config', SimpleNamespace(settings='fixture'),
        io.BytesIO(b'{"operation":"diagnose","input":{}}'), output, error)
    assert code == exit_code and error.getvalue() == ''
    brief, full = read_response(output, expected_status)
    assert full['status'] == expected_status
    assert 'internal_detail' not in brief


@pytest.mark.parametrize('status,code', [
    ('integrated', 0), ('checks_failed', 1), ('rejected', 0),
])
def test_work_post_effect_history_preserves_adapter_exit(runtime, monkeypatch, status, code):
    # A returned business status is not a thrown transport rejection: existing
    # Work policy deliberately returns success for a returned "rejected" result.
    payload = deepcopy(json.loads(FIXTURE.read_text())['result'])
    payload['status'] = status
    marker = runtime.runtime / 'effect.txt'
    calls = []

    def invoke(self, packet):
        calls.append(packet)
        marker.write_text('exactly one effect\n')
        return payload

    monkeypatch.setattr(work, 'WorkTools',
                        lambda value: SimpleNamespace(invoke=lambda packet: invoke(None, packet)))
    packet = {'operation': 'verify', 'input': {'result': None, 'artifacts': []}, 'messages': []}
    output = io.StringIO()
    observed = work.execute(runtime, io.BytesIO(json.dumps(packet).encode()), output)
    brief, full = read_response(output, status)
    assert observed == code
    assert len(calls) == 1 and marker.read_text() == 'exactly one effect\n'
    assert full == payload
    assert 'historical_receipts' not in brief


def test_work_thrown_rejection_preserves_exit_two(runtime, monkeypatch):
    def invoke(self, packet):
        raise PoiseError('operation refused')

    monkeypatch.setattr(work, 'WorkTools',
                        lambda value: SimpleNamespace(invoke=lambda packet: invoke(None, packet)))
    packet = {'operation': 'verify', 'input': {'result': None, 'artifacts': []}, 'messages': []}
    output = io.StringIO()
    assert work.execute(runtime, io.BytesIO(json.dumps(packet).encode()), output) == 2
    brief, full = read_response(output, 'rejected')
    assert full == {'status': 'rejected', 'reason': 'operation refused'}


def test_valid_work_budget_survives_history_growth_after_effect(runtime, monkeypatch):
    runtime.runtime = runtime.runtime / ('long-result-directory-' * 10)
    raw = runtime.runtime / 'stdout.txt'
    raw.parent.mkdir()
    raw.write_text('retired command data\n')
    err = raw.with_name('stderr.txt')
    err.write_text('')
    rows = json.loads(FIXTURE.read_text())['result']['historical_receipts'][0]['checks']
    receipts = [{**row, 'argv': ['fixture'], 'stdout': str(raw), 'stderr': str(err),
                 'stdout_digest': hashlib.sha256(raw.read_bytes()).hexdigest(),
                 'stderr_digest': hashlib.sha256(err.read_bytes()).hexdigest()} for row in rows]
    payload = {'status': 'integrated', 'checks': receipts}
    runtime.retired_receipt = lambda receipt: True
    calls = []

    def invoke(self, packet):
        calls.append(packet)
        return payload

    monkeypatch.setattr(work, 'WorkTools',
                        lambda value: SimpleNamespace(invoke=lambda packet: invoke(None, packet)))
    packet = {'operation': 'verify', 'input': {'result': None, 'artifacts': []}, 'messages': []}
    output = io.StringIO()
    assert work.execute(runtime, io.BytesIO(json.dumps(packet).encode()), output) == 0
    brief, full = read_response(output, 'integrated')
    assert len(calls) == 1 and full['checks'] == receipts
    assert full['historical_receipts'] == [{'task': 'T1', 'checks': rows}]
    legacy = {key: full[key] for key in ('status', 'notice', 'historical_receipts')}
    legacy['response_path'] = brief['response_path']
    assert len(json.dumps(legacy, ensure_ascii=False, separators=(',', ':')) + '\n') > 2200
    assert 'historical_receipts' not in brief


def editor_settings(tmp_path, cap):
    return SimpleNamespace(root=tmp_path, path=tmp_path / 'settings.json', raw={
        'output_chars': cap, 'responses': 'responses', 'file_mode': 0o600,
        'json_indent': 2, 'max_input_bytes': 20000,
        'exit_codes': {'success': 7, 'rejected': 9, 'pending': 11}})


@pytest.mark.parametrize('adapter', [
    'work', 'goal-config', 'catalogue', 'runtime-config', 'hook-work', 'runtime',
])
def test_impossible_budget_never_invokes_effect(tmp_path, runtime, monkeypatch, adapter):
    calls = []
    output, error = io.StringIO(), io.StringIO()
    payload = {'status': 'installed'}

    def effect(*args):
        calls.append(args)
        (tmp_path / 'unwanted-effect.txt').write_text('must not happen')
        return payload

    if adapter == 'work':
        runtime.cfg['limits']['output_chars'] = 1
        monkeypatch.setattr(work, 'WorkTools', lambda value: SimpleNamespace(invoke=effect))
        packet = {'operation': 'verify', 'input': {'result': None, 'artifacts': []}, 'messages': []}
        run = lambda: work.execute(runtime, io.BytesIO(json.dumps(packet).encode()), output)
    elif adapter == 'goal-config':
        settings = editor_settings(tmp_path, 1)
        monkeypatch.setattr(goal_config, 'EditorSettings', lambda path: settings)
        monkeypatch.setattr(goal_config, 'goal_config_tools', lambda path: SimpleNamespace(apply_batch=effect))
        run = lambda: goal_config.execute(settings.path, io.BytesIO(b'{"schema":"goal-config-batch-1"}'), output)
    elif adapter == 'catalogue':
        repository = SimpleNamespace(editor=editor_settings(tmp_path, 1), root=tmp_path, raw={'max_items': 10})
        monkeypatch.setattr(catalogue, 'FileCatalogue', lambda path: repository)
        monkeypatch.setattr(catalogue, 'goal_config_tools', lambda path: object())
        monkeypatch.setattr(catalogue, 'CatalogueCommands', lambda *args: SimpleNamespace(install=effect))
        run = lambda: catalogue.execute(tmp_path / 'catalogue.json',
            io.BytesIO(b'{"action":"install","items":[],"project_config":null}'), output)
    elif adapter in ('hook-work', 'runtime'):
        runtime.cfg['limits']['output_chars'] = 1
        packet = {'operation': 'verify', 'input': {'result': None, 'artifacts': []}, 'messages': []}
        if adapter == 'hook-work':
            settings = SimpleNamespace(observations=tmp_path, raw={
                'output_chars': 1, 'response_file': 'response.json', 'file_mode': 0o600,
                'max_input_bytes': 20000,
                'exit_codes': {'success': 7, 'incomplete': 8, 'rejected': 9}})
            service = SimpleNamespace(settings=settings, work=effect, runtime=runtime)
            monkeypatch.setattr(hook_transport, 'HookService', lambda path: service)
            run = lambda: hook_transport.execute('hook-work',
                SimpleNamespace(settings='fixture', binding='fixture-binding'),
                io.BytesIO(json.dumps(packet).encode()), output, error)
        else:
            monkeypatch.setattr(runtime_adapter, 'read_json',
                                lambda path: {'project_config': str(tmp_path / 'project.json')})
            monkeypatch.setattr(runtime_adapter, 'RuntimeAdapter',
                                lambda settings: SimpleNamespace(invoke=effect, runtime=runtime))
            monkeypatch.setattr(runtime_adapter, 'load_config', lambda path: (None, runtime.cfg, None))
            run = lambda: runtime_adapter.execute(tmp_path / 'runtime.json',
                io.BytesIO(json.dumps(packet).encode()), output, error)
    else:
        settings = SimpleNamespace(observations=tmp_path, project_config=tmp_path / 'project.json', raw={
            'output_chars': 1, 'response_file': 'response.json', 'file_mode': 0o600,
            'max_input_bytes': 20000, 'exit_codes': {'success': 7, 'incomplete': 8, 'rejected': 9}})
        payload['definition_path'] = 'fixture-definition'
        service = SimpleNamespace(settings=settings, install=effect,
                                  probes=lambda *args: {'ready': True})
        monkeypatch.setattr(hook_transport, 'HookService', lambda path: service)
        monkeypatch.setattr(hook_transport, 'load_config',
                            lambda path: (None, {'git': {'repository': str(tmp_path)}}, None))
        run = lambda: hook_transport.execute('runtime-config', SimpleNamespace(settings='fixture'),
            io.BytesIO(b'{"operation":"install","input":{}}'), output, error)
    # A budget incapable of expressing even an error may raise to the CLI's
    # error boundary. It must never execute the action to discover that fact.
    try:
        code = run()
    except PoiseError:
        code = 2
    assert calls == [] and not (tmp_path / 'unwanted-effect.txt').exists()
    assert code != 0


@pytest.mark.parametrize('adapter', ['hook-work', 'runtime'])
@pytest.mark.parametrize('outcome', ['integrated', 'checks_failed', 'thrown-rejection'])
def test_bound_work_consumers_save_true_outcomes(runtime, monkeypatch, adapter, outcome):
    calls = []
    payload = deepcopy(json.loads(FIXTURE.read_text())['result'])
    payload['status'] = outcome

    def invoke(*args):
        calls.append(args)
        if outcome == 'thrown-rejection':
            raise PoiseError('consumer refused')
        return payload

    packet = {'operation': 'verify', 'input': {'result': None, 'artifacts': []}, 'messages': []}
    output, error = io.StringIO(), io.StringIO()
    if adapter == 'hook-work':
        settings = SimpleNamespace(observations=runtime.runtime, raw={
            'output_chars': 2200, 'response_file': 'response.json', 'file_mode': 0o600,
            'max_input_bytes': 20000, 'exit_codes': {'success': 7, 'incomplete': 8, 'rejected': 9}})
        monkeypatch.setattr(hook_transport, 'HookService',
                            lambda path: SimpleNamespace(settings=settings, work=invoke, runtime=runtime))
        code = hook_transport.execute('hook-work', SimpleNamespace(settings='fixture', binding='fixture-binding'),
                                      io.BytesIO(json.dumps(packet).encode()), output, error)
        expected_code = {'integrated': 7, 'checks_failed': 8, 'thrown-rejection': 9}[outcome]
    else:
        monkeypatch.setattr(runtime_adapter, 'read_json',
                            lambda path: {'project_config': str(runtime.runtime / 'project.json')})
        monkeypatch.setattr(runtime_adapter, 'RuntimeAdapter',
                            lambda settings: SimpleNamespace(invoke=invoke, runtime=runtime))
        monkeypatch.setattr(runtime_adapter, 'load_config', lambda path: (None, runtime.cfg, None))
        code = runtime_adapter.execute(runtime.runtime / 'runtime.json',
            io.BytesIO(json.dumps(packet).encode()), output, error)
        expected_code = {'integrated': 0, 'checks_failed': 1, 'thrown-rejection': 2}[outcome]
    expected_status = 'rejected' if outcome == 'thrown-rejection' else outcome
    brief, full = read_response(output, expected_status)
    assert code == expected_code and len(calls) == 1 and error.getvalue() == ''
    if outcome == 'thrown-rejection':
        assert full == {'status': 'rejected', 'reason': 'consumer refused'}
    else:
        assert full == payload and 'historical_receipts' not in brief


@pytest.mark.parametrize('adapter', ['goal-config', 'catalogue'])
@pytest.mark.parametrize('outcome,exit_code', [('success', 7), ('rejected', 9), ('pending', 11)])
def test_editor_catalogue_preserve_configured_exit_policy(tmp_path, monkeypatch, adapter, outcome, exit_code):
    settings = editor_settings(tmp_path, 2200)
    payload = deepcopy(json.loads(FIXTURE.read_text())['result'])

    def operation(*args):
        if outcome == 'rejected':
            raise PoiseError('refused')
        if outcome == 'pending':
            raise goal_config.PublicationPending('publication pending')
        return payload

    output = io.StringIO()
    if adapter == 'goal-config':
        monkeypatch.setattr(goal_config, 'EditorSettings', lambda path: settings)
        monkeypatch.setattr(goal_config, 'goal_config_tools', lambda path: SimpleNamespace(status=operation))
        code = goal_config.execute(settings.path, io.BytesIO(b'{"schema":"goal-config-status-1"}'), output)
    else:
        repository = SimpleNamespace(editor=settings, root=tmp_path, raw={'max_items': 10})
        monkeypatch.setattr(catalogue, 'FileCatalogue', lambda path: repository)
        monkeypatch.setattr(catalogue, 'goal_config_tools', lambda path: object())
        monkeypatch.setattr(catalogue, 'CatalogueCommands', lambda *args: SimpleNamespace(install=operation))
        code = catalogue.execute(tmp_path / 'catalogue.json',
            io.BytesIO(b'{"action":"install","items":[],"project_config":null}'), output)
    assert code == exit_code
    brief, full = read_response(output, 'integrated' if outcome == 'success' else outcome)
    if outcome == 'success':
        assert full == payload and 'historical_receipts' not in brief


@pytest.mark.parametrize('adapter', ['work', 'hook', 'goal-config', 'catalogue'])
def test_real_storage_failure_never_emits_a_fake_link(tmp_path, runtime, monkeypatch, adapter):
    payload = {'status': 'installed'}
    output = io.StringIO()

    def unavailable(*args):
        raise OSError('storage unavailable')

    if adapter == 'work':
        monkeypatch.setattr(work, 'atomic_write', unavailable)
        with pytest.raises(OSError, match='storage unavailable'):
            work.write_result(runtime, payload, output)
    elif adapter == 'hook':
        monkeypatch.setattr(hook_transport, 'atomic_write', unavailable)
        service = SimpleNamespace(settings=SimpleNamespace(observations=tmp_path, raw={
            'output_chars': 2200, 'response_file': 'response.json', 'file_mode': 0o600}))
        with pytest.raises(OSError, match='storage unavailable'):
            hook_transport._write(service, payload, output)
    elif adapter == 'goal-config':
        settings = editor_settings(tmp_path, 2200)
        monkeypatch.setattr(goal_config, 'EditorSettings', lambda path: settings)
        monkeypatch.setattr(goal_config, 'goal_config_tools', lambda path: SimpleNamespace(status=lambda packet: payload))
        monkeypatch.setattr(goal_config, 'atomic_write', unavailable)
        assert goal_config.execute(settings.path, io.BytesIO(b'{"schema":"goal-config-status-1"}'), output) == 11
        assert json.loads(output.getvalue())['status'] == 'response_storage_error'
    else:
        settings = editor_settings(tmp_path, 2200)
        repository = SimpleNamespace(editor=settings, root=tmp_path, raw={'max_items': 10})
        monkeypatch.setattr(catalogue, 'FileCatalogue', lambda path: repository)
        monkeypatch.setattr(catalogue, 'goal_config_tools', lambda path: object())
        monkeypatch.setattr(catalogue, 'CatalogueCommands', lambda *args: SimpleNamespace(install=lambda packet: payload))
        monkeypatch.setattr(catalogue, 'atomic_write', unavailable)
        assert catalogue.execute(tmp_path / 'catalogue.json',
            io.BytesIO(b'{"action":"install","items":[],"project_config":null}'), output) == 11
        assert json.loads(output.getvalue())['status'] == 'response_storage_error'
    assert 'response_path' not in output.getvalue()
