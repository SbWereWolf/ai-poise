"""PreToolUse preservation through real registration and process boundaries.

Earliest GREEN: implementation. The separate controls remain GREEN now.
"""
import json
import math
from pathlib import Path

import pytest

from poise.infrastructure.session_establishment import cleanup_project_session_runtime
from .initial_proxy_support import invoke
from .pre_tool_proxy_support import (
    FIXTURES, PreToolScene, arrange_case, assert_deliveries, baseline_cases,
    captured, runner_records,
)


@pytest.fixture
def scene(project, tmp_path):
    return PreToolScene(project, tmp_path)


def test_publication_has_one_entry_and_keeps_existing_start(scene):
    scene.publish(event='SessionStart')
    before = json.loads(scene.hooks.read_text())
    handler = scene.publish()
    current = json.loads(scene.hooks.read_text())
    assert {k: v for k, v in current.items() if k != 'hooks'} == {
        k: v for k, v in before.items() if k != 'hooks'}
    assert {k: v for k, v in current['hooks'].items() if k != 'PreToolUse'} == {
        k: v for k, v in before['hooks'].items() if k != 'PreToolUse'}
    logical = scene.services[1].repository.logical_document(current)
    assert logical['hooks']['PreToolUse'] == scene.tool_members
    assert logical['hooks']['SessionStart'] == scene.before['hooks']['SessionStart']
    assert handler['async'] is False
    assert [group['hooks'][0]['timeout'] for group in scene.tool_members] == [30, 30]
    # Reject 60.1s: each real runner needs .25s cleanup and orchestration
    # needs finite positive time. Do not benchmark a minute-long sleep.
    assert math.isfinite(handler['timeout']) and handler['timeout'] > 60.5
    assert handler['additionalContextLimit'] >= 16384
    assert scene.operation('restore')[0] == 0
    assert json.loads(scene.hooks.read_text()) == before
    result = invoke(before['hooks']['SessionStart'][0]['hooks'][0]['command'],
                    scene.raw_event(), scene.root)
    assert (result.returncode, result.stdout, result.stderr) == (0, b'{}\n', b'')


@pytest.mark.parametrize('first,second', [
    ('SessionStart', 'PreToolUse'), ('PreToolUse', 'SessionStart')])
def test_either_projection_can_be_restored_without_removing_the_other(scene, first, second):
    scene.publish(event=first)
    scene.publish(event=second)
    both = json.loads(scene.hooks.read_text())
    assert scene.operation('restore', event=first)[0] == 0
    restored = json.loads(scene.hooks.read_text())
    assert restored['hooks'][second] == both['hooks'][second]
    assert restored['hooks'][first] == scene.before['hooks'][first]
    assert scene.operation('restore', event=second)[0] == 0
    assert json.loads(scene.hooks.read_text()) == scene.before


@pytest.mark.parametrize('tool,source,selected', [
    ('Bash', 'unmatched-source', [0, 1]),
    ('mcp__codex_app__list_projects', 'fork', [0, 1]),
    ('Read', 'startup', [0]),
])
def test_selection_uses_tool_name_and_forwards_original_input(scene, tool, source, selected):
    handler = scene.publish()
    raw = scene.tool_raw(tool=tool, source=source, repository='other', large=True)
    assert len(raw) > 5000 and len(raw) < 1048576
    result = invoke(handler['command'], raw, scene.root)
    assert (result.returncode, result.stdout, result.stderr) == (0, b'{}\n', b'')
    scene.assert_calls(raw, selected)


def test_no_matching_callback_is_neutral(scene):
    scene.change_tool(0, matcher='^Bash$')
    handler = scene.publish()
    raw = scene.tool_raw(tool='Read')
    result = invoke(handler['command'], raw, scene.root)
    assert (result.returncode, result.stdout, result.stderr) == (0, b'{}\n', b'')
    scene.assert_calls(raw, [])


