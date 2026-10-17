"""Native event commitment, configured consumer delivery and episode continuity."""
import json
from pathlib import Path
import subprocess
import sys
import time

import pytest

from conftest import write_json
from poise.common import PoiseError
from poise.infrastructure.hook_transport import HookService, HookSettings
from .helpers import command_probe, definition, event, install, settings
from .test_mcp import spec as mcp_spec


RECEIVER = r'''
import json, os, sqlite3, sys, time
from pathlib import Path
packet = json.load(sys.stdin)
assert packet['schema'] == 'poise-hook-lifecycle-1'
receipt = packet['event_receipt']
assert packet['deadline_monotonic'] > time.monotonic()
with sqlite3.connect('file:' + os.environ['DATABASE'] + '?mode=ro', uri=True) as db:
    row = db.execute('SELECT binding,event FROM hook_events WHERE id=?',
                     (receipt['event_id'],)).fetchone()
assert row == (receipt['binding_id'], packet['native_event']['hook_event_name'])
Path(os.environ['MARKER']).write_text(json.dumps(packet))
if receipt['event'] == 'SessionStart':
    print(json.dumps({'hookSpecificOutput': {'hookEventName': 'SessionStart',
        'additionalContext': 'ERP admission delivered'}}))
else:
    print('{}')
'''


def consumer(project, code=RECEIVER):
    root = project['root']
    return {'argv': [sys.executable, '-B', '-c', code], 'cwd': str(root),
            'environment': {'MARKER': str(root / 'received.json'),
                'DATABASE': str(root / 'state/hook-control/hook-state.sqlite')},
            'timeout_seconds': 1.5, 'cleanup_seconds': 0.15,
            'poll_seconds': 0.01, 'completion_reserve_seconds': 0.3,
            'max_output_bytes': 4096}


def configured(project, tmp_path, code=RECEIVER, hook_definition=None):
    path = settings(project, tmp_path)
    raw = json.loads(path.read_text())
    raw['lifecycle_consumer'] = consumer(project, code)
    write_json(path, raw)
    service = HookService(path)
    installed = install(service, d=hook_definition)
    return service, installed


def deliver(service, installed, kind='SessionStart', **changes):
    packet = {**event(kind, 'conversation'), 'cwd': str(service.settings.root), **changes}
    return service.event(installed['definition_path'], packet)


def native_rows(service):
    with service.registry.read_transaction() as db:
        return [tuple(row) for row in db.execute(
            "SELECT id,binding,event FROM hook_events WHERE event IN ('SessionStart','SessionEnd') ORDER BY id")]


def received(project):
    marker = project['root'] / 'received.json'
    assert marker.is_file(), 'Configured lifecycle consumer was not invoked'
    return json.loads(marker.read_text())


def delivery_records(service):
    return [value for path in service.settings.observations.rglob(service.settings.raw['response_file'])
            if (value := json.loads(path.read_text())).get('schema') == 'poise-hook-lifecycle-delivery-1']


def test_committed_start_is_visible_to_real_consumer_before_admission(project, tmp_path):
    service, installed = configured(project, tmp_path)
    output = deliver(service, installed)
    packet = received(project)
    rows = native_rows(service)
    assert len(rows) == 1
    assert rows[0][1:] == ('conversation', 'SessionStart')
    assert packet['event_receipt'] == {
        'schema_version': 1, 'event': 'SessionStart', 'event_id': rows[0][0],
        'project_id': 'demo', 'session_id': 'conversation', 'binding_id': 'conversation',
        'native_start_event_id': rows[0][0]}
    assert packet['native_event']['source'] == 'startup'
    assert 'ERP admission delivered' in output['hookSpecificOutput']['additionalContext']


