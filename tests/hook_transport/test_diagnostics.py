"""C031: observable owner effects, never installation-as-proof or a new work gate."""
import io
import json
import os
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest
from poise.common import load_config, configured_root
from poise.infrastructure.hook_transport import HookService
from poise.interfaces.hook_transport import execute
from .helpers import settings, install, event


def setup(project, tmp_path, reader=True):
    if reader:
        policy = Path(__file__).parents[2] / 'config/development/source-reader.json'
        project['cfg']['source_reader'] = {'receipt_file': 'reader.json', 'policy': str(policy)}
    s = HookService(settings(project, tmp_path))
    installed = install(s)
    return s, installed


def native(s, installed, name='SessionStart', source='startup', session='A'):
    wire = event(name, session=session)
    wire['cwd'] = str(s.settings.root)
    if name == 'SessionStart': wire['source'] = source
    s.event(installed['definition_path'], wire)
    with sqlite3.connect(s.settings.database) as db:
        return db.execute('SELECT max(id) FROM hook_events WHERE binding=?', (session,)).fetchone()[0]


def request(installed, **updates):
    return {'definition_path': installed['definition_path'], 'session_id': 'A',
            'event': 'SessionStart', 'event_id': None, 'run_ids': [], 'probe_cwd': None, **updates}


def call(s, query):
    out = io.StringIO(); err = io.StringIO()
    code = execute('runtime-config', SimpleNamespace(settings=s.settings.path),
                   io.BytesIO(json.dumps({'operation': 'diagnose', 'input': query}).encode()), out, err)
    assert not err.getvalue(), err.getvalue()
    value = json.loads(out.getvalue())
    if 'response_path' in value:
        value = json.loads(Path(value['response_path']).read_text())
    return code, value


def state_paths(s, session='A'):
    root, cfg, _ = load_config(s.settings.project_config)
    state = configured_root(root, cfg['paths']['state'])
    return state / cfg['paths']['database'], state / cfg['paths']['runtime'] / session / 'reader.json'


def test_installed_only_is_not_evidence_and_does_not_create_binding(project, tmp_path):
    s, installed = setup(project, tmp_path)
    before = s.settings.database.read_bytes()
    code, result = call(s, request(installed))
    assert code == 1
    assert result['status'] == 'inconclusive'
    assert result['installation']['status'] == 'configured'
    assert result['native_event']['status'] == 'not_observed'
    assert result['host_trust'] == 'not_observed'
    assert s.settings.database.read_bytes() == before
    assert not s.settings.bindings.exists()


def test_start_has_correlated_reader_effect_without_state_mutation(project, tmp_path):
    s, installed = setup(project, tmp_path)
    eid = native(s, installed)
    dbpath, reader = state_paths(s)
    watched = [dbpath, reader, s.settings.database, s.settings.hooks_file]
    before = [p.read_bytes() for p in watched]
    code, result = call(s, request(installed, event_id=eid))
    assert code == 0
    assert result['status'] == 'diagnosed'
    assert result['effect']['status'] == 'confirmed'
    assert result['effect']['owner'] == 'SourceReader'
    assert result['effect']['context'] == {'generation': 2, 'reason': 'startup', 'event_id': f'hook-event:{eid}'}
    assert [p.read_bytes() for p in watched] == before
    assert not s.settings.observations.exists()  # no implicit probes


def test_old_start_does_not_confirm_current_generation(project, tmp_path):
    s, installed = setup(project, tmp_path)
    eid = native(s, installed)
    current = native(s, installed, source='compact')
    code, result = call(s, request(installed, event_id=eid))
    assert code == 1 and result['effect']['status'] == 'not_correlated'
    code, result = call(s, request(installed, event_id=current))
    assert code == 0 and result['effect']['context']['reason'] == 'compact'


def test_reader_not_configured_is_explicit_not_a_read_claim(project, tmp_path):
    s, installed = setup(project, tmp_path, reader=False)
    native(s, installed)
    code, result = call(s, request(installed))
    assert code == 0 and result['effect']['status'] == 'not_configured'


