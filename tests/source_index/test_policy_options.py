"""Unknown configuration is retained; known options and API remain strict."""
import json
import subprocess

import pytest

from .helpers import Scene, assert_failure, decode, literal, phases


@pytest.mark.parametrize('level', ['policy', 'paths', 'limits'])
def test_unknown_configuration_option_preserves_query(project, tmp_path, level):
    scene = Scene(project, tmp_path); scene.require(); scene.ready()
    selected = scene.policy_data if level == 'policy' else scene.policy_data[level]
    selected['operator_note'] = {'text': 'installation annotation'}
    scene.save()
    before = scene.policy.read_bytes()
    code, result = scene.query()
    assert (code, result['status']) == (0, 'complete'), scene.calls[-1]
    assert result['checkout'] == str(scene.checkout)
    assert result['database'] == str(scene.database)
    assert json.loads(scene.output(result)) == literal('query.json')
    rows = scene.records()
    assert rows
    assert all(row['cwd'] == row['root'] == str(scene.checkout) for row in rows)
    assert all(row['database'] == str(scene.database) for row in rows)
    assert phases(scene).index('update') < phases(scene).index('usages')
    assert scene.policy.read_bytes() == before
    saved = json.loads(before)
    actual = saved if level == 'policy' else saved[level]
    assert actual['operator_note'] == {'text': 'installation annotation'}


@pytest.mark.parametrize('case', ['paths-shape', 'limits-shape', 'request-extra'])
def test_known_configuration_and_request_remain_strict(project, tmp_path, case):
    scene = Scene(project, tmp_path); scene.require()
    if case != 'request-extra':
        field = 'paths' if case == 'paths-shape' else 'limits'
        scene.policy_data[field] = []
        scene.save()
    before = scene.policy.read_bytes()
    if case == 'request-extra':
        request = {'operation': 'status', 'arguments': [], 'operator_note': 'annotation'}
        completed = subprocess.run(scene.argv(), input=json.dumps(request), text=True,
                                   capture_output=True, cwd=scene.parent,
                                   env=scene.environment, check=False)
        (scene.parent / 'extra-request.stdout.txt').write_text(completed.stdout)
        (scene.parent / 'extra-request.stderr.txt').write_text(completed.stderr)
        code, result = decode(completed)
        field = 'request'
    else:
        code, result = scene.invoke('rebuild')
    assert_failure(code, result, 'rejected', field)
    assert scene.records() == []
    assert not scene.database.exists()
    assert scene.policy.read_bytes() == before
