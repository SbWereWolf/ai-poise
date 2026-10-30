import io,json,shutil
from pathlib import Path
from poise.composition import project_tools
from poise.common import load_config
from poise.interfaces.projects import interactive
from .helpers import setup_case, shipped_setup, shipped_edits

ROOT=Path(__file__).resolve().parents[2]


def test_installed_reference_template_builds_all_thirteen_workflows(project):
    root=project['root']
    settings=shipped_setup(root / 'shipped')
    tools=settings.parent.parent
    cfg=json.loads(settings.read_text())
    blueprint=json.loads((tools/cfg['templates']['common']['path']).read_text())
    data=root.parent/'reference-data'
    request={'schema':'project-setup-1','request_id':'reference','destination':'projects/real-probe/config',
             'template':{'id':'common'},
             'edits':shipped_edits(blueprint,tools=tools,project='real-probe',repository=project['app'],
                                   data=data,trees=project['app']/'task-worktrees'),
             'probe_repository':True}
    result=project_tools(settings).apply(request)
    _,_,workflows=load_config(Path(result['config_path']))
    assert len(workflows)==13
    assert set(workflows)==set(blueprint['config']['workflows'])
    assert (data/'workflows/development.json').is_file()
    assert not (data/'tasks/tasks.sqlite').exists()


def test_wizard_eof_is_abort_not_silent_publication(project):
    settings,_,req=setup_case(project);req['edits']=[];out=io.StringIO()
    assert interactive(settings,req,io.StringIO(''),out,io.StringIO())==3
    assert not (project['root']/'configured/pilot').exists()