@pytest.mark.parametrize('source', ['resume', 'clear', 'compact'])
def test_event_receipt_binds_to_retained_original_episode(project, tmp_path, source):
    service, installed = configured(project, tmp_path)
    deliver(service, installed)
    first = native_rows(service)[0][0]
    (project['root'] / 'received.json').unlink(missing_ok=True)
    episode = {'project_id': 'demo', 'session_id': 'conversation',
               'binding_id': 'conversation', 'start_event_id': first}
    deliver(service, installed, source=source)
    packet = received(project)
    assert packet['event_receipt']['event_id'] > first
    bound = service.bind_lifecycle_receipt(installed['definition_path'], packet['event_receipt'], episode)
    assert bound == {'schema_version': 1, 'event': 'SessionStart',
                     'event_id': packet['event_receipt']['event_id'], 'episode': episode}
    assert len(native_rows(service)) == 2


def test_committed_end_survives_unavailable_subject_task_runtime(project, tmp_path, monkeypatch):
    service, installed = configured(project, tmp_path)
    deliver(service, installed)
    first = native_rows(service)[0][0]
    (project['root'] / 'received.json').unlink(missing_ok=True)
    def unavailable(*args, **kwargs):
        raise PoiseError('Subject Task runtime unavailable')
    monkeypatch.setattr(service, '_runtime_event', unavailable)
    try:
        output = deliver(service, installed, 'SessionEnd')
    except PoiseError:
        output = None
    rows = native_rows(service)
    assert [row[2] for row in rows] == ['SessionStart', 'SessionEnd']
    packet = received(project)
    assert packet['event_receipt']['event_id'] == rows[1][0]
    assert packet['event_receipt']['native_start_event_id'] == first
    assert output == {}


def test_end_without_observed_start_preserves_fact_without_inventing_episode(project, tmp_path):
    service, installed = configured(project, tmp_path)
    with pytest.raises(PoiseError, match='SessionStart'):
        deliver(service, installed, 'SessionEnd')
    rows = native_rows(service)
    assert len(rows) == 1 and rows[0][1:] == ('conversation', 'SessionEnd')
    assert not (project['root'] / 'received.json').exists()


def test_old_end_receipt_cannot_bind_to_new_active_episode(project, tmp_path):
    service, installed = configured(project, tmp_path)
    deliver(service, installed)
    old_start = native_rows(service)[0][0]
    deliver(service, installed, 'SessionEnd')
    old_end = {'schema_version': 1, 'event': 'SessionEnd',
               'event_id': native_rows(service)[1][0], 'project_id': 'demo',
               'session_id': 'conversation', 'binding_id': 'conversation',
               'native_start_event_id': old_start}
    old_episode = {'project_id': 'demo', 'session_id': 'conversation',
                   'binding_id': 'conversation', 'start_event_id': old_start}
    deliver(service, installed, source='resume')
    new_episode = {**old_episode, 'start_event_id': native_rows(service)[2][0]}
    before = native_rows(service)
    assert callable(getattr(service, 'bind_lifecycle_receipt', None)), 'Native receipt binding operation is absent'
    assert service.bind_lifecycle_receipt(installed['definition_path'], old_end, old_episode) == {
        'schema_version': 1, 'event': 'SessionEnd', 'event_id': old_end['event_id'], 'episode': old_episode}
    with pytest.raises(PoiseError, match='episode'):
        service.bind_lifecycle_receipt(installed['definition_path'], old_end, new_episode)
    new_start = {**old_end, 'event': 'SessionStart', 'event_id': new_episode['start_event_id'],
                 'native_start_event_id': new_episode['start_event_id']}
    with pytest.raises(PoiseError, match='episode'):
        service.bind_lifecycle_receipt(installed['definition_path'], new_start, old_episode)
    with pytest.raises(PoiseError):
        service.bind_lifecycle_receipt(installed['definition_path'], old_end,
                                      {**old_episode, 'session_id': 'foreign-actor'})
    assert native_rows(service) == before