@pytest.mark.parametrize('damage', ['missing', 'corrupt', 'fifo', 'wrong-generation'])
def test_reader_failure_cannot_be_confirmed(project, tmp_path, damage):
    s, installed = setup(project, tmp_path)
    native(s, installed)
    _, path = state_paths(s)
    if damage == 'missing': path.unlink()
    elif damage == 'corrupt': path.write_text('{broken')
    elif damage == 'fifo': path.unlink(); os.mkfifo(path)
    else:
        value = json.loads(path.read_text()); value['generation'] = True
        path.write_text(json.dumps(value))
    code, result = call(s, request(installed))
    assert code == 1
    assert result['effect']['status'] in ('unavailable', 'not_observed')


def test_prompt_effect_uses_exact_existing_interaction_receipt(project, tmp_path):
    s, installed = setup(project, tmp_path)
    native(s, installed)
    eid = native(s, installed, 'UserPromptSubmit')
    dbpath, _ = state_paths(s)
    before = dbpath.read_bytes()
    code, result = call(s, request(installed, event='UserPromptSubmit', event_id=eid))
    assert code == 0 and result['effect']['status'] == 'confirmed'
    assert result['effect']['owner'] == 'InteractionStore'
    assert result['effect']['message']['conversation_id'] == 'A'
    assert result['effect']['message']['message_id'] == 'turn1'
    assert 'private user content' not in json.dumps(result)
    assert dbpath.read_bytes() == before
    with sqlite3.connect(dbpath) as db:
        db.execute("UPDATE interaction_events SET project='foreign'")
    code, result = call(s, request(installed, event='UserPromptSubmit'))
    assert code == 1 and result['effect']['status'] == 'not_observed'


def test_stop_is_observed_but_response_delivery_is_not_claimed(project, tmp_path):
    s, installed = setup(project, tmp_path)
    native(s, installed); native(s, installed, 'Stop')
    code, result = call(s, request(installed, event='Stop'))
    assert code == 1
    assert result['native_event']['status'] == 'observed'
    assert result['effect']['status'] == 'not_recorded'


def test_session_end_requires_specific_run_receipts(project, tmp_path):
    s, installed = setup(project, tmp_path)
    native(s, installed); native(s, installed, 'SessionEnd')
    code, result = call(s, request(installed, event='SessionEnd'))
    assert code == 1 and result['effect']['status'] == 'not_observed'
    code, result = call(s, request(installed, event='SessionEnd', run_ids=['missing-run']))
    assert code == 1 and result['effect']['runs'][0]['status'] == 'not_observed'


def test_other_session_event_cannot_be_used(project, tmp_path):
    s, installed = setup(project, tmp_path)
    native(s, installed)
    other = native(s, installed, session='B')
    code, result = call(s, request(installed, event_id=other))
    assert code == 1 and result['native_event']['status'] == 'not_observed'


def test_unsupported_event_is_not_inferred(project, tmp_path):
    s, installed = setup(project, tmp_path)
    code, result = call(s, request(installed, event='PreToolUse'))
    assert code == 1 and result['native_event']['status'] == 'unsupported'


@pytest.mark.parametrize('damage', ['group', 'launcher', 'binding'])
def test_changed_registration_or_binding_is_not_a_pass(project, tmp_path, damage):
    s, installed = setup(project, tmp_path)
    native(s, installed)
    binding = s.latest_binding('A', 'primary')
    if damage == 'group':
        data = json.loads(s.settings.hooks_file.read_text())
        data['hooks']['SessionStart'][0]['hooks'][0]['command'] += ' changed'
        s.settings.hooks_file.write_text(json.dumps(data))
    elif damage == 'launcher': Path(binding['launcher']).write_text('#!/bin/sh\nexit 0\n')
    else:
        data = json.loads(Path(binding['binding_path']).read_text()); data['agent_id'] = 'foreign'
        Path(binding['binding_path']).write_text(json.dumps(data))
    code, result = call(s, request(installed))
    assert code == 1
    assert result['installation']['status'] == 'changed' or result['binding']['status'] == 'changed'


