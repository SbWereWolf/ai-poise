from copy import deepcopy
import pytest
from poise.common import PoiseError
from .helpers import settings


def test_explicit_transfer_config_validates_all_paths_and_limits(project,recovery_tool):
    from poise.modules.transfers.domain import TransferPolicy
    from .compact_helpers import configure
    configure(project, recovery_tool)
    configured = project['cfg']['runtime_services']['transfer']
    p=TransferPolicy.parse(configured)
    assert p.data['max_tasks']==1000
    for key in configured.keys() - {'recovery'}:
        raw=deepcopy(configured);del raw[key]
        with pytest.raises(PoiseError):TransferPolicy.parse(raw)


def test_transfer_policy_allows_absent_recovery(project):
    from poise.modules.transfers.domain import TransferPolicy
    raw = deepcopy(project['cfg']['runtime_services']['transfer'])
    del raw['recovery']
    assert TransferPolicy.parse(raw).data == raw


@pytest.mark.parametrize('recovery', [None, {}])
def test_present_recovery_must_be_complete(project, recovery):
    from poise.modules.transfers.domain import TransferPolicy
    raw = deepcopy(project['cfg']['runtime_services']['transfer'])
    raw['recovery'] = recovery
    with pytest.raises(PoiseError):
        TransferPolicy.parse(raw)


def test_reserved_filenames_cannot_overlap(project,recovery_tool):
    from poise.modules.transfers.domain import TransferPolicy
    from .compact_helpers import configure
    configure(project, recovery_tool)
    raw=deepcopy(project['cfg']['runtime_services']['transfer'])
    TransferPolicy.parse(raw)
    raw['manifest']=raw['database']
    with pytest.raises(PoiseError):TransferPolicy.parse(raw)


def test_selection_is_explicit_unique_and_bounded():
    from poise.modules.transfers.domain import TransferRequest
    raw={'action':'export','request_id':'x','task_ids':['A','B'],'sprint_id':None,'handoff':None}
    assert TransferRequest.parse(raw,settings()).data==raw
    for ids in ([],['A','A'],['../escape']):
        value=deepcopy(raw);value['task_ids']=ids
        with pytest.raises(PoiseError):TransferRequest.parse(value,settings())
    value=deepcopy(raw);value['sprint_id']='S'
    with pytest.raises(PoiseError):TransferRequest.parse(value,settings())
