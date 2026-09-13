import io,json,os,subprocess,sys
from pathlib import Path
from poise.interfaces.projects import execute,interactive
from tests.conftest import write_json
from .helpers import setup_case


def test_batch_cli_one_packet_creates_real_project(project):
    settings,_,req=setup_case(project)
    r=subprocess.run([sys.executable,'-m','poise','project','--settings',str(settings)],input=json.dumps(req),text=True,capture_output=True)
    assert r.returncode==0,r.stderr+r.stdout
    reply=json.loads(r.stdout);assert Path(reply['config_path']).exists()


def test_duplicate_json_keys_rejected_before_any_write(project):
    settings,_,req=setup_case(project);raw=json.dumps(req)[:-1]+',"schema":"other"}'
    out=io.StringIO();code=execute(settings,io.BytesIO(raw.encode()),out)
    assert code==2 and json.loads(out.getvalue())['status']=='rejected'
    assert not (project['root']/'configured/pilot').exists()


def test_interactive_back_change_keep_and_publish(project):
    settings,_,req=setup_case(project);req['edits']=[]
    answers=io.StringIO('set "first"\nkeep\nback\nkeep\nset true\nback\nset false\npublish\n')
    out=io.StringIO();prompts=io.StringIO()
    code=interactive(settings,req,answers,out,prompts)
    assert code==0,prompts.getvalue()+out.getvalue()
    cfg=json.loads(Path(json.loads(out.getvalue())['config_path']).read_text())
    assert cfg['project']=='first' and cfg['git']['push_required'] is False
    assert 'Repository' in prompts.getvalue()


def test_interactive_abort_does_not_publish(project):
    settings,_,req=setup_case(project);req['edits']=[];out=io.StringIO()
    assert interactive(settings,req,io.StringIO('set "x"\nabort\n'),out,io.StringIO())==3
    assert json.loads(out.getvalue())['status']=='aborted'
    assert not (project['root']/'configured/pilot').exists()


def test_interactive_blank_not_silent_accept_and_limits_are_bounded(project):
    settings,_,req=setup_case(project);req['edits']=[]
    cfg=json.loads(settings.read_text());cfg['max_survey_steps']=2;write_json(settings,cfg)
    prompts=io.StringIO();out=io.StringIO()
    assert interactive(settings,req,io.StringIO('\n\n'),out,prompts)==2
    assert not (project['root']/'configured/pilot').exists()


def test_small_reply_budget_rejected_before_project_publication(project):
    settings,_,req=setup_case(project);cfg=json.loads(settings.read_text());cfg['output_chars']=10;write_json(settings,cfg)
    out=io.StringIO()
    assert execute(settings,io.BytesIO(json.dumps(req).encode()),out)==2
    assert not (project['root']/'configured/pilot').exists()