def test_foreign_hook_addition_does_not_invalidate_owned_group(project, tmp_path):
    s, installed = setup(project, tmp_path)
    native(s, installed)
    data = json.loads(s.settings.hooks_file.read_text()); data['description'] = 'foreign note'
    data['hooks']['PostToolUse'] = [{'hooks': []}]
    s.settings.hooks_file.write_text(json.dumps(data))
    assert call(s, request(installed))[0] == 0


def test_unknown_registry_version_diagnosed_without_migration(project, tmp_path):
    s, installed = setup(project, tmp_path)
    native(s, installed)
    with sqlite3.connect(s.settings.database) as db: db.execute('PRAGMA user_version=99')
    before = s.settings.database.read_bytes()
    code, result = call(s, request(installed))
    assert code == 1 and result['native_event']['status'] == 'unavailable'
    assert s.settings.database.read_bytes() == before


def test_entire_batch_validated_before_active_probes(project, tmp_path, monkeypatch):
    s, installed = setup(project, tmp_path)
    called = []
    monkeypatch.setattr(HookService, 'probes', lambda *args: called.append(args))
    good = request(installed, probe_cwd=str(tmp_path))
    bad = {**request(installed), 'event_id': True}
    code, result = call(s, {'queries': [{'id': 'a', 'request': good}, {'id': 'b', 'request': bad}]})
    assert code == 2 and result['status'] == 'rejected'
    assert called == []


def test_explicit_probe_uses_only_supplied_directory(project, tmp_path, monkeypatch):
    s, installed = setup(project, tmp_path)
    native(s, installed)
    called = []
    def probe(self, definition, path):
        called.append(str(path)); return {'ready': True, 'items': []}
    monkeypatch.setattr(HookService, 'probes', probe)
    assert call(s, request(installed))[0] == 0 and called == []
    external = tmp_path / 'arbitrary-copy'; external.mkdir()
    assert call(s, request(installed, probe_cwd=str(external)))[0] == 0
    assert called == [str(external)]


def test_batch_continues_after_observational_failure(project, tmp_path):
    s, installed = setup(project, tmp_path)
    native(s, installed)
    code, result = call(s, {'queries': [
        {'id': 'lost', 'request': request(installed, session_id='missing')},
        {'id': 'ok', 'request': request(installed)}]})
    assert code == 1
    assert [(r['id'], r['result']['status']) for r in result['results']] == [
        ('lost', 'inconclusive'), ('ok', 'diagnosed')]


def test_cancelled_run_is_correlated_through_existing_receipt_owner(project, tmp_path):
    import threading
    import time
    import sys
    from poise.execution import RegisteredCheckRunner
    from poise.infrastructure.sqlite.evidence import SqliteEvidenceRepository
    s, installed = setup(project, tmp_path)
    start = native(s, installed)
    runner = RegisteredCheckRunner()
    runner.bind_cancellation(s.registry.runner_cancellation_check('A'))
    actual, errors = {}, []
    def run():
        try:
            actual.update(runner.run('test-receipt', [sys.executable, '-B', '-c',
                "import time;print('ready',flush=True);time.sleep(5)"], tmp_path, dict(os.environ), 8,
                tmp_path / 'out', tmp_path / 'err'))
        except Exception as exc: errors.append(exc)
    thread = threading.Thread(target=run); thread.start()
    try:
        until = time.monotonic() + 4
        while not (tmp_path / 'out').exists() or not (tmp_path / 'out').read_bytes():
            assert time.monotonic() < until
            time.sleep(.01)
        end = native(s, installed, 'SessionEnd')
        thread.join(4)
        assert not thread.is_alive() and not errors
        assert actual['cancelled'] is True
        assert actual['cancellation_observation'] == {
            'session_id': 'A', 'start_event_id': start, 'end_event_id': end}
        dbpath, _ = state_paths(s)
        # This read-side test uses a test-owned task reference, without Task lifecycle work.
        with sqlite3.connect(dbpath) as db:
            SqliteEvidenceRepository(db).record('fixture-task', 'implementation', 1,
                                               {**actual, 'id': 'test-receipt'})
        code, result = call(s, request(installed, event='SessionEnd', event_id=end, run_ids=['test-receipt']))
        assert code == 0 and result['effect']['runs'][0]['status'] == 'confirmed'
        assert result['effect']['runs'][0]['task_id'] == 'fixture-task'
        assert 'stdout_preview' not in result['effect']['runs'][0]
    finally:
        runner.cancel('test-receipt'); thread.join(8)


