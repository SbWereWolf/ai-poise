"""Initial proxy contract at native registration and published process boundaries.

GREEN is first required at implementation, except independent legacy controls.
No fixture implements proxy execution, merging or registration projection.
"""
import base64
from copy import deepcopy
import json
from pathlib import Path

import pytest

from poise.modules.hook_transport.domain import MissingHookBinding
from .initial_proxy_support import Scene, invoke, literal


@pytest.fixture
def scene(project, tmp_path):
    return Scene(project, tmp_path)


def assert_inputs(scene, raw, expected_ids):
    calls = scene.calls()
    assert [row['id'] for row in calls] == expected_ids
    for row in calls:
        assert base64.b64decode(row['stdin']) == raw
        assert row['cwd'] == str(scene.root)
        assert row['marker'] == 'literal inherited marker'
        number = row['id']
        assert row['argv'] == ['-P', '-B', '-X', 'pycache_prefix=/dev/null',
            '-m', 'poise', 'hook', '--settings', str(scene.services[number].settings.path),
            '--definition', scene.receipts[number]['definition_path']]


def test_legacy_registered_commands_positive_control(scene):
    values = literal('responses.json')
    for number, key in enumerate(('first', 'second', 'third')):
        scene.outcome(number, values[key])
    raw = scene.raw_event()
    old = scene.legacy(raw)
    assert [result.returncode for result in old] == [0, 0, 0]
    assert [json.loads(result.stdout) for result in old] == [
        values['first'], values['second'], values['third']]
    assert [result.stderr for result in old] == [b'', b'', b'']
    assert_inputs(scene, raw, [0, 1, 2])


@pytest.mark.parametrize('repository,selected', [('poise', [1]), ('erp', [0, 2]), ('other', [])])
def test_real_legacy_repository_selection_positive_control(scene, repository, selected):
    for number in range(3):
        scene.outcome(number, mode='delegate')
    results = scene.legacy(scene.raw_event(repository=repository))
    assert [result.returncode for result in results] == [0, 0, 0], [r.stderr for r in results]
    assert [json.loads(result.stdout) for result in results] == [{}, {}, {}]
    for number, service in enumerate(scene.services):
        if number in selected:
            assert service.latest_binding('proxy-fixture-session', 'primary')
        else:
            with pytest.raises(MissingHookBinding):
                service.latest_binding('proxy-fixture-session', 'primary')


@pytest.mark.parametrize('code,diagnostic', [
    (1, b'legacy ordinary failure\n'), (2, b'legacy blocking reason\n')])
def test_legacy_error_positive_control(scene, code, diagnostic):
    scene.outcome(0, stdout=b'', stderr=diagnostic, code=code)
    results = scene.legacy(scene.raw_event())
    assert (results[0].stdout, results[0].stderr, results[0].returncode) == (b'', diagnostic, code)
    assert [(row.stdout, row.stderr, row.returncode) for row in results[1:]] == [
        (b'{}\n', b'', 0), (b'{}\n', b'', 0)]


def test_publish_replaces_three_entries_and_preserves_other_events(scene):
    # This control must succeed before the absent feature is called RED.
    assert [result.returncode for result in scene.legacy(scene.raw_event())] == [0, 0, 0]
    assert len(scene.members) == 3
    handler = scene.publish()
    actual = json.loads(scene.hooks.read_text())
    assert {k: v for k, v in actual.items() if k != 'hooks'} == {
        k: v for k, v in scene.before.items() if k != 'hooks'}
    assert {k: v for k, v in actual['hooks'].items() if k != 'SessionStart'} == {
        k: v for k, v in scene.before['hooks'].items() if k != 'SessionStart'}
    assert handler['async'] is False
    assert handler['timeout'] >= 45
    assert handler['additionalContextLimit'] >= 6002
    # Round trip exercises owned logical snapshot without fixing its private schema.
    assert scene.operation('restore')[0] == 0
    assert json.loads(scene.hooks.read_text()) == scene.before


@pytest.mark.parametrize('source', ['startup', 'resume', 'compact', 'clear', 'fork'])
def test_proxy_preserves_original_input_and_member_order(scene, source):
    handler = scene.publish()
    raw = scene.raw_event(source=source)
    result = invoke(handler['command'], raw, scene.root)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {}
    assert result.stderr == b''
    assert_inputs(scene, raw, [0, 1, 2])


