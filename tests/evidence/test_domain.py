"""Evidence contracts first: no I/O, factual observations vs semantic decisions."""
import copy
import pytest

from poise.modules.evidence.domain import EvidencePlan, EvidenceBook
from poise.modules.foundation.errors import DomainError


def plan(kind='logical', phase='continue'):
    return EvidencePlan.parse({
        'measure': {'subject_methods':{'M':{'exit_codes':[0,1],'stdout_contains':['observed=3'],'stderr_contains':[]}}, 'arguments':[
            {'id':'A','kind':kind,'phase':phase,'observation_methods':['M']}], 'review_arguments':[]},
        'audit': {'subject_methods':{}, 'arguments':[], 'review_arguments':['A']},
    }, {'measure':'observe','audit':'inspect'}, {'measure':['M'],'audit':[]})


def receipt(ok=True, timeout=False, id='RUN'):
    return {'id':id, 'guard':False, 'interpretable':True, 'method':'M', 'obligations':['M'], 'passed':ok, 'timed_out':timeout,
            'actual_exit_code':0 if ok else 1, 'capture_complete':True,
            'tree':'TREE', 'stdout':'/task/run/out', 'stderr':'/task/run/err'}


def argument(verdict='proved'):
    return {'id':'A','kind':'logical','facts':['Наблюдение сообщает X'], 'assumptions':[],
            'inference':'По определению X удовлетворяет критерию.', 'conclusion':'Критерий выполнен.',
            'verdict':verdict,'observation_ids':['RUN']}


def test_plan_rejects_missing_stage_and_unknown_method():
    with pytest.raises(DomainError):
        EvidencePlan.parse({}, {'measure':'observe'}, {'measure':['M']})
    with pytest.raises(DomainError):
        EvidencePlan.parse({'measure':{'subject_methods':{'UNKNOWN':{'exit_codes':[0],'stdout_contains':[],'stderr_contains':[]}},'arguments':[],'review_arguments':[]}},
                           {'measure':'observe'}, {'measure':['M']})


def test_subject_methods_only_belong_to_observe_check():
    with pytest.raises(DomainError):
        EvidencePlan.parse({'s':{'subject_methods':{'M':{'exit_codes':[0,1],'stdout_contains':['observed=3'],'stderr_contains':[]}},'arguments':[],'review_arguments':[]}},
                           {'s':'produce'}, {'s':['M']})


def test_observation_negative_predicate_is_not_execution_failure():
    p=plan(); b=EvidenceBook.empty().record_batch('measure',1,'TREE','KEY',[receipt(False)])
    r=b.assess(p,'measure',1,'TREE','KEY',{'phase':'prepare','arguments':[],'decisions':[]})
    assert not r.ready and r.needs_continuation
    assert r.book.to_dict()['batches'][0]['receipts'][0]['passed'] is False


def test_timeout_cannot_be_a_negative_subject_proof():
    b=EvidenceBook.empty().record_batch('measure',1,'TREE','KEY',[receipt(False,True)])
    r=b.assess(plan(),'measure',1,'TREE','KEY',{'phase':'prepare','arguments':[],'decisions':[]})
    assert not r.ready and not r.needs_continuation


def test_continue_requires_existing_current_observations():
    with pytest.raises(DomainError):
        EvidenceBook.empty().assess(plan(),'measure',1,'TREE','KEY',
                                   {'phase':'continue','arguments':[argument()],'decisions':[]})


def test_continue_seals_argument_but_does_not_accept_own_proof():
    b=EvidenceBook.empty().record_batch('measure',1,'TREE','KEY',[receipt()])
    r=b.assess(plan(),'measure',1,'TREE','KEY',{'phase':'continue','arguments':[argument()],'decisions':[]})
    assert r.ready and r.outcome == 'complete'
    assert len(r.book.to_dict()['arguments']) == 1
    assert r.book.to_dict()['decisions'] == []
    repeated=r.book.assess(plan(),'measure',1,'TREE','KEY',{'phase':'continue','arguments':[argument()],'decisions':[]})
    assert repeated.book == r.book