@pytest.mark.parametrize('correlation', ['absent', 'foreign', 'old-event', 'bool-id', 'bad-start'])
def test_bad_or_legacy_cancellation_receipts_cannot_confirm(project, tmp_path, correlation):
    s, installed = setup(project, tmp_path)
    start = native(s, installed)
    end = native(s, installed, 'SessionEnd')
    receipt = {'id': 'r', 'cancelled': True, 'cancellation_reason': 'native_session_end'}
    corr = {'session_id': 'A', 'start_event_id': start, 'end_event_id': end}
    if correlation == 'foreign': corr['session_id'] = 'B'
    if correlation == 'old-event': corr['end_event_id'] = end - 1
    if correlation == 'bool-id': corr['end_event_id'] = True
    if correlation == 'bad-start': corr['start_event_id'] = end
    if correlation != 'absent': receipt['cancellation_observation'] = corr
    dbpath, _ = state_paths(s)
    with sqlite3.connect(dbpath) as db:
        db.execute('INSERT INTO evidence(id,task_id,stage,iteration,data) VALUES(?,?,?,?,?)',
                   ('r', 'fixture-task', 'implementation', 1, json.dumps(receipt)))
    code, result = call(s, request(installed, event='SessionEnd', run_ids=['r']))
    assert code == 1 and result['effect']['runs'][0]['status'] == 'not_correlated'


def test_missing_database_never_created_by_diagnostics(project, tmp_path):
    s, installed = setup(project, tmp_path)
    s.settings.database.unlink()
    code, result = call(s, request(installed))
    assert code == 1 and not s.settings.database.exists()


@pytest.mark.parametrize('change', ['duplicate', 'empty', 'unknown-field', 'wrong-cwd', 'too-many'])
def test_invalid_diagnostic_batch_is_rejected(project, tmp_path, change):
    s, installed = setup(project, tmp_path)
    one = {'id': 'one', 'request': request(installed)}
    queries = [one]
    if change == 'duplicate': queries.append(one)
    if change == 'empty': queries = []
    if change == 'unknown-field': one['request']['root'] = '/tmp'
    if change == 'wrong-cwd': one['request']['probe_cwd'] = str(tmp_path / 'absent')
    if change == 'too-many': queries = [{'id': str(i), 'request': request(installed)} for i in range(33)]
    assert call(s, {'queries': queries})[0] == 2


def test_observe_committed_wal_event_without_initialization(project, tmp_path):
    s, installed = setup(project, tmp_path)
    native(s, installed)
    with sqlite3.connect(s.settings.database) as db:
        assert db.execute('PRAGMA journal_mode=WAL').fetchone()[0] == 'wal'
        db.execute('INSERT INTO hook_events(at,binding,event,data) VALUES(?,?,?,?)',
                   ('2026-09-16T03:00:00+00:00', 'A', 'Stop', '{"turn_id":"late"}'))
        db.commit()
        code, result = call(s, request(installed, event='Stop'))
        assert code == 1 and result['native_event']['turn_id'] == 'late'


def test_oversized_definition_is_bounded(project, tmp_path):
    s, installed = setup(project, tmp_path)
    Path(installed['definition_path']).write_bytes(b' ' * (s.settings.raw['max_input_bytes'] + 1))
    code, result = call(s, request(installed))
    assert code == 1 and result['native_event']['status'] == 'unavailable'


def test_read_only_operation_does_not_start_runtime_or_replay(project, tmp_path, monkeypatch):
    from poise.infrastructure import hook_transport as ht
    from poise.infrastructure.telemetry_spool import TelemetrySpool
    s, installed = setup(project, tmp_path)
    native(s, installed)
    def forbidden(*args, **kwargs): raise AssertionError('No work, replay or restore in diagnosis')
    monkeypatch.setattr(ht, 'establish_poise', forbidden)
    monkeypatch.setattr(TelemetrySpool, 'replay', forbidden)
    assert call(s, request(installed))[0] == 0