@pytest.mark.parametrize('case', baseline_cases(), ids=lambda row: row['id'])
def test_real_old_behaviour_is_preserved_by_proxy(scene, case):
    raw = arrange_case(scene, case)
    handler = scene.publish()
    result = invoke(handler['command'], raw, scene.root)
    assert (result.returncode, result.stdout.decode(), result.stderr.decode()) == tuple(case['proxy'])
    scene.assert_calls(raw, [] if case['invalid'] else case['selected'])
    assert_deliveries(scene, case)


@pytest.mark.parametrize('codes,expected', [([1, 0], 1), ([1, 1], 1), ([1, 2], 2)])
def test_callback_errors_keep_order_and_do_not_manufacture_json_control(scene, codes, expected):
    scene.bind()
    diagnostics = [b'first private diagnostic\n', b'second private diagnostic\n']
    for number, code in enumerate(codes):
        scene.outcome(number, stdout=b'' if code else b'{}\n',
                      stderr=diagnostics[number], code=code)
    handler = scene.publish()
    raw = scene.tool_raw()
    result = invoke(handler['command'], raw, scene.root)
    assert (result.returncode, result.stdout, result.stderr) == (
        expected, b'{}\n', b''.join(diagnostics))
    scene.assert_calls(raw, [0, 1])
    for number, code in enumerate(codes):
        captured(scene, b'' if code else b'{}\n', diagnostics[number], code)


@pytest.mark.parametrize('failure', ['invalid-json', 'timeout', 'shell-127', 'spawn-exception'])
def test_failure_is_recorded_and_later_callback_still_runs(scene, failure):
    scene.bind()
    if failure == 'invalid-json':
        scene.outcome(0, stdout=b'not JSON\n')
    elif failure == 'timeout':
        scene.outcome(0, mode='wait')
        scene.change_tool(0, timeout=0.2)
    handler = scene.publish()
    environment = {}
    witness = scene.root / 'spawn-exception.json'
    if failure == 'shell-127':
        scene.probes[0].unlink()
    elif failure == 'spawn-exception':
        environment = {'INITIAL_PROXY_FAIL_POPEN_TARGET': str(scene.probes[0]),
                       'INITIAL_PROXY_FAULT_WITNESS': str(witness)}
    raw = scene.tool_raw()
    result = invoke(handler['command'], raw, scene.root, environment=environment)
    assert result.returncode == 1 and result.stdout == b'{}\n'
    assert result.stderr
    expected_calls = [1] if failure in ('shell-127', 'spawn-exception') else [0, 1]
    scene.assert_calls(raw, expected_calls)
    captured(scene, b'{}\n', b'', 0)
    if failure == 'invalid-json':
        captured(scene, b'not JSON\n', b'', 0)
    elif failure == 'timeout':
        assert any(row.get('timed_out') is True and row['capture_complete'] is True
                   for row in runner_records(scene))
    elif failure == 'shell-127':
        assert any(row['actual_exit_code'] == 127 and row['capture_complete'] is True
                   for row in runner_records(scene))
    else:
        assert json.loads(witness.read_text())['error'] == 'fixture actual Popen failure'
        captured(scene, b'', b'', None)
        documents = [path.read_text() for path in scene.capture_roots['proxy-fixture-session'].rglob('*.json')]
        assert any('RegisteredLaunchFailure' in text and 'fixture actual Popen failure' in text
                   for text in documents)