def test_unknown_or_stale_observation_is_rejected():
    b=EvidenceBook.empty().record_batch('measure',1,'TREE','KEY',[receipt()])
    for ids,tree,key in [(['ALIEN'],'TREE','KEY'),(['RUN'],'NEW','NEWKEY')]:
        a=argument(); a['observation_ids']=ids
        with pytest.raises(DomainError):
            b.assess(plan(),'measure',1,tree,key,{'phase':'continue','arguments':[a],'decisions':[]})


def test_review_decision_is_bound_to_exact_argument_revision():
    p=plan(); b=EvidenceBook.empty().record_batch('measure',1,'TREE','KEY',[receipt()])
    b=b.assess(p,'measure',1,'TREE','KEY',{'phase':'continue','arguments':[argument()],'decisions':[]}).book
    a=b.to_dict()['arguments'][0]
    decision={'argument_id':'A','revision':a['revision'],'decision':'rejected','reason':'Не учтено допущение.'}
    r=b.assess(p,'audit',1,'TREE','AUDIT',{'phase':'prepare','arguments':[],'decisions':[decision]})
    assert r.ready and r.outcome == 'changes_requested'
    assert r.book.to_dict()['arguments'] == b.to_dict()['arguments']
    decision['revision']='STALE'
    with pytest.raises(DomainError):
        b.assess(p,'audit',1,'TREE','AUDIT',{'phase':'prepare','arguments':[],'decisions':[decision]})


def test_argument_has_structure_not_automatic_truth_validation():
    a=argument(); a['facts']=[]
    b=EvidenceBook.empty().record_batch('measure',1,'TREE','KEY',[receipt()])
    with pytest.raises(DomainError):
        b.assess(plan(),'measure',1,'TREE','KEY',{'phase':'continue','arguments':[a],'decisions':[]})


def test_book_roundtrip_and_conflicting_batch_identity():
    b=EvidenceBook.empty().record_batch('measure',1,'TREE','KEY',[receipt()])
    assert EvidenceBook.from_dict(b.to_dict()) == b
    assert b.record_batch('measure',1,'TREE','KEY',[receipt()]) == b
    with pytest.raises(DomainError):
        b.record_batch('measure',1,'TREE','KEY',[receipt(False)])


def test_argument_A_B_A_selects_current_A_not_historical_B():
    p=plan(); b=EvidenceBook.empty().record_batch('measure',1,'TREE','KEY',[receipt()])
    a=argument(); different={**a,'inference':'Альтернативный аргумент.'}
    for body in [a,different,a]:
        b=b.assess(p,'measure',1,'TREE','KEY',{'phase':'continue','arguments':[body],'decisions':[]}).book
    assert b.latest_argument('A')['body']==a
    assert len(b.to_dict()['arguments'])==3
    assert b.assess(p,'measure',1,'TREE','KEY',{'phase':'continue','arguments':[a],'decisions':[]}).book==b


def test_pre_observation_argument_can_use_verified_earlier_stage_observation():
    p=EvidencePlan.parse({
        'baseline':{'subject_methods':{'M':{'exit_codes':[0,1],'stdout_contains':['observed=3'],'stderr_contains':[]}},'arguments':[],'review_arguments':[]},
        'reason':{'subject_methods':{},'arguments':[{'id':'A','kind':'logical','phase':'prepare','observation_methods':['M']}],'review_arguments':[]}},
        {'baseline':'observe','reason':'check'},{'baseline':['M'],'reason':[]})
    b=EvidenceBook.empty().record_batch('baseline',1,'TREE','KEY',[receipt()])
    result=b.assess(p,'reason',1,'TREE','OTHER',{'phase':'prepare','arguments':[argument()],'decisions':[]})
    assert result.ready and result.outcome=='satisfied'