@pytest.mark.parametrize('code,reason', [
    ('import sys;sys.exit(7)', 'nonzero_exit'),
    ('import time;time.sleep(10)', 'timed_out'),
    ('print("invalid-json")', 'invalid_output'),
    ('import sys,time;sys.stdout.write("x"*1000000);sys.stdout.flush();time.sleep(10)', 'output_limit'),
])
def test_real_consumer_failure_preserves_commit_and_retriable_receipt(project, tmp_path, code, reason):
    service, installed = configured(project, tmp_path, code)
    with pytest.raises(PoiseError, match='consumer'):
        deliver(service, installed)
    rows = native_rows(service)
    assert len(rows) == 1 and rows[0][1:] == ('conversation', 'SessionStart')
    records = delivery_records(service)
    assert len(records) == 1
    assert records[0]['status'] == 'incomplete'
    assert records[0]['reason'] == reason
    assert records[0]['packet']['event_receipt']['event_id'] == rows[0][0]
    termination = records[0]['execution']['termination']
    assert termination['child_reaped'] is True
    assert termination['process_group_stopped'] is True


@pytest.mark.skipif(sys.platform != 'linux', reason='The independently observed descendant state uses Linux procfs')
def test_timeout_stops_the_actual_owned_descendant_group(project, tmp_path):
    code = ('import os,subprocess,sys,time;from pathlib import Path;'
            'child=subprocess.Popen([sys.executable,"-B","-c","import time;time.sleep(10)"]);'
            'Path(os.environ["MARKER"]).write_text(str(child.pid));time.sleep(10)')
    service, installed = configured(project, tmp_path, code)
    with pytest.raises(PoiseError, match='consumer'):
        deliver(service, installed)
    pid = int((project['root'] / 'received.json').read_text())
    status = Path('/proc') / str(pid) / 'stat'
    if status.exists():
        assert status.read_text().split()[2] == 'Z', 'A callback descendant is still running after terminal teardown'
    record = delivery_records(service)[0]
    assert record['reason'] == 'timed_out'
    assert record['execution']['termination']['child_reaped'] is True
    assert record['execution']['termination']['process_group_stopped'] is True


def test_unknown_consumer_options_do_not_block_supported_delivery(project, tmp_path):
    path = settings(project, tmp_path)
    raw = json.loads(path.read_text())
    raw['lifecycle_consumer'] = {**consumer(project), 'operator_note': {'keep': True}}
    raw['unrecognized_operator_option'] = 'ignored'
    write_json(path, raw)
    service = HookService(path)
    installed = install(service)
    deliver(service, installed)
    assert received(project)['event_receipt']['event'] == 'SessionStart'


@pytest.mark.parametrize('field,value', [('argv', []), ('timeout_seconds', 0),
    ('max_output_bytes', -1), ('cleanup_seconds', float('inf'))])
def test_invalid_known_consumer_settings_reject_before_native_effects(project, tmp_path, field, value):
    path = settings(project, tmp_path)
    raw = json.loads(path.read_text())
    raw['lifecycle_consumer'] = consumer(project)
    raw['lifecycle_consumer'][field] = value
    with pytest.raises(PoiseError, match='lifecycle_consumer'):
        HookSettings.proposed(path, raw)
    assert not (project['root'] / 'state/hook-control/hook-state.sqlite').exists()


def test_exhausted_host_budget_never_launches_consumer(project, tmp_path, monkeypatch):
    service, installed = configured(project, tmp_path)
    original_clock = time.monotonic
    shift = [0.0]
    monkeypatch.setattr(time, 'monotonic', lambda: original_clock() + shift[0])
    record = service.registry.record_event
    def committed_then_budget_exhausted(*args, **kwargs):
        result = record(*args, **kwargs)
        shift[0] = 100.0
        return result
    monkeypatch.setattr(service.registry, 'record_event', committed_then_budget_exhausted)
    with pytest.raises(PoiseError, match='budget'):
        deliver(service, installed)
    assert len(native_rows(service)) == 1
    assert not (project['root'] / 'received.json').exists()