def test_proxy_preserves_matcher_nonmatch(scene):
    handler = scene.publish()
    # A native matcher evaluates the raw source; rejection of unsupported source
    # remains the real transport's concern, not a newly supported event reason.
    raw = scene.raw_event(source='unmatched-fixture')
    result = invoke(handler['command'], raw, scene.root)
    assert result.returncode == 0
    assert_inputs(scene, raw, [0, 1])


@pytest.mark.parametrize('repository,selected', [('poise', [1]), ('erp', [0, 2]), ('other', [])])
def test_proxy_keeps_real_transport_repository_selection(scene, repository, selected):
    for number in range(3):
        scene.outcome(number, mode='delegate')
    handler = scene.publish()
    raw = scene.raw_event(repository=repository)
    result = invoke(handler['command'], raw, scene.root)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {}
    assert_inputs(scene, raw, [0, 1, 2])
    for number, service in enumerate(scene.services):
        if number in selected:
            assert service.latest_binding('proxy-fixture-session', 'primary')
        else:
            with pytest.raises(MissingHookBinding):
                service.latest_binding('proxy-fixture-session', 'primary')


def test_proxy_composes_literal_context_and_diagnostics(scene):
    values = literal('responses.json')
    for number, key in enumerate(('first', 'second', 'third')):
        scene.outcome(number, values[key])
    handler = scene.publish()
    result = invoke(handler['command'], scene.raw_event(), scene.root)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == values['combined']
    assert result.stderr == b''


def test_proxy_preserves_empty_and_genuine_control_results(scene):
    scene.outcome(1, literal('responses.json')['stop'])
    handler = scene.publish()
    result = invoke(handler['command'], scene.raw_event(), scene.root)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {'continue': False, 'stopReason': 'literal legacy stop'}
    assert [row['id'] for row in scene.calls()] == [0, 1, 2]


def test_proxy_retains_aggregate_multibyte_context_above_old_limit(scene):
    # 1200 bytes per member; 3602 aggregate bytes exceeds one old 2000 budget.
    values = literal('budget-responses.json')
    for number, response in enumerate(values['members']):
        scene.outcome(number, response)
    handler = scene.publish()
    result = invoke(handler['command'], scene.raw_event(), scene.root)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == values['expected']


@pytest.mark.parametrize('code,diagnostic', [
    (1, b'legacy ordinary failure\n'), (2, b'legacy blocking reason\n')])
def test_proxy_preserves_outward_error_and_full_capture(scene, code, diagnostic):
    scene.outcome(0, stdout=b'', stderr=diagnostic, code=code)
    handler = scene.publish()
    result = invoke(handler['command'], scene.raw_event(), scene.root)
    assert result.stdout == b'{}\n'
    assert result.stderr == diagnostic
    assert result.returncode == code
    assert 'continue' not in json.loads(result.stdout)
    assert 'stopReason' not in json.loads(result.stdout)
    assert [row['id'] for row in scene.calls()] == [0, 1, 2]
    assert_member_capture(scene, b'', diagnostic, code)


def runner_records(scene):
    """Observe existing runner stream references in product-owned JSON receipts.

    Test probe records are excluded. Do not let the fixture's own stdout/stderr
    options, preview or bare text search stand in for a real complete capture.
    """
    def records(value):
        if isinstance(value, dict):
            if {'stdout', 'stderr', 'actual_exit_code'} <= set(value):
                yield value
            for child in value.values():
                yield from records(child)
        elif isinstance(value, list):
            for child in value:
                yield from records(child)

    captures = []
    for path in scene.owner_root.rglob('*.json'):
        try:
            captures.extend(records(json.loads(path.read_text())))
        except (UnicodeError, json.JSONDecodeError):
            continue
    return captures


def assert_member_capture(scene, stdout, stderr, code):
    matches = []
    for record in runner_records(scene):
        if (record['actual_exit_code'] == code and
                isinstance(record['stdout'], str) and isinstance(record['stderr'], str)):
            out, err = Path(record['stdout']), Path(record['stderr'])
            if out.is_file() and err.is_file() and out.read_bytes() == stdout and err.read_bytes() == stderr:
                matches.append(record)
    assert matches, 'No product-owned runner receipt references complete expected member streams'
    assert all(record['capture_complete'] is True for record in matches)