def test_new_application_coordinates_ports_without_io():
    import ast
    source = Path(__file__).parents[2] / 'src/poise/application/hook_diagnostics.py'
    violations = []
    for node in ast.walk(ast.parse(source.read_text())):
        names = [n.name for n in node.names] if isinstance(node, ast.Import) else [node.module or ''] if isinstance(node, ast.ImportFrom) else []
        violations.extend(n for n in names if n.split('.')[0] in
                          {'os', 'pathlib', 'sqlite3', 'subprocess', 'time'} or 'infrastructure' in n)
    assert violations == []


def test_real_cli_and_explicit_probe_from_arbitrary_directory(project, tmp_path):
    import shlex
    import subprocess
    s, installed = setup(project, tmp_path)
    native(s, installed)
    directory = tmp_path / 'outside-running-poise'; directory.mkdir()
    command = shlex.split(s.settings.command('runtime-config'))
    proc = subprocess.run(command, cwd=directory, text=True, capture_output=True,
        input=json.dumps({'operation': 'diagnose', 'input': request(installed, probe_cwd='.') }), timeout=10)
    assert proc.returncode == 0, proc.stderr
    value = json.loads(proc.stdout)
    if 'response_path' in value: value = json.loads(Path(value['response_path']).read_text())
    assert value['status'] == 'diagnosed'
    assert value['capability_checks']['ready'] is True
    assert s.settings.observations.is_dir()


def test_missing_or_corrupt_spool_is_not_effect_proof_or_a_gate(project, tmp_path):
    from conftest import write_json
    from accounting.test_telemetry_delivery import delivery_policy
    project['cfg']['telemetry_delivery'] = {'directory': 'telemetry-spool', 'policy': delivery_policy()}
    s, installed = setup(project, tmp_path)
    native(s, installed)
    code, result = call(s, request(installed))
    assert code == 0
    assert result['telemetry_delivery']['scope'] == 'configured_spool_not_selected_event'
    assert result['telemetry_delivery']['pre_persistence_loss_window'] is True
    assert 'records' not in result['telemetry_delivery']
    root, cfg, _ = load_config(s.settings.project_config)
    spool = configured_root(root, cfg['paths']['state']) / 'telemetry-spool'
    spool.mkdir(exist_ok=True)
    bad = spool / 'bad.event.json'; bad.write_text('{broken')
    code, result = call(s, request(installed))
    assert code == 0 and result['telemetry_delivery']['corrupt'] == 1
    assert bad.read_text() == '{broken'


@pytest.mark.parametrize('table', ['installations', 'bindings'])
def test_invalid_owner_observation_is_reported_not_an_exception(project, tmp_path, table):
    s, installed = setup(project, tmp_path); native(s, installed)
    with sqlite3.connect(s.settings.database) as db:
        # table names are test-owned literals, not external input.
        db.execute(f'UPDATE {table} SET data=?', ('[]',))
    code, result = call(s, request(installed))
    assert code == 1 and result['binding']['status'] == 'unavailable'


@pytest.mark.parametrize('exit_code,timed_out', [(0, False), (-9, True), (None, False)])
def test_cancellation_request_without_terminal_signal_is_not_effect_proof(project, tmp_path, exit_code, timed_out):
    s, installed = setup(project, tmp_path)
    start = native(s, installed); end = native(s, installed, 'SessionEnd')
    receipt = {'id': 'r', 'cancelled': True, 'cancellation_reason': 'native_session_end',
               'actual_exit_code': exit_code, 'timed_out': timed_out,
               'cancellation_observation': {'session_id': 'A', 'start_event_id': start, 'end_event_id': end}}
    dbpath, _ = state_paths(s)
    with sqlite3.connect(dbpath) as db:
        db.execute('INSERT INTO evidence(id,task_id,stage,iteration,data) VALUES(?,?,?,?,?)',
                   ('r', 'fixture-task', 'implementation', 1, json.dumps(receipt)))
    code, result = call(s, request(installed, event='SessionEnd', run_ids=['r']))
    assert code == 1 and result['effect']['runs'][0]['status'] == 'not_correlated'
