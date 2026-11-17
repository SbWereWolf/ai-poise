"""GREEN controls: real old callbacks, independent of the absent new proxy."""
import pytest

from .initial_proxy_support import invoke
from .pre_tool_proxy_support import (
    PreToolScene, arrange_case, assert_deliveries, baseline_cases,
)


@pytest.fixture
def scene(project, tmp_path):
    return PreToolScene(project, tmp_path)


@pytest.mark.parametrize('case', baseline_cases(), ids=lambda row: row['id'])
def test_actual_old_callback_baseline(scene, case):
    raw = arrange_case(scene, case)
    results = scene.old(raw, case['selected'])
    assert [(row.returncode, row.stdout.decode(), row.stderr.decode()) for row in results] == [
        tuple(row) for row in case['old']]
    scene.assert_calls(raw, case['selected'])
    assert_deliveries(scene, case)


@pytest.mark.parametrize('code', [1, 2])
def test_old_error_keeps_real_code_and_later_callback(scene, code):
    scene.outcome(0, stdout=b'', stderr=b'first private diagnostic\n', code=code)
    raw = scene.tool_raw()
    results = scene.old(raw, [0, 1])
    assert [(row.returncode, row.stdout, row.stderr) for row in results] == [
        (code, b'', b'first private diagnostic\n'), (0, b'{}\n', b'')]
    scene.assert_calls(raw, [0, 1])


def test_existing_start_projection_with_tool_definitions(scene):
    handler = scene.publish(event='SessionStart')
    raw = scene.raw_event()
    result = invoke(handler['command'], raw, scene.root)
    assert (result.returncode, result.stdout, result.stderr) == (0, b'{}\n', b'')
    # The two public tool-definition installs republish their Start groups.
    # The untouched third member consequently precedes those two groups.
    scene.assert_calls(raw, [2, 0, 1])
    assert scene.operation('restore', event='SessionStart')[0] == 0
