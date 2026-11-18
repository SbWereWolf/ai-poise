"""Initial PostToolUse proxy through actual public configuration/process owners."""
from copy import deepcopy
import json
import math
import os
from pathlib import Path

import pytest

from poise.infrastructure.session_establishment import cleanup_project_session_runtime
from .initial_proxy_support import invoke
from .post_tool_proxy_support import PostToolScene
from .pre_tool_proxy_support import captured, runner_records


@pytest.fixture
def scene(project, tmp_path):
    value = PostToolScene(project, tmp_path)
    try:
        yield value
    finally:
        value.stop_owned_metric()


def test_single_post_entry_preserves_other_events_and_exact_logical_commands(scene):
    scene.publish(event='SessionStart')
    scene.publish(event='PreToolUse')
    before = json.loads(scene.hooks.read_text())
    handler = scene.publish()
    current = json.loads(scene.hooks.read_text())
    assert {k: v for k, v in current.items() if k != 'hooks'} == {
        k: v for k, v in before.items() if k != 'hooks'}
    assert {k: v for k, v in current['hooks'].items() if k != 'PostToolUse'} == {
        k: v for k, v in before['hooks'].items() if k != 'PostToolUse'}
    logical = scene.services[1].repository.logical_document(current)
    assert logical['hooks']['PostToolUse'] == scene.post_members
    assert handler['async'] is False
    assert math.isfinite(handler['timeout']) and handler['timeout'] > 60.5
    assert handler['additionalContextLimit'] >= 16384
    assert scene.operation('restore')[0] == 0
    assert json.loads(scene.hooks.read_text()) == before


@pytest.mark.parametrize('tool,selected', [('Bash', [0, 1]), ('Read', [0, 1]),
                                        ('mcp__fixture__read', [0, 1])], ids=['bash', 'read', 'mcp'])
def test_wildcard_foreign_and_empty_native_matchers_get_complete_raw_input(scene, tool, selected):
    scene.bind()
    handler = scene.publish()
    raw = scene.tool_raw(tool=tool, large=True)
    assert len(raw) > 8192 and len(raw) < 1048576
    result = invoke(handler['command'], raw, scene.root)
    assert (result.returncode, result.stdout, result.stderr) == (0, b'{}\n', b'')
    scene.assert_calls(raw, selected)
    scene.assert_metric(raw)


def test_supported_tool_regex_and_no_selection_are_independent_of_source_field(scene):
    scene.change_post(0, matcher='^Bash$')
    scene.change_post(1, matcher='^mcp__fixture__read$')
    # A private host mutation arranges the foreign registration before publish.
    document = json.loads(scene.hooks.read_text())
    document['hooks']['PostToolUse'][0]['matcher'] = '^Bash$'
    from .initial_proxy_support import write
    write(scene.hooks, document)
    handler = scene.publish()
    for tool, selected in [('Bash', [0]), ('mcp__fixture__read', [1]), ('Read', [])]:
        scene.reset_calls()
        raw = scene.tool_raw(tool=tool)
        result = invoke(handler['command'], raw, scene.root)
        assert (result.returncode, result.stdout, result.stderr) == (0, b'{}\n', b'')
        scene.assert_calls(raw, selected)
    assert scene.assert_metric(scene.tool_raw(tool='Bash'))


def test_malformed_matcher_refuses_before_partial_publication(scene):
    document = json.loads(scene.hooks.read_text())
    document['hooks']['PostToolUse'][0]['matcher'] = '['
    from .initial_proxy_support import write
    write(scene.hooks, document)
    before = scene.hooks.read_bytes()
    result = scene.operation()
    assert result[0] != 0
    assert 'matcher' in json.dumps(result).lower()
    assert scene.hooks.read_bytes() == before


@pytest.mark.parametrize('late_stdout,late_code', [(b'{"continue":true}\n', 0),
                                                 (b'not-json\n', 1)], ids=['success', 'failure'])
