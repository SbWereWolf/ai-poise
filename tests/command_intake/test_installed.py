"""The current wheel is consumed outside its checkout, without an editable import."""
import pytest

from tests.command_intake.helpers import Intake, cases
from tests.command_intake.installed_consumer import installed


@pytest.mark.parametrize('case',cases('installed'),ids=lambda x:x['id'])
def test_installed_contract(case,project,tmp_path,monkeypatch):
    i=Intake(project,tmp_path,monkeypatch).enable()
    proof=installed(i)
    assert proof['after']['details']['documents'][0]['ranges']==[[1,4]]
    assert proof['recovery']['status']=='cleaned'
