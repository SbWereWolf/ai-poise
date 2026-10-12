"""Agent summaries stay bounded while complete history remains authoritative."""
from copy import deepcopy
import json

import pytest

from poise.modules.result_views.domain import bounded_envelope, response_required


@pytest.mark.parametrize('count', [0, 15, 10000])
@pytest.mark.parametrize('status', ['integrated', 'blocked', 'rejected'])
def test_historical_records_are_not_mandatory_inline(count, status):
    checks = [dict(id=str(index), method='CHECK', actual_exit_code=0, passed=True)
              for index in range(count)]
    history = [dict(task='T1', checks=checks)]
    result = dict(status=status, historical_receipts=history)
    original = deepcopy(result)
    text = bounded_envelope(
        result, '/saved/response.json', 2200, {}, {}, [],
        required_fields=dict(historical_receipts=history))
    view = json.loads(text)
    assert len(text) <= 2200
    assert view['status'] == status
    assert view['response_path'] == '/saved/response.json'
    assert 'historical_receipts' not in view
    assert result == original


def test_even_small_full_result_is_not_inlined_as_agent_view():
    result = dict(status='read_only', internal_detail='complete saved data')
    view = json.loads(bounded_envelope(result, '/saved/response.json', 2200,
                                       {}, {}, []))
    assert view['status'] == 'read_only'
    assert view['response_path'] == '/saved/response.json'
    assert 'internal_detail' not in view


def test_small_result_also_requires_a_full_output_file():
    assert response_required(dict(status='read_only'), 2200) is True
