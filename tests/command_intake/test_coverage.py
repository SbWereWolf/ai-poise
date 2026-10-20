"""Independent interval, version, interop and generation-policy observations."""
from copy import deepcopy
import os

import pytest

from tests.command_intake.helpers import cases, each_port, fixture


def range_read(i, first, last, name='A'):
    return i.read(f"sed -n '{first},{last}p' docs/{name}.txt")


def denied(i, reader, reason):
    before = i.coverage()
    call, native, result = i.pre(reader)
    assert result['status'] == 'denied'
    assert result['decision']['action'] == 'deny'
    assert result['decision']['reason_code'] == reason, result
    assert result['decision']['action_hint']
    assert native['hookSpecificOutput']['permissionDecision'] == 'deny'
    assert i.plans(call) == []
    assert i.coverage()['details'] == before['details']
    return result


@pytest.mark.parametrize('case', cases('coverage'), ids=lambda x:x['id'])
def test_coverage_contract(case, project, tmp_path, monkeypatch):
    functions = {
        'V01':union_ranges, 'V02':subtract_overlap, 'V03':complete_repeat,
        'V04':covered_toggle, 'V05':mostly_repeat, 'V06':line_equality,
        'V07':fraction_equality, 'V08':one_call_duplicates,
        'V09':operation_repeat, 'V10':small_version, 'V11':large_version,
        'V12':direct_interop, 'V13':generation_targeted,
        'V14':generation_complete, 'V15':generation_file_budget,
        'V16':generation_total_budget,
    }
    for i in each_port(project, tmp_path, monkeypatch):
        # Coverage checks use real observations; expiry has its own controlled clock.
        i.configure(**{'registry.retention.max_pending_age_seconds':60})
        functions[case['id'][:3]](i)


def union_ranges(i):
    for probe in fixture('disjoint-novelty.json'):
        i.restart_episode()
        i.configure(**{'registry.same_generation.min_new_lines':probe['min_lines'],
                       'registry.same_generation.min_new_fraction_percent':probe['min_percent']})
        range_read(i,1,4); range_read(i,4,7); range_read(i,9,10)
        i.ranges([[1,7],[9,10]])
        if probe['action']=='deny':
            denied(i,"sed -n '5,12p' docs/A.txt",'mostly_redundant_document_range')
            i.counts(pending=0,current=1)
        else:
            call, _, result = i.pre("sed -n '5,12p' docs/A.txt")
            assert result['decision']['action'] == 'allow'
            i.ranges([[1,7],[9,10]])  # Pre is not delivered content.
            i.counts(pending=1,current=1)
            _,post=i.post(call, i.actual(call))
            assert post['status']=='settled' and post['counts']['registered']==1
            i.counts(pending=0,current=1)
            i.ranges([[1,12]])


def subtract_overlap(i):
    range_read(i,1,10)
    pre, _ = range_read(i,8,20)
    assert pre['decision']['action']=='allow'
    i.ranges([[1,20]])


def complete_repeat(i):
    i.read('cat docs/A.txt')
    denied(i,"sed -n '1,4p' docs/A.txt",'duplicate_document_range')
    denied(i,'cat docs/A.txt','duplicate_complete_document')
    assert i.document()['complete'] is True


def covered_toggle(i):
    range_read(i,2,8)
    denied(i,"sed -n '3,6p' docs/A.txt",'duplicate_document_range')
    i.configure(**{'registry.same_generation.deny_fully_covered_ranges':False})
    _, post = range_read(i,3,6)
    assert post['counts']['registered']==1
    i.ranges([[2,8]])


def mostly_repeat(i):
    range_read(i,1,35,'long')
    denied(i,"sed -n '1,40p' docs/long.txt",'mostly_redundant_document_range')
    i.ranges([[1,35]],'long')


def line_equality(i):
    range_read(i,1,92,'long'); range_read(i,1,100,'long')
    i.ranges([[1,100]],'long')


def fraction_equality(i):
    range_read(i,1,16); range_read(i,1,20)
    i.ranges([[1,20]])


def one_call_duplicates(i):
    reader='head -n4 docs/A.txt docs/A.txt'
    denied(i,reader,'duplicate_document_range')
    i.counts(pending=0,current=0)
    i.mode('audit')
    pre, post=i.read(reader)
    assert pre['decision']['action']=='audit'
    assert post['status']=='settled'
    i.ranges([[1,4]])
    i.counts(pending=0,current=1)


def operation_repeat(i):
    reader="awk 'NR==2 {print}' docs/A.txt"
    i.read(reader)
    denied(i,reader,'duplicate_document_operation')
    i.read("awk 'NR==3 {print}' docs/A.txt")
    i.advance()
    denied(i,reader,'unbounded_reread_after_generation')
    i.configure(**{'registry.post_generation.unknown_repeat_behavior':'allow_with_audit'})
    pre,_=i.read(reader)
    assert pre['decision']['action']=='audit'
    assert i.document(generation=1)['operation_count']==1


def small_version(i):
    range_read(i,1,4)
    path=i.s.checkout/'docs/A.txt'; before=path.stat()
    os.utime(path,ns=(before.st_atime_ns,before.st_mtime_ns+2_000_000_000))
    denied(i,'head -n4 docs/A.txt','duplicate_document_range')
    old=i.document()['version_key']
    path.write_bytes(b'01 Z\n'+path.read_bytes()[5:])
    range_read(i,1,4)
    assert i.document()['version_key']!=old
    assert i.coverage()['details']['counts']['stale_documents']==1


