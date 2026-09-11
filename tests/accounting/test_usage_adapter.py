import pytest
from poise.infrastructure.usage_events import response_usage
from poise.modules.accounting.domain import MetricPolicy,UsageSample,usage_contribution
from poise.common import PoiseError
from .test_domain import policy


def convert(usage):return response_usage(source='test',stream='api',response_id='r1',sequence=1,occurred_at='2026-09-07T00:00:00Z',usage=usage)


def test_observed_response_usage_total_includes_reasoning_not_added_twice():
    e=convert({'input_tokens':100,'output_tokens':50,'total_tokens':150,'input_tokens_details':{'cached_tokens':40},'output_tokens_details':{'reasoning_tokens':20}})
    assert usage_contribution(None,UsageSample.parse(e,MetricPolicy.parse(policy())))['total_tokens']==150


def test_absent_optional_details_unknown_and_absent_total_rejected():
    e=convert({'input_tokens':0,'output_tokens':0,'total_tokens':0})
    assert e['counters']['cached_input_tokens'] is None
    assert e['counters']['reasoning_tokens'] is None
    with pytest.raises(PoiseError):convert({'input_tokens':1,'output_tokens':2})