@pytest.mark.parametrize('kind', ['command', 'mcp_stdio'])
def test_native_probe_uses_remaining_shared_deadline(project, tmp_path, monkeypatch, kind):
    selected = definition()
    selected['events'][0]['timeout_seconds'] = 1.0
    marker = str(project['root'] / 'probe-started.txt')
    if kind == 'command':
        probe = command_probe()
        probe['argv'] = [sys.executable, '-B', '-c',
            'import sys,time;from pathlib import Path;Path(sys.argv[1]).write_text("started");time.sleep(2);print("ready")', marker]
    else:
        probe = mcp_spec('ok')
        peer = Path(__file__).with_name('mcp_fixture.py')
        probe['argv'] = [sys.executable, '-B', '-c',
            'import time,runpy,sys;from pathlib import Path;Path(sys.argv[2]).write_text("started");time.sleep(2);sys.argv=[sys.argv[1],"ok"];runpy.run_path(sys.argv[0],run_name="__main__")', str(peer), marker]
    probe['timeout_seconds'] = 3.0
    selected['probes'] = [probe]
    service, installed = configured(project, tmp_path, hook_definition=selected)
    # This test owns the optional probe boundary, not unrelated Task reconstruction.
    monkeypatch.setattr(service, '_runtime_event', lambda *args: {
        'hookSpecificOutput': {'hookEventName': 'SessionStart',
                              'additionalContext': 'test-owned native context'}})
    started = time.monotonic()
    deliver(service, installed)
    elapsed = time.monotonic() - started
    assert elapsed < 2.0, 'Native event waited for a full probe instead of the shared hook deadline'
    assert Path(marker).read_text() == 'started'
    received(project)
    binding = service.latest_binding('conversation', 'primary')
    summary = json.loads(Path(binding['binding_path']).with_name('capabilities.json').read_text())
    assert summary['ready'] is False


def test_optional_readiness_exhaustion_never_launches_a_probe(project, tmp_path, monkeypatch):
    selected = definition()
    marker = project['root'] / 'optional-probe-started.txt'
    probe = command_probe()
    probe['argv'] = [sys.executable, '-B', '-c',
        'from pathlib import Path;import sys;Path(sys.argv[1]).write_text("started");print("ready")', str(marker)]
    selected['probes'] = [probe]
    service, installed = configured(project, tmp_path, hook_definition=selected)
    monkeypatch.setattr(service, '_runtime_event', lambda *args: {
        'hookSpecificOutput': {'hookEventName': 'SessionStart',
                              'additionalContext': 'test-owned native context'}})
    original_clock, original_readiness = time.monotonic, service._readiness
    shift, entries = [0.0], []
    monkeypatch.setattr(time, 'monotonic', lambda: original_clock() + shift[0])

    def exhaust_at_optional_boundary(*args, **kwargs):
        readiness = original_readiness(*args, **kwargs)
        entries.append(True)
        shift[0] = 100.0
        return readiness

    monkeypatch.setattr(service, '_readiness', exhaust_at_optional_boundary)
    result = deliver(service, installed)
    assert entries == [True]
    assert 'ERP admission delivered' in result['hookSpecificOutput']['additionalContext']
    assert received(project)['event_receipt']['event'] == 'SessionStart'
    assert not marker.exists(), 'An optional probe launched after its shared deadline expired'
    binding = service.latest_binding('conversation', 'primary')
    summary = json.loads(Path(binding['binding_path']).with_name('capabilities.json').read_text())
    assert summary['ready'] is False