def large_version(i):
    i.configure(**{'registry.versioning.hash_max_bytes':1})
    range_read(i,1,4)
    path=i.s.checkout/'docs/A.txt'; old=i.document()['version_key']; before=path.stat()
    os.utime(path,ns=(before.st_atime_ns,before.st_mtime_ns+2_000_000_000))
    range_read(i,1,4)
    assert i.document()['version_key']!=old
    old=i.document()['version_key'];path.write_bytes(path.read_bytes()+b'21 A\n')
    range_read(i,1,4)
    assert i.document()['version_key']!=old


def direct_interop(i):
    direct=i.s.read(path='docs/A.txt',start=1,end=5)
    assert direct['items'][0]['content']=='01 A\n02 A\n03 A\n04 A\n05 A\n'
    denied(i,'head -n5 docs/A.txt','duplicate_document_range')
    range_read(i,6,10)
    i.ranges([[1,10]])
    result=i.s.read(path='docs/A.txt',start=6,end=10)
    assert result['items'][0]['content']=='06 A\n07 A\n08 A\n09 A\n10 A\n'
    repeat=i.s.read(path='docs/A.txt',start=6,end=10)
    assert repeat['items'][0]['status']=='unchanged'
    assert repeat['items'][0]['previous_read_available'] is True
    before=i.coverage()['details']
    i.s.error(i.s.invoke(i.s.request(path='docs/A.txt',start=100,end=101)),
              'range_after_eof',key='sample')
    assert i.coverage()['details']==before
    assert i.document()['last_generation']==0


def generation_row(i, row, mode):
    i.restart_episode()
    i.policy=fixture('policy.json'); i.mode(mode)
    # Seed the old completed reads with a policy admitting two complete files.
    for name, selection in row['prior'].items():
        if selection=='complete':i.read('cat docs/'+name+'.txt')
        else:
            for first,last in selection:range_read(i,first,last,name)
    old=i.coverage()
    assert old['context']['generation']==0
    assert old['details']['counts']['pending']==0
    i.advance()
    i.counts(pending=0,current=0)
    fields={'classifier.bounded_reads.'+k:v for k,v in
            fixture('cases.json')['common_vectors']['post_generation']['classifier_bounds'].items()}
    fields.update({'registry.post_generation.allow_targeted_reread':row.get('targeted',True),
                   'registry.post_generation.deny_complete_reread':row.get('deny_complete',True),
                   'registry.post_generation.max_lines_per_file':row.get('per_file_limit',4),
                   'registry.post_generation.max_total_lines':row.get('total_limit',8)})
    i.configure(**fields)
    commands=[]
    for name,selection in row['requested'].items():
        commands.append('cat docs/'+name+'.txt' if selection=='complete' else
                        f"sed -n '{selection[0][0]},{selection[0][1]}p' docs/{name}.txt")
    call,native,pre=i.pre(' && '.join(commands))
    should_deny=row['decision'].startswith('deny')
    if should_deny:
        assert pre['decision']['action']==('deny' if mode=='enforce' else 'audit')
        assert pre['decision']['message'] and pre['decision']['action_hint']
        reasons={'deny_targeted_reread_disabled':'post_generation_reread_disabled',
                 'deny_complete_reread':'complete_reread_after_generation',
                 'deny_post_generation_file_budget':'post_generation_file_budget_exceeded',
                 'deny_post_generation_total_budget':'post_generation_total_budget_exceeded'}
        assert pre['decision']['reason_code']==reasons[row['decision']]
    else:
        assert pre['decision']['action'] in {'allow','audit'}
    i.counts(pending=0 if should_deny and mode=='enforce' else 1,current=0)
    if should_deny and mode=='enforce':
        assert native['hookSpecificOutput']['permissionDecision']=='deny'
        assert i.plans(call)==[]
        return
    response=i.actual(call);assert response['exit_code']==0
    _,post=i.post(call,response);assert post['status']=='settled'
    expected=row['requested'] if should_deny else row['after_usable']
    i.counts(pending=0,current=len(expected))
    for name,selection in expected.items():
        if selection=='complete':assert i.document(name,generation=1)['complete'] is True
        else:i.ranges(selection,name,generation=1)
    plans=i.plans(call);assert len(plans)==len(expected)
    assert all(x[0]==1 for x in plans)


def generation_matrix(i,prefix):
    rows=[row for row in fixture('cases.json')['common_vectors']['post_generation']['rows']
          if row['case'].startswith(prefix)]
    for row in rows:
        for mode in ['enforce','audit']:generation_row(i,row,mode)
    if prefix=='V13':
        # Cross both prior shapes with both toggle values, not just two examples.
        original=next(x for x in rows if x['case']=='V13-enabled')
        extra=deepcopy(original);extra['prior']={'A':[[1,12]]}
        for mode in ['enforce','audit']:generation_row(i,extra,mode)
        extra=deepcopy(next(x for x in rows if x['case']=='V13-disabled'))
        extra['prior']={'A':'complete'}
        for mode in ['enforce','audit']:generation_row(i,extra,mode)


def generation_targeted(i):generation_matrix(i,'V13')
def generation_complete(i):generation_matrix(i,'V14')
def generation_file_budget(i):generation_matrix(i,'V15')
def generation_total_budget(i):generation_matrix(i,'V16')
