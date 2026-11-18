"""Real old-producer controls: these must be GREEN before proxy implementation."""
import json
import pytest

from .initial_proxy_support import invoke
from .post_tool_proxy_support import PostToolScene


@pytest.fixture
def scene(project, tmp_path):
    return PostToolScene(project, tmp_path)


def test_old_foreign_metric_accepts_complete_native_input_and_can_stay_background(scene):
    raw = scene.tool_raw(oversized=True)
    assert len(raw) > 1048576
    scene.metric_outcome(barrier=True)
    process = scene.start_old_metric(raw)
    try:
        process.stdin.write(raw)
        process.stdin.close()
        process.stdin = None
        scene.assert_metric(raw)
        results = scene.old_sync(scene.tool_raw())
        assert [(row.returncode, row.stdout, row.stderr) for row in results] == [
            (0, b'{}\n', b''), (0, b'{}\n', b'')]
        assert process.poll() is None
        scene.assert_calls(scene.tool_raw(), [0, 1])
        scene.release_metric()
        stdout, stderr = process.communicate(timeout=5)
        assert (process.returncode, stdout, stderr) == (0, b'{"continue":true}\n', b'')
    finally:
        scene.release_metric()
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=5)


@pytest.mark.parametrize('repository,bound,expected', [
    ('poise', False, [1, 1]), ('poise', True, [1, 0]), ('erp', True, [0, 1])], ids=['unbound', 'poise-bound', 'erp-bound'])
def test_actual_native_post_delivery_and_original_binding_refusal(scene, repository, bound, expected):
    if bound:
        scene.bind(repository=repository)
    for number in (0, 1):
        scene.outcome(number, mode='delegate')
    raw = scene.tool_raw(repository=repository)
    results = scene.old_sync(raw)
    assert [row.returncode for row in results] == expected
    for number, row in enumerate(results):
        if expected[number]:
            assert (row.stdout, row.stderr) == (
                b'', b'Native event requires its genuine preceding SessionStart binding\n')
            assert scene.post_deliveries(number) == []
        else:
            assert (row.stdout, row.stderr) == (b'{}\n', b'')
            delivery, = scene.post_deliveries(number)
            assert delivery['status'] == 'completed'
            assert delivery['event']['tool_response'] == json.loads(raw)['tool_response']
            assert delivery['event_receipt']['event'] == 'PostToolUse'
    scene.assert_calls(raw, [0, 1])


def test_old_sync_control_and_full_streams_are_fixture_effects(scene):
    scene.outcome(0, response={'continue': False, 'stopReason': 'literal ERP stop'})
    scene.outcome(1, stdout=b'', stderr=b'literal later failure\n', code=2)
    raw = scene.tool_raw(large=True)
    first, second = scene.old_sync(raw)
    assert (first.returncode, json.loads(first.stdout), first.stderr) == (
        0, {'continue': False, 'stopReason': 'literal ERP stop'}, b'')
    assert (second.returncode, second.stdout, second.stderr) == (2, b'', b'literal later failure\n')
    scene.assert_calls(raw, [0, 1])


@pytest.mark.parametrize('enforce', [False, True], ids=['audit', 'enforce'])
def test_real_native_tool_result_control(project, tmp_path, enforce):
    from .post_tool_proxy_support import actual_output_case
    actual_output_case(project, tmp_path, proxy=False, enforce=enforce)