def test_complete_streams_are_separate_per_invocation_and_session_and_follow_cleanup(scene):
    first = scene.bind(session='tool-capture-a')
    second = scene.bind(session='tool-capture-b')
    second.mkdir(parents=True, exist_ok=True)
    marker = second / 'unrelated-session-marker'
    marker.write_bytes(b'keep second session')
    handler = scene.publish()
    original = (FIXTURES / 'large-input.txt').read_bytes()
    groups = []
    for session, suffix in [('tool-capture-a', b'first'), ('tool-capture-a', b'second'),
                            ('tool-capture-b', b'third')]:
        diagnostic = original + suffix
        scene.outcome(0, stdout=b'', stderr=diagnostic, code=1)
        scene.reset_calls()
        raw = scene.tool_raw(session=session, large=True)
        result = invoke(handler['command'], raw, scene.root, session)
        assert (result.returncode, result.stdout, result.stderr) == (1, b'{}\n', diagnostic)
        scene.assert_calls(raw, [0, 1])
        groups.append(captured(scene, b'', diagnostic, 1, session))
    assert groups[0].isdisjoint(groups[1])
    assert (groups[0] | groups[1]).isdisjoint(groups[2])
    second_bytes = {name: Path(name).read_bytes() for pair in groups[2] for name in pair}
    cleanup_project_session_runtime(scene.services[1].settings.project_config,
                                    scene.bindings['tool-capture-a']['session_id'])
    assert not first.exists()
    assert marker.read_bytes() == b'keep second session'
    assert {name: Path(name).read_bytes() for name in second_bytes} == second_bytes


def test_replay_is_historical_and_stale_revision_preserves_effective_document(scene):
    revision = scene.services[1].revision()
    scene.publish()
    # Use the saved first publication inputs, not a freshly calculated revision.
    first = scene.operation(revision=revision)
    assert first[0] == 0, first
    assert scene.operation('restore')[0] == 0
    restored = scene.hooks.read_bytes()
    assert scene.operation(revision=revision) == first
    assert scene.hooks.read_bytes() == restored
    refusal = scene.operation(request_id='stale-private-pre-tool', revision='0' * 64)
    assert refusal[0] != 0 and 'revision' in json.dumps(refusal).lower()
    assert scene.hooks.read_bytes() == restored


def test_member_update_keeps_both_single_entries_and_restores_logical_changes(scene):
    scene.publish(event='SessionStart')
    scene.publish()
    before = json.loads(scene.hooks.read_text())
    scene.change_tool(1, timeout=12)
    current = json.loads(scene.hooks.read_text())
    assert len(current['hooks']['PreToolUse']) == len(current['hooks']['SessionStart']) == 1
    assert current['hooks']['SessionStart'] == before['hooks']['SessionStart']
    assert current['hooks']['PostToolUse'] == before['hooks']['PostToolUse']
    assert scene.operation('restore')[0] == 0
    restored = json.loads(scene.hooks.read_text())
    assert restored['hooks']['SessionStart'] == before['hooks']['SessionStart']
    assert restored['hooks']['PreToolUse'][0] == scene.tool_members[0]
    assert len(restored['hooks']['PreToolUse']) == 2
    assert restored['hooks']['PreToolUse'][1]['hooks'][0]['timeout'] == 12
    assert restored['hooks']['PreToolUse'][1]['hooks'][0]['statusMessage'] == 'Changed private PreToolUse callback'


def test_interrupted_publication_recovers_through_owner_and_keeps_start(scene, monkeypatch):
    import poise.infrastructure.hook_transport as transport
    scene.publish(event='SessionStart')
    before_start = json.loads(scene.hooks.read_text())['hooks']['SessionStart']
    revision = scene.services[1].revision()
    original = transport.atomic_write
    reached = []

    def replace_then_fail(path, data, mode):
        original(path, data, mode)
        if Path(path) == scene.hooks:
            reached.append(True)
            raise OSError('private failure after native replacement')

    with monkeypatch.context() as patch:
        patch.setattr(transport, 'atomic_write', replace_then_fail)
        interrupted = scene.operation(revision=revision)
    assert reached, ('PRE_TOOL_PROXY_PUBLICATION_BOUNDARY', interrupted)
    assert interrupted[0] != 0
    recovered = scene.operation(revision=revision)
    assert recovered[0] == 0, recovered
    current = json.loads(scene.hooks.read_text())
    assert len(current['hooks']['PreToolUse']) == 1
    assert current['hooks']['SessionStart'] == before_start
    assert scene.operation(revision=revision) == recovered
