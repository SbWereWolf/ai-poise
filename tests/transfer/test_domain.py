from copy import deepcopy
import pytest
from poise.common import PoiseError
from .helpers import settings


def test_explicit_transfer_config_validates_all_paths_and_limits():
    from poise.modules.transfers.domain import TransferPolicy
    p=TransferPolicy.parse(settings())
    assert p.data['max_tasks']==1000
    for key in settings():
        raw=settings();del raw[key]
        with pytest.raises(PoiseError):TransferPolicy.parse(raw)


def test_reserved_filenames_cannot_overlap():
    from poise.modules.transfers.domain import TransferPolicy
    raw=settings();raw['manifest']=raw['database']
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