def test_async_metric_does_not_delay_override_or_stop_sync_chain(scene, late_stdout, late_code):
    scene.bind()
    scene.metric_outcome(stdout=late_stdout, code=late_code, barrier=True)
    scene.outcome(0, response={'continue': False, 'stopReason': 'literal ERP stop'})
    handler = scene.publish()
    raw = scene.tool_raw(large=True)
    launches = scene.root / 'launches.jsonl'
    result = invoke(handler['command'], raw, scene.root,
                    environment={'POST_TOOL_SPAWN_RECORD': str(launches)})
    # communicate() has already completed while the background process is held.
    assert (result.returncode, json.loads(result.stdout), result.stderr) == (
        0, {'continue': False, 'stopReason': 'literal ERP stop'}, b'')
    metric = scene.assert_metric(raw)
    os.kill(metric['pid'], 0)
    assert not (scene.trace / 'metric-finished').exists()
    assert [row['target'] for row in map(json.loads, launches.read_text().splitlines())] == [
        'metric', 0, 1]
    scene.assert_calls(raw, [0, 1])
    scene.release_metric()
    assert json.loads(result.stdout) == {'continue': False, 'stopReason': 'literal ERP stop'}


def test_foreign_launch_failure_is_diagnostic_and_never_stops_later_sync_commands(scene):
    scene.bind()
    handler = scene.publish()
    raw = scene.tool_raw()
    launches = scene.root / 'launches.jsonl'
    result = invoke(handler['command'], raw, scene.root, environment={
        'POST_TOOL_SPAWN_RECORD': str(launches), 'POST_TOOL_FAIL_TARGET': 'metric'})
    assert json.loads(result.stdout) == {}
    assert 'fixture actual foreign Popen failure' in result.stderr.decode()
    scene.assert_calls(raw, [0, 1])
    assert not (scene.trace / 'metric-started').exists()
    documents = [p.read_text() for p in scene.capture_roots['proxy-fixture-session'].rglob('*.json')]
    assert any('fixture actual foreign Popen failure' in text for text in documents)


@pytest.mark.parametrize('codes,expected', [([1, 0], 1), ([1, 2], 2), ([2, 0], 2)], ids=['first-error', 'later-two', 'early-two'])
def test_sync_failure_does_not_invent_control_and_keeps_complete_capture(scene, codes, expected):
    scene.bind()
    handler = scene.publish()
    raw = scene.tool_raw(large=True)
    diagnostic = (Path(__file__).parent / 'fixtures/pre_tool_proxy/large-input.txt').read_bytes()
    for number, code in enumerate(codes):
        scene.outcome(number, stdout=b'' if code else b'{}\n', stderr=diagnostic, code=code)
    result = invoke(handler['command'], raw, scene.root)
    assert (result.returncode, result.stdout, result.stderr) == (expected, b'{}\n', diagnostic * 2)
    scene.assert_calls(raw, [0, 1])
    for code in codes:
        captured(scene, b'' if code else b'{}\n', diagnostic, code)


@pytest.mark.parametrize('failure', ['invalid-json', 'invalid-type', 'timeout', 'shell-127', 'spawn-exception'])
def test_sync_protocol_or_launch_failure_still_runs_the_next_native_command(scene, failure):
    scene.bind()
    if failure == 'invalid-json':
        scene.outcome(0, stdout=b'not-json\n')
    elif failure == 'invalid-type':
        scene.outcome(0, stdout=b'[]\n')
    elif failure == 'timeout':
        scene.change_post(0, timeout=.2)
        scene.outcome(0, mode='wait')
    handler = scene.publish()
    if failure == 'shell-127':
        scene.probes[0].unlink()
    raw = scene.tool_raw()
    environment = {}
    witness = scene.root / 'native-spawn-failure.json'
    if failure == 'spawn-exception':
        environment = {'INITIAL_PROXY_FAIL_POPEN_TARGET': str(scene.probes[0]),
                       'INITIAL_PROXY_FAULT_WITNESS': str(witness)}
    result = invoke(handler['command'], raw, scene.root, environment=environment)
    assert result.returncode == 1 and result.stdout == b'{}\n' and result.stderr
    scene.assert_calls(raw, [1] if failure in ('shell-127', 'spawn-exception') else [0, 1])
    captured(scene, b'{}\n', b'', 0)
    if failure == 'timeout':
        assert any(row.get('timed_out') is True for row in runner_records(scene))
    if failure == 'spawn-exception':
        assert json.loads(witness.read_text())['error'] == 'fixture actual Popen failure'
        captured(scene, b'', b'', None)


