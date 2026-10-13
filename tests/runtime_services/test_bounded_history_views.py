"""Agent summaries stay bounded while complete history remains authoritative."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from poise.modules.result_views.domain import bounded_envelope, response_required
from poise.common import PoiseError


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


@pytest.mark.parametrize('budget', [2199, 2200, 2201])
def test_exact_2200_unicode_boundary_counts_serialized_lf(budget):
    fixture = json.loads((Path(__file__).with_name('fixtures') /
                          'envelope_2200_boundary.json').read_text())
    result, path = fixture['result'], fixture['response_path']
    minimal = {'status': result['status'], 'response_path': path}
    assert len(json.dumps(minimal, ensure_ascii=False, separators=(',', ':')) + '\n') == 2200
    if budget == 2199:
        with pytest.raises(PoiseError):
            bounded_envelope(result, path, budget, {}, {}, [])
    else:
        text = bounded_envelope(result, path, budget, {}, {}, [])
        assert json.loads(text) == minimal
        assert len(text) == 2200 and text.endswith('\n')


def test_small_agent_view_rejects_missing_full_file_address():
    with pytest.raises(PoiseError):
        bounded_envelope({'status': 'read_only'}, None, 2200, {}, {}, [])
