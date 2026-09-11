import io,json,shutil
from pathlib import Path
from poise.composition import project_tools
from poise.common import load_config
from poise.interfaces.projects import interactive
from .helpers import setup_case

ROOT=Path(__file__).resolve().parents[2]


def test_installed_reference_template_builds_all_thirteen_processes(project):
    root=project['root']
    shutil.copytree(ROOT/'config',root/'catalogue-config')
    # This copy is the explicitly mounted Poise configuration, not the target application.
    shutil.rmtree(root/'config');(root/'catalogue-config').rename(root/'config')
    settings=root/'config/project-setup.json';cfg=json.loads(settings.read_text())
    selection=cfg['templates']['linux-reference']
    blueprint=json.loads((root/selection['path']).read_text())
    values={'project':'real-probe','repository':str(project['app']),'base':'main','remote':'backup',
            'author_name':'Fixture','author_email':'fixture@example.invalid','push':True,'state':'state',
            'environment':['PATH','HOME']}
    request={'schema':'project-setup-1','request_id':'reference','destination':'configured/reference',
             'template':{'id':'linux-reference','version':selection['version'],'digest':selection['digest']},
             'edits':[{'path':q['path'],'value':values[q['id']]} for q in blueprint['questions']],
             'probe_repository':True}
    result=project_tools(settings).apply(request)
    _,_,processes=load_config(Path(result['config_path']))
    assert len(processes)==13
    assert set(processes)==set(blueprint['config']['processes'])
    assert not (Path(result['config_path']).parent/'state').exists()


def test_wizard_eof_is_abort_not_silent_publication(project):
    settings,_,req=setup_case(project);req['edits']=[];out=io.StringIO()
    assert interactive(settings,req,io.StringIO(''),out,io.StringIO())==3
    assert not (project['root']/'configured/pilot').exists()
