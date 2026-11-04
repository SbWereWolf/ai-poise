"""Fixture arithmetic and RED discrimination; these do not require new APIs."""
from copy import deepcopy
import hashlib
import json

from tests.skills.portable_delivery_checks import expected_red
from tests.skills.portable_delivery_support import FIXTURES, literal


def test_literal_selection_and_full_frame_agree_on_authored_content_versions():
    selection = literal('selection.json')
    assert selection['materials'] == literal('complete-frame.json')['materials']
    for record in selection['materials']:
        assert record['sha256'] == hashlib.sha256(record['content'].encode()).hexdigest()
    body = literal('read-body.json')['content'].encode()
    expected = literal('read-frame.json')
    assert expected['content'].encode() == body
    assert expected['sha256'] == hashlib.sha256(body).hexdigest()
    for case in literal('ranges.json'):
        spec = case['range']
        if spec is None:
            actual = body
        elif spec['unit'] == 'lines':
            actual = b''.join(body.splitlines(keepends=True)[spec['start']-1:spec['end']])
        else:
            actual = body[spec['start']:spec['end']]
        assert case['content'].encode() == actual
        assert case['complete'] is (actual == body)


def test_red_reporter_accepts_only_the_literal_single_behavioral_failure():
    expected = literal('red-events.json')
    assert expected_red(expected)
    for code in (0, 2, 3, 4, 5):
        assert not expected_red({**expected, 'exit_code': code})
    changed = deepcopy(expected)
    changed['collection_errors'] = ['missing_import']
    assert not expected_red(changed)
    for index in range(3):
        for field, value in [('outcome', 'skipped'), ('exception', 'ImportError'),
                             ('exception', 'AssertionError'), ('phase', 'unexpected')]:
            changed = deepcopy(expected)
            changed['reports'][index][field] = value
            assert not expected_red(changed)
    changed = deepcopy(expected)
    changed['reports'].append(expected['reports'][0])
    assert not expected_red(changed)


def test_fixture_files_are_valid_json_without_runtime_snapshot_updates():
    files = sorted(FIXTURES.glob('*.json'))
    assert files
    for path in files:
        json.loads(path.read_text(encoding='utf-8'))
