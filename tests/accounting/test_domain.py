from copy import deepcopy
import pytest
from poise.modules.foundation.errors import PoiseError
from poise.modules.accounting.domain import (BenefitDefinition, MetricPolicy, UsageSample,
    usage_contribution, line_delta, parse_telemetry)


def policy():
    return {'timezone':'UTC','week_start':0,'max_events':1000,'max_files':1000,
        'storage':{'database':'accounting-fixture/observed.sqlite','lock':'accounting-fixture/observed.lock'},
        'max_blob_bytes':1048576,'time_mode':'tool_cycle',
        'causes':['initial','internal_qa','delivered_rework','requirement_change','poise_incident'],
        'sources':{'test':'reported','codex':'observed'},
        'path_categories':{'code':['src/**'],'tests':['tests/**'],'fixtures':['fixtures/**'],'documentation':['docs/**','*.md']},
        'tokenizer':{'kind':'unavailable','identity':'not-configured','command':None}}


def sample(event='e1',mode='delta',sequence=1,inp=100,out=20):
    return {'source':'test','stream':'stream','event_id':event,'sequence':sequence,'mode':mode,
        'occurred_at':'2026-09-07T00:00:00+00:00',
        'counters':{'input_tokens':inp,'output_tokens':out,'total_tokens':inp+out,
                    'cached_input_tokens':inp//2,'reasoning_tokens':out//2}}


def test_details_are_subsets_not_extra_spend():
    event=UsageSample.parse(sample(),MetricPolicy.parse(policy()))
    assert usage_contribution(None,event)['total_tokens']==120


def test_cumulative_requires_baseline_and_does_not_charge_initial_history():
    p=MetricPolicy.parse(policy())
    first=UsageSample.parse(sample('base','baseline',0,1000,200),p)
    next_=UsageSample.parse(sample('next','cumulative',1,1100,230),p)
    assert usage_contribution(None,first)['total_tokens']==0
    assert usage_contribution(first,next_)['total_tokens']==130
    with pytest.raises(PoiseError):usage_contribution(None,next_)


@pytest.mark.parametrize('key,value',[('input_tokens',-1),('total_tokens',999),('cached_input_tokens',101),('reasoning_tokens',21),('output_tokens',True)])
def test_invalid_usage_fails_not_estimated(key,value):
    raw=sample();raw['counters'][key]=value
    with pytest.raises(PoiseError):UsageSample.parse(raw,MetricPolicy.parse(policy()))


def test_missing_details_remain_unknown():
    raw=sample();raw['counters']['cached_input_tokens']=None
    event=UsageSample.parse(raw,MetricPolicy.parse(policy()))
    assert usage_contribution(None,event)['cached_input_tokens'] is None


def test_counter_reset_requires_new_explicit_stream():
    p=MetricPolicy.parse(policy());a=UsageSample.parse(sample('b','baseline',0,100,20),p)
    b=UsageSample.parse(sample('x','cumulative',1,90,20),p)
    with pytest.raises(PoiseError):usage_contribution(a,b)


def test_useful_delta_counts_deletions_and_exact_newline_bytes():
    delta=line_delta(b'a=1\n',b'a=2\n')
    assert delta['added_lines']==delta['removed_lines']==1
    assert delta['added_bytes']==delta['removed_bytes']==4
    assert delta['added_text']=='a=2\n' and delta['removed_text']=='a=1\n'
    assert line_delta(b'a\r\n',b'')['removed_bytes']==3
    assert line_delta('Привет\n'.encode(),b'')['removed_bytes']==13


def test_final_state_not_sum_of_intermediate_rewrites():
    assert line_delta(b'one\n',b'one\n')['added_lines']==0
    assert line_delta(b'old\n',b'new\n')['removed_lines']==1


def test_binary_is_unmeasurable_not_text_zero():
    with pytest.raises(PoiseError):line_delta(b'\x00bin',b'x')


def test_benefit_has_explicit_empty_categories_and_sections():
    assert BenefitDefinition.parse({'git_categories':[],'sections':[]}).git_categories==()
    with pytest.raises(PoiseError):BenefitDefinition.parse({'sections':[]})


@pytest.mark.parametrize('field',list(policy()))
def test_no_missing_policy_defaults(field):
    raw=policy();del raw[field]
    with pytest.raises(PoiseError):MetricPolicy.parse(raw)


def test_telemetry_is_batch_and_cause_not_inferred():
    raw={'usage':[sample()], 'intervals':[], 'cause':None,'finding_targets':[]}
    parsed=parse_telemetry(raw,MetricPolicy.parse(policy()))
    assert parsed['cause'] is None and len(parsed['usage'])==1
    raw['cause']='misspelled_rework'
    with pytest.raises(PoiseError):parse_telemetry(raw,MetricPolicy.parse(policy()))
