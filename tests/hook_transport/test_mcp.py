import sys
from pathlib import Path
from .test_probes import run_probes
from .helpers import command_probe


def spec(mode='ok'):
    p=command_probe('ide')
    p.update(kind='mcp_stdio',argv=[sys.executable,'-B',str(Path(__file__).with_name('mcp_fixture.py')),mode],
        stdout_contains=[],project_bound=True,
        json_assertions=[{'path':['structuredContent','project'],'equals':'${workspace}'}],
        mcp={'protocol_version':'2025-11-25','client_info':{'name':'poise-probe','version':'1'},
             'required_tools':['read_project','rename_refactoring'],
             'call':{'name':'read_project','arguments':{'projectPath':'${workspace}'},'read_only':True},
             'max_messages':20,'max_pages':4,'max_message_bytes':32768,'shutdown_seconds':0.5})
    return p


def test_stdio_handshake_tools_and_project_smoke(tmp_path):
    out=run_probes(tmp_path,[spec()])
    assert out['ready']
    observed=out['observations'][0]
    assert observed['observation']=='mcp_smoke_passed' and observed['version']=='1'
    assert set(observed['tools'])=={'read_project','rename_refactoring'}


def test_tools_list_only_is_not_project_smoke(tmp_path):
    p=spec();p['project_bound']=False;p['json_assertions']=[];p['mcp']['call']=None
    out=run_probes(tmp_path,[p]);assert out['ready']
    assert out['observations'][0]['observation']=='mcp_tools_listed'


def test_missing_advertised_tool(tmp_path):
    p=spec();p['mcp']['required_tools'].append('missing')
    assert not run_probes(tmp_path,[p])['ready']


def test_negotiated_version_mismatch_has_no_fallback(tmp_path):
    assert not run_probes(tmp_path,[spec('version')])['ready']


def test_smoke_error_cannot_mean_available(tmp_path):
    assert not run_probes(tmp_path,[spec('error')])['ready']
    assert not run_probes(tmp_path,[spec('toolerror')])['ready']


def test_different_project_does_not_pass(tmp_path):
    assert not run_probes(tmp_path,[spec('wrong_project')])['ready']


def test_pagination_cycle_is_bounded(tmp_path):
    assert not run_probes(tmp_path,[spec('loop')])['ready']


def test_notifications_do_not_break_handshake(tmp_path):
    assert run_probes(tmp_path,[spec('noisy')])['ready']


def test_deadline_applies_to_whole_handshake(tmp_path):
    p=spec('hang');p['timeout_seconds']=0.05
    assert not run_probes(tmp_path,[p])['ready']