def test_public_retry_delivers_the_same_saved_receipt_without_replaying_native_event(project, tmp_path):
    selected = definition()
    selected['events'][0]['timeout_seconds'] = 1.0
    prefix = ('import os,sys;from pathlib import Path\n'
              'marker=Path(os.environ["MARKER"])\n'
              'if not marker.exists():\n'
              ' marker.write_text("first attempt failed");sys.exit(7)\n')
    service, installed = configured(project, tmp_path, prefix + RECEIVER, hook_definition=selected)
    with pytest.raises(PoiseError, match='consumer'):
        deliver(service, installed)
    before = native_rows(service)
    assert len(before) == 1 and before[0][2] == 'SessionStart'
    paths = [path for path in service.settings.observations.rglob(service.settings.raw['response_file'])
             if json.loads(path.read_text()).get('schema') == 'poise-hook-lifecycle-delivery-1']
    assert len(paths) == 1
    record = json.loads(paths[0].read_text())
    assert record['status'] == 'incomplete' and record['reason'] == 'nonzero_exit'
    # An explicit retry obtains a new delivery budget, not a fabricated new event.
    time.sleep(max(0, record['packet']['deadline_monotonic'] - time.monotonic()))
    assert record['packet']['deadline_monotonic'] <= time.monotonic()
    retry = subprocess.run([sys.executable, '-P', '-s', '-B', '-m', 'poise', 'hook',
        '--settings', str(service.settings.path), '--definition', installed['definition_path'],
        '--retry', str(paths[0])], input='', capture_output=True, text=True, timeout=10)
    assert retry.returncode == 0, retry.stdout + retry.stderr
    assert 'ERP admission delivered' in json.loads(retry.stdout)['hookSpecificOutput']['additionalContext']
    packet = received(project)
    assert packet['event_receipt'] == record['packet']['event_receipt']
    assert packet['deadline_monotonic'] > record['packet']['deadline_monotonic']
    assert native_rows(service) == before


@pytest.mark.parametrize('changed', ['source', 'cwd'])
def test_public_retry_rejects_metadata_different_from_committed_start(project, tmp_path, changed):
    prefix = ('import os,sys;from pathlib import Path\n'
              'marker=Path(os.environ["MARKER"])\n'
              'if not marker.exists():\n'
              ' marker.write_text("first attempt failed");sys.exit(7)\n')
    service, installed = configured(project, tmp_path, prefix + RECEIVER)
    with pytest.raises(PoiseError, match='consumer'):
        deliver(service, installed)
    paths = [path for path in service.settings.observations.rglob(service.settings.raw['response_file'])
             if json.loads(path.read_text()).get('schema') == 'poise-hook-lifecycle-delivery-1']
    assert len(paths) == 1
    record = json.loads(paths[0].read_text())
    assert record['status'] == 'incomplete' and record['reason'] == 'nonzero_exit'
    assert record['packet']['native_event']['source'] == 'startup'
    assert record['packet']['native_event']['cwd'] == str(service.settings.root)
    (project['root'] / 'another-workspace').mkdir()
    record['packet']['native_event'][changed] = ('clear' if changed == 'source'
                                                else str(project['root'] / 'another-workspace'))
    paths[0].write_text(json.dumps(record))
    before = paths[0].read_bytes()
    marker = (project['root'] / 'received.json').read_bytes()
    with service.registry.read_transaction() as db:
        rows = [tuple(row) for row in db.execute('SELECT * FROM hook_events ORDER BY id')]
    retry = subprocess.run([sys.executable, '-P', '-s', '-B', '-m', 'poise', 'hook',
        '--settings', str(service.settings.path), '--definition', installed['definition_path'],
        '--retry', str(paths[0])], input='', capture_output=True, text=True, timeout=10)
    assert retry.returncode != 0, retry.stdout + retry.stderr
    assert 'retry' in retry.stdout + retry.stderr
    assert 'committed' in retry.stdout + retry.stderr
    assert ('source' if changed == 'source' else 'workspace') in retry.stdout + retry.stderr
    assert paths[0].read_bytes() == before
    assert (project['root'] / 'received.json').read_bytes() == marker
    with service.registry.read_transaction() as db:
        assert [tuple(row) for row in db.execute('SELECT * FROM hook_events ORDER BY id')] == rows