@pytest.mark.parametrize('verdict,expected',[('proved','satisfied'),('disproved','not_satisfied'),('inconclusive','inconclusive')])
def test_logical_check_outcomes_are_subject_verdicts(verdict,expected):
    p=EvidencePlan.parse({'s':{'subject_methods':{},'arguments':[{'id':'A','kind':'logical','phase':'prepare','observation_methods':[]}],'review_arguments':[]}}, {'s':'check'},{'s':[]})
    a=argument(verdict);a['observation_ids']=[]
    result=EvidenceBook.empty().assess(p,'s',1,'TREE','KEY',{'phase':'prepare','arguments':[a],'decisions':[]})
    assert result.ready and result.outcome==expected


def test_own_acceptance_and_unknown_decision_values_rejected():
    b=EvidenceBook.empty().record_batch('measure',1,'TREE','KEY',[receipt()])
    for d in [{'argument_id':'A','revision':'X','decision':'accepted','reason':'self'},
              {'argument_id':'A','revision':'X','decision':'invented','reason':'self'}]:
        with pytest.raises(DomainError):
            b.assess(plan(),'measure',1,'TREE','KEY',{'phase':'continue','arguments':[argument()],'decisions':[d]})


def test_nonzero_signal_is_not_a_product_disproof():
    r=receipt(False);r['actual_exit_code']=-9
    b=EvidenceBook.empty().record_batch('measure',1,'TREE','KEY',[r])
    result=b.assess(plan(),'measure',1,'TREE','KEY',{'phase':'prepare','arguments':[],'decisions':[]})
    assert not result.ready and not result.needs_continuation


def failed_rework_batch(value):
    return EvidenceBook.empty().record_submission_batch(
        'implementation', 1, 'TREE', 'KEY', 'SUBMISSION', [value]
    )


def test_completed_uninterpretable_subject_batch_is_available_only_for_explicit_rework():
    value=receipt(False)
    value.update(guard=False,interpretable=False,actual_exit_code=1)
    book=failed_rework_batch(value)

    selected=book.failed_batch('implementation',1,'SUBMISSION','TREE','KEY')

    assert selected is not None
    assert selected['receipts'] == [value]


@pytest.mark.parametrize(
    ('changes', 'available'),
    [
        ({'timed_out':True,'actual_exit_code':-9,'capture_complete':True}, True),
        ({'actual_exit_code':None}, False),
        ({'actual_exit_code':-9,'capture_complete':True}, True),
        ({'timed_out':True,'actual_exit_code':-9,'capture_complete':False}, False),
    ],
)
def test_rework_distinguishes_terminal_negative_from_incomplete_capture(changes, available):
    value=receipt(False)
    value.update(guard=False,interpretable=False,actual_exit_code=1)
    value.update(changes)
    book=failed_rework_batch(value)

    selected=book.failed_batch('implementation',1,'SUBMISSION','TREE','KEY')
    assert (selected is not None) is available
    if available:
        assert selected['receipts'] == [value]


def test_interpretable_negative_subject_is_not_misclassified_as_failed_check_rework():
    value=receipt(False)
    value.update(guard=False,interpretable=True,actual_exit_code=1)
    book=failed_rework_batch(value)

    assert book.failed_batch('implementation',1,'SUBMISSION','TREE','KEY') is None


@pytest.mark.parametrize(
    'identity',
    [
        ('other',1,'SUBMISSION','TREE','KEY'),
        ('implementation',2,'SUBMISSION','TREE','KEY'),
        ('implementation',1,'OTHER','TREE','KEY'),
        ('implementation',1,'SUBMISSION','OTHER','KEY'),
        ('implementation',1,'SUBMISSION','TREE','OTHER'),
    ],
)
def test_failed_rework_batch_requires_exact_stage_iteration_submission_tree_and_execution(identity):
    value=receipt(False)
    value.update(guard=False,interpretable=False,actual_exit_code=1)
    book=failed_rework_batch(value)

    assert book.failed_batch(*identity) is None