@pytest.mark.parametrize('repository,bound,expected', [('poise', False, 2),
                                                     ('poise', True, 1), ('erp', True, 1)], ids=['unbound', 'poise-bound', 'erp-bound'])
def test_real_native_post_guards_and_delivery_are_preserved(scene, repository, bound, expected):
    if bound:
        scene.bind(repository=repository)
    for number in (0, 1):
        scene.outcome(number, mode='delegate')
    handler = scene.publish()
    raw = scene.tool_raw(repository=repository)
    result = invoke(handler['command'], raw, scene.root)
    assert (result.returncode, result.stdout, result.stderr) == (
        1, b'{}\n', b'Native event requires its genuine preceding SessionStart binding\n' * expected)
    for number in (0, 1):
        rows = scene.post_deliveries(number)
        success = bound and number == (1 if repository == 'poise' else 0)
        assert len(rows) == int(success)
        if success:
            assert rows[0]['event']['hook_event_name'] == 'PostToolUse'
            assert rows[0]['status'] == 'completed'
    scene.assert_calls(raw, [0, 1])
    scene.assert_metric(raw)


def test_foreign_receives_oversized_input_while_original_native_admission_refuses_it(scene):
    scene.bind()
    for number in (0, 1):
        scene.outcome(number, mode='delegate')
    handler = scene.publish()
    raw = scene.tool_raw(oversized=True)
    assert len(raw) > 1048576
    result = invoke(handler['command'], raw, scene.root)
    assert result.returncode != 0
    assert json.loads(result.stdout) == {}
    assert result.stderr.count(b'Hook/adapter packet exceeds configured input limit') == 2
    scene.assert_metric(raw)


def test_post_context_and_native_event_name_are_preserved(scene):
    scene.outcome(0, response={'hookSpecificOutput': {
        'hookEventName': 'PostToolUse', 'additionalContext': 'literal ERP context'}})
    handler = scene.publish()
    result = invoke(handler['command'], scene.tool_raw(), scene.root)
    assert (result.returncode, json.loads(result.stdout), result.stderr) == (0, {
        'hookSpecificOutput': {'hookEventName': 'PostToolUse',
                               'additionalContext': 'literal ERP context'}}, b'')


def test_revision_replay_and_restore_keep_existing_projections(scene):
    scene.publish(event='SessionStart')
    scene.publish(event='PreToolUse')
    before = scene.hooks.read_bytes()
    revision = scene.services[1].revision()
    scene.publish()
    first = scene.operation(revision=revision)
    assert first[0] == 0
    assert scene.operation('restore')[0] == 0
    assert scene.hooks.read_bytes() == before
    assert scene.operation(revision=revision) == first
    assert scene.hooks.read_bytes() == before
    stale = scene.operation(request_id='stale-post', revision='0' * 64)
    assert stale[0] != 0 and 'revision' in json.dumps(stale).lower()
    assert scene.hooks.read_bytes() == before


def test_logical_member_update_preserves_three_projections_and_restores_latest_post(scene):
    for event in ('SessionStart', 'PreToolUse', 'PostToolUse'):
        scene.publish(event=event)
    before = json.loads(scene.hooks.read_text())
    scene.change_post(1, timeout=12)
    current = json.loads(scene.hooks.read_text())
    assert all(len(current['hooks'][event]) == 1
               for event in ('SessionStart', 'PreToolUse', 'PostToolUse'))
    for event in ('SessionStart', 'PreToolUse'):
        assert current['hooks'][event] == before['hooks'][event]
    assert scene.operation('restore')[0] == 0
    restored = json.loads(scene.hooks.read_text())
    assert restored['hooks']['PostToolUse'][0] == scene.post_members[0]
    assert restored['hooks']['PostToolUse'][1] == scene.post_members[1]
    last, = restored['hooks']['PostToolUse'][2]['hooks']
    assert last['timeout'] == 12
    assert scene.receipts[1]['definition_path'] in last['command']
    assert restored['hooks']['SessionStart'] == before['hooks']['SessionStart']
    assert restored['hooks']['PreToolUse'] == before['hooks']['PreToolUse']


