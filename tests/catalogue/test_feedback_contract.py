"""A return to collection/reasoning must be able to propose an actual repair."""
import pytest
from harness.modules.inspection.domain import FeedbackBook
from harness.modules.workflow.handlers import ProduceHandler,ObserveHandler,CheckHandler,InspectHandler
from harness.modules.foundation.errors import HarnessError


def book():
    return FeedbackBook.empty().inspect([{'id':'F','subject':'prior result','description':'Missing evidence',
             'evidence':'The previous delivered result omitted the source.'}],[],'audit',1)


def proposal():
    return {'id':'R','finding_id':'F','description':'Added the missing source in the new iteration.',
            'evidence':'Current collection section and retained command receipt.'}


@pytest.mark.parametrize('kind',[ProduceHandler,ObserveHandler,CheckHandler])
def test_repeated_substantive_stage_can_propose_resolution_without_new_engine(kind):
    out=kind().evaluate({'resolutions':[proposal()]},book(),'earlier',2)
    assert out.feedback.open_findings
    assert out.feedback.pending_resolutions[0].id=='R'
    accepted=InspectHandler().evaluate({'coverage':'Checked the new source.','findings':[],
               'resolution_decisions':[{'resolution_id':'R','decision':'accepted','reason':'Evidence exists.'}]},
               out.feedback,'audit',2)
    assert accepted.outcome=='clear'


def test_review_again_can_correct_its_report_without_modifying_product():
    work={'coverage':'Repeated product inspection with corrected report.','findings':[],
          'resolution_decisions':[],'resolutions':[proposal()]}
    fixed=InspectHandler().evaluate(work,book(),'review',2)
    assert fixed.feedback.pending_resolutions[0].id=='R'
    assert fixed.outcome=='changes_requested'  # acceptance belongs to the next inspector


def test_inspection_cannot_find_and_propose_fix_for_the_same_new_finding():
    with pytest.raises(HarnessError):
        InspectHandler().evaluate({'coverage':'Check','findings':[{'id':'F','subject':'x','description':'x','evidence':'x'}],
            'resolution_decisions':[],'resolutions':[proposal()]},FeedbackBook.empty(),'audit',1)
