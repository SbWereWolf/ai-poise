"""Explicit adapter for a supplied Responses API usage object; performs no API call."""
from ..common import HarnessError


def response_usage(*,source,stream,response_id,sequence,occurred_at,usage):
    if not isinstance(usage,dict) or any(k not in usage for k in ('input_tokens','output_tokens','total_tokens')):
        raise HarnessError('A complete observed Responses usage object is required')
    # Missing optional detail means unavailable, never an invented zero.
    def detail(container,field):
        if container not in usage:return None
        if not isinstance(usage[container],dict):raise HarnessError('Invalid usage detail object')
        return usage[container][field] if field in usage[container] else None
    return {'source':source,'stream':stream,'event_id':response_id,'sequence':sequence,'mode':'delta','occurred_at':occurred_at,
        'counters':{'input_tokens':usage['input_tokens'],'output_tokens':usage['output_tokens'],'total_tokens':usage['total_tokens'],
                    'cached_input_tokens':detail('input_tokens_details','cached_tokens'),
                    'reasoning_tokens':detail('output_tokens_details','reasoning_tokens')}}