def test_interrupted_post_publication_replays_through_existing_owner(scene, monkeypatch):
    import poise.infrastructure.hook_transport as transport
    scene.publish(event='PreToolUse')
    before = json.loads(scene.hooks.read_text())
    revision = scene.services[1].revision()
    original = transport.atomic_write
    reached = []

    def replace_then_fail(path, data, mode):
        original(path, data, mode)
        if Path(path) == scene.hooks:
            reached.append(True)
            raise OSError('private failure after PostToolUse replacement')

    with monkeypatch.context() as patch:
        patch.setattr(transport, 'atomic_write', replace_then_fail)
        interrupted = scene.operation(revision=revision)
    assert reached and interrupted[0] != 0
    recovered = scene.operation(revision=revision)
    assert recovered[0] == 0
    assert scene.operation(revision=revision) == recovered
    current = json.loads(scene.hooks.read_text())
    assert len(current['hooks']['PostToolUse']) == 1
    assert current['hooks']['PreToolUse'] == before['hooks']['PreToolUse']


@pytest.mark.parametrize('enforce', [False, True], ids=['audit', 'enforce'])
def test_real_native_tool_result_survives_proxy(project, tmp_path, enforce):
    from .post_tool_proxy_support import actual_output_case
    actual_output_case(project, tmp_path, proxy=True, enforce=enforce)


def test_complete_captures_are_separate_per_session_and_cleanup_preserves_other_session(scene):
    first = scene.bind(session='post-capture-a')
    second = scene.bind(session='post-capture-b')
    handler = scene.publish()
    pairs = []
    for session in ('post-capture-a', 'post-capture-b'):
        scene.outcome(0, stdout=b'', stderr=b'complete native diagnostic\n', code=1)
        result = invoke(handler['command'], scene.tool_raw(session=session), scene.root, session)
        assert (result.returncode, result.stdout, result.stderr) == (
            1, b'{}\n', b'complete native diagnostic\n')
        pairs.append(captured(scene, b'', b'complete native diagnostic\n', 1, session))
    assert pairs[0].isdisjoint(pairs[1])
    preserved = {name: Path(name).read_bytes() for pair in pairs[1] for name in pair}
    cleanup_project_session_runtime(scene.services[1].settings.project_config,
                                   scene.bindings['post-capture-a']['session_id'])
    assert not first.exists() and second.exists()
    assert {name: Path(name).read_bytes() for name in preserved} == preserved


def test_same_file_reconfiguration_preserves_all_three_active_projections(scene):
    from poise.common import digest, file_digest
    from poise.infrastructure.hook_transport import HookService
    for event in ('SessionStart', 'PreToolUse', 'PostToolUse'):
        scene.publish(event=event)
    before = json.loads(scene.hooks.read_text())
    service = scene.services[1]
    settings = json.loads(service.settings.path.read_text())
    changed = deepcopy(settings)
    changed['repository_scope']['timeout_seconds'] = 4
    receipt = service.reconfigure({'request_id': 'post-proxy-same-file',
        'expected_settings_digest': digest(settings), 'expected_revision': service.revision(),
        'target_revision': file_digest(scene.hooks), 'settings': changed,
        'definition': scene.definitions[1]})
    assert receipt['status'] == 'installed'
    scene.services[1] = HookService(service.settings.path)
    current = json.loads(scene.hooks.read_text())
    assert all(len(current['hooks'][event]) == 1
               for event in ('SessionStart', 'PreToolUse', 'PostToolUse'))
    assert current == before
    logical = scene.services[1].repository.logical_document(current)
    assert logical['hooks']['PostToolUse'] == scene.post_members


def test_invalid_native_object_is_refused_without_running_callbacks(scene):
    handler = scene.publish()
    result = invoke(handler['command'], b'[]\n', scene.root)
    assert (result.returncode, result.stdout, result.stderr) == (
        1, b'', b'Native hook JSON object required\n')
    assert scene.calls() == []
    assert not (scene.trace / 'metric-started').exists()