def test_proxy_keeps_diagnostic_order_and_blocking_precedence(scene):
    scene.outcome(0, stdout=b'', stderr=b'first diagnostic\n', code=1)
    scene.outcome(1, stdout=b'', stderr=b'second diagnostic\n', code=2)
    handler = scene.publish()
    result = invoke(handler['command'], scene.raw_event(), scene.root)
    assert (result.stdout, result.stderr, result.returncode) == (
        b'{}\n', b'first diagnostic\nsecond diagnostic\n', 2)
    assert_member_capture(scene, b'', b'first diagnostic\n', 1)
    assert_member_capture(scene, b'', b'second diagnostic\n', 2)


@pytest.mark.parametrize('failure', ['invalid-json', 'timeout', 'start-error'])
def test_proxy_records_failure_and_executes_later_members(scene, failure):
    if failure == 'invalid-json':
        scene.outcome(0, stdout=b'not JSON\n')
    elif failure == 'timeout':
        scene.outcome(0, mode='wait')
        scene.edit_member(0, timeout=0.2)
        scene.members = deepcopy(json.loads(scene.hooks.read_text())['hooks']['SessionStart'])
    handler = scene.publish()
    if failure == 'start-error':
        scene.probes[0].unlink()
    result = invoke(handler['command'], scene.raw_event(), scene.root)
    assert result.returncode == 1
    assert json.loads(result.stdout) == {}
    assert result.stderr
    assert [row['id'] for row in scene.calls() if row['id'] != 0] == [1, 2]
    if failure == 'invalid-json':
        assert_member_capture(scene, b'not JSON\n', b'', 0)
    elif failure == 'timeout':
        assert any(row.get('timed_out') is True and row.get('capture_complete') is True
                   for row in runner_records(scene)), 'Timeout must have a complete owned execution record'
    else:
        assert any(row.get('actual_exit_code') == 127 and row.get('capture_complete') is True
                   for row in runner_records(scene)), 'Missing legacy executable must retain its real exit 127'


def test_member_republication_keeps_single_entry_and_restore_keeps_later_events(scene):
    scene.publish()
    before = json.loads(scene.hooks.read_text())
    scene.edit_member(1)
    changed = json.loads(scene.hooks.read_text())
    assert len(changed['hooks']['SessionStart']) == 1
    assert changed['hooks']['PostToolUse'] == before['hooks']['PostToolUse']
    changed['hooks']['Stop'] = [{'hooks': [{'type': 'command', 'command': 'echo later foreign'}]}]
    scene.hooks.write_text(json.dumps(changed))
    code, _, _ = scene.operation('restore')
    assert code == 0
    restored = json.loads(scene.hooks.read_text())
    assert restored['hooks']['Stop'] == changed['hooks']['Stop']
    groups = restored['hooks']['SessionStart']
    assert len(groups) == 3
    assert groups[0] == scene.members[0]
    assert groups[2] == scene.members[2]
    assert groups[1]['hooks'][0]['statusMessage'] == 'Changed logical member'


def test_publication_replays_and_stale_revision_refuses_without_overwrite(scene):
    revision = scene.services[1].revision()
    first = scene.operation(revision=revision)
    assert first[0] == 0, first
    before = scene.hooks.read_bytes()
    assert scene.operation(revision=revision) == first
    assert scene.hooks.read_bytes() == before
    refused = scene.operation(request_id='stale-proxy', revision='0' * 64)
    assert refused[0] != 0
    assert 'revision' in json.dumps(refused).lower()
    assert scene.hooks.read_bytes() == before


def test_interrupted_native_replace_has_owner_managed_continuation(scene, monkeypatch):
    import poise.infrastructure.hook_transport as transport
    original = transport.atomic_write
    reached = []

    def replace_then_fail(path, data, mode):
        original(path, data, mode)
        if Path(path) == scene.hooks:
            reached.append(True)
            raise OSError('fixture failure after native replacement')

    revision = scene.services[1].revision()
    with monkeypatch.context() as patch:
        patch.setattr(transport, 'atomic_write', replace_then_fail)
        interrupted = scene.operation(revision=revision)
    assert reached, ('INITIAL_PROXY_PUBLICATION_BOUNDARY_NOT_REACHED', interrupted)
    recovered = scene.operation(revision=revision)
    assert recovered[0] == 0, recovered
    assert len(json.loads(scene.hooks.read_text())['hooks']['SessionStart']) == 1
    assert scene.operation(revision=revision) == recovered
