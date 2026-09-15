"""C004 public opt-in, without an unrestricted new-field config editor."""
import json
import pytest
from poise.common import PoiseError
from .test_update import installed_project, update_tools, update_request


def test_public_editor_adds_only_explicit_routing_section(project,tmp_path):
    settings,config_path,created=installed_project(project)
    config={name:str(tmp_path/(name+'.json')) for name in ('catalog','selection','policy','packages')}
    request=update_request(config_path,created['revision'],manifest_edits=[
        {'path':['development_routing'],'value':config}])
    result=update_tools(settings).apply(request)
    assert result['changed'] is True
    assert json.loads(config_path.read_text())['development_routing']==config
    assert update_tools(settings).apply(request)['replayed'] is True


@pytest.mark.parametrize('name,value',[
    ('invented',{}), ('development_routing',{}),
    ('development_routing',{'catalog':'x','selection':'x','policy':'x','packages':''})])
def test_invalid_or_other_new_section_is_rejected_atomically(project,name,value):
    settings,config_path,created=installed_project(project);before=config_path.read_bytes()
    request=update_request(config_path,created['revision'],manifest_edits=[{'path':[name],'value':value}])
    with pytest.raises(PoiseError):update_tools(settings).apply(request)
    assert config_path.read_bytes()==before
