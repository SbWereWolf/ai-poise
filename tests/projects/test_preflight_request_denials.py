"""Raw transport and typed request boundaries, with independent literal cases."""
import json

import pytest

from tests.conftest import write_json
from .test_project_preflight import (
    FIXTURES, case, expected, fixture, invoke, invoke_raw, request,
)


RAW = json.loads((FIXTURES / 'raw-request-denials.json').read_text())
FIELDS = json.loads((FIXTURES / 'invalid-request-fields.json').read_text())


def assert_input_denial(case, actual, reason, context):
    assert actual == {
        **fixture('success.json', case[3]),
        'status': 'rejected', 'ready': False, 'reason': reason, 'context': context,
        'checks': fixture('checks-not-checked.json', {}),
        'recovery': fixture('recovery.json', {**case[3], 'config': context['config_path']}),
    }


@pytest.mark.parametrize('record', RAW, ids=[item['id'] for item in RAW])
def test_raw_strict_json_denial_is_structured_readonly_and_uses_configured_exit(case, record):
    raw = bytes.fromhex(record['hex']) if 'hex' in record else record['text'].encode()
    # Configuration cannot be read; raw validation must precede its owner.
    case[0]['config_path'].unlink()
    code, actual = invoke_raw(case, raw)
    assert code == 23
    assert_input_denial(case, actual, record['cause'], fixture('input-context-unobserved.json', case[3]))


def test_raw_input_limit_refuses_before_parsing_or_selected_owner(case):
    settings = case[1]
    raw_settings = json.loads(settings.read_text())
    raw_settings['max_input_bytes'] = 8
    write_json(settings, raw_settings)
    case[0]['config_path'].unlink()
    code, actual = invoke_raw(case, b' ' * 9)
    assert code == 23
    assert_input_denial(case, actual, 'Project input limit exceeded',
                       fixture('input-context-unobserved.json', case[3]))


def test_raw_input_at_exact_limit_can_complete_all_mandatory_checks(case):
    settings = case[1]
    body = json.dumps(request(case)).encode()
    raw_settings = json.loads(settings.read_text())
    raw_settings['max_input_bytes'] = len(body)
    write_json(settings, raw_settings)
    assert invoke_raw(case, body) == (17, expected(case))


@pytest.mark.parametrize('record', FIELDS, ids=[item['id'] for item in FIELDS])
def test_missing_empty_or_wrong_type_selector_never_chooses_a_fallback(case, record):
    values = fixture('invalid-request-fields.json', case[3])
    record = next(item for item in values if item['id'] == record['id'])
    body = request(case)
    if record.get('omit'):
        del body[record['field']]
    else:
        body[record['field']] = record['value']
    case[0]['config_path'].unlink()
    code, actual = invoke(case, body)
    assert code == 23
    # The planned request-validation contract leaves wording to its owner, but
    # it must name the actual invalid field, never a downstream missing file.
    assert isinstance(actual['reason'], str) and record['field'] in actual['reason']
    context = fixture('input-context-unobserved.json', case[3])
    context['config_path'] = record['context_config_path']
    context['requested_project_id'] = record['context_project_id']
    assert_input_denial(case, actual, actual['reason'], context)


def test_empty_commit_string_is_supplied_candidate_not_a_null_or_skipped_check(case):
    body = request(case)
    body['commit_message'] = ''
    reason = 'Сообщение коммита не соответствует правилу проекта'
    wanted = expected(case, status='rejected', ready=False, reason=reason,
                      statuses=[('configuration', 'passed', None), ('git', 'passed', None),
                                ('commit_policy', 'rejected', reason), ('task_lookup', 'not_checked', None)])
    assert invoke(case, body) == (23, wanted)
