"""One logical batch, exact independent templates, no handwritten managed config."""
from copy import deepcopy
import io
import json
import shutil
from pathlib import Path
import pytest
from poise.application.catalogue import CatalogueCommands
from poise.composition import goal_config_tools
from poise.infrastructure.catalogue import FileCatalogue
from poise.common import PoiseError

ROOT=Path(__file__).resolve().parents[2]


def test_shipped_editor_mutable_paths_are_project_local_and_configured():
    project=json.loads((ROOT/'config/projects/ai-poise/project.json').read_text())
    assert project['paths']['runtime']=='.runtime'
    assert project['paths']['tasks']=='task'
    assert project['paths']['sprints']=='sprint'
    assert project['paths']['database']=='database/tasks.sqlite'
    assert project['paths']['lock']=='database/tasks.lock'

    for relative in ('config/catalogue/editor.json','config/goal-editor.json'):
        path=ROOT/relative
        settings=json.loads(path.read_text())
        assert (path.parent/settings['root']).resolve()==ROOT
        assert settings['database'].startswith('projects/ai-poise/database/')
        assert settings['lock'].startswith('projects/ai-poise/database/')
        assert settings['responses'].startswith('projects/ai-poise/.runtime/')


def prepare(tmp_path):
    root=tmp_path/'home';root.mkdir()
    for name in ('process-templates','task-templates'):
        shutil.copytree(ROOT/'config/catalogue'/name,root/'config/catalogue'/name)
    for name in ('settings.json','editor.json','reference.json'):
        shutil.copy2(ROOT/'config/catalogue'/name,root/'config/catalogue'/name)
    repo=FileCatalogue(root/'config/catalogue/settings.json')
    tools=CatalogueCommands(repo,goal_config_tools(repo.editor.path),repo.raw['max_items'])
    items=[{'goal_type':g,'request_id':'install-'+g,'mode':'create','expected_revision':None,
            'template':repo.process_selection(g),'changes':[]} for g in repo.editor.raw['processes']]
    return root,repo,tools,items


def test_all_independent_packs_installed_in_one_batch_and_replay_preserved(tmp_path):
    root,repo,tools,items=prepare(tmp_path)
    first=tools.install(items);second=tools.install(items)
    assert first['count']==13 and [r['revision'] for r in second['results']]==[r['revision'] for r in first['results']]
    assert all(r['replayed'] for r in second['results'])
    for item in items:
        assert repo.configured_process(item['goal_type'])['goal_type']==item['goal_type']
    assert len(list((root/'config/catalogue/processes').glob('*.json')))==13


@pytest.mark.parametrize('invalid',[{'mode':'guess'},{'expected_revision':'not-null'}, {'request_id':'../outside'}])
def test_invalid_late_request_cannot_publish_earlier_files(tmp_path,invalid):
    root,repo,tools,items=prepare(tmp_path)
    batch=items[:2];batch[1].update(invalid)
    with pytest.raises(PoiseError):tools.install(batch)
    assert not list((root/'config/catalogue/processes').glob('*.json'))


def test_late_invalid_graph_is_checked_before_any_publication(tmp_path):
    root,repo,tools,items=prepare(tmp_path)
    # Removing a referenced stage leaves the final candidate graph invalid.
    second=items[1];entry=repo.process_template(second['template'])['route']['entry']
    second['changes']=[{'op':'remove_stage','id':entry}]
    with pytest.raises(PoiseError):tools.install(items[:2])
    assert not list((root/'config/catalogue/processes').glob('*.json'))


def test_bound_and_duplicate_goal_are_explicit_errors(tmp_path):
    root,repo,tools,items=prepare(tmp_path)
    small=CatalogueCommands(repo,tools.editor,1)
    with pytest.raises(PoiseError):small.install(items[:2])
    with pytest.raises(PoiseError):tools.install([items[0],items[0]])
    assert not list((root/'config/catalogue/processes').glob('*.json'))


def test_task_template_tampering_is_not_accepted_as_declared_revision(tmp_path):
    root,repo,tools,items=prepare(tmp_path)
    key='review-v1';entry=repo.raw['task_templates'][key]
    source=root/entry['path'];data=json.loads(source.read_text());data['task']['goal_type']='development'
    source.write_text(json.dumps(data))
    with pytest.raises(PoiseError):repo.task_blueprint({'id':key,'version':entry['version'],'digest':entry['digest']})


def test_cli_installs_all_packs_from_stdin(tmp_path):
    from poise.interfaces.catalogue import execute
    root,repo,tools,items=prepare(tmp_path)
    body=json.dumps({'action':'install','items':items,'project_config':None}).encode()
    out=io.StringIO();code=execute(repo.path,io.BytesIO(body),out)
    assert code==repo.editor.raw['exit_codes']['success']
    result=json.loads(out.getvalue());assert result['status']=='installed'
    complete=json.loads(Path(result['response_path']).read_text());assert complete['count']==13
    assert Path(result['response_path']).is_relative_to(root/'projects/ai-poise/.runtime')
    assert not (root/'state').exists()


def test_real_cli_accepts_a_batch_without_task_store(tmp_path):
    import subprocess,sys,os
    root,repo,tools,items=prepare(tmp_path)
    packet={'action':'install','items':items,'project_config':None}
    result=subprocess.run([sys.executable,'-m','poise','catalogue','--settings',str(repo.path)],
        input=json.dumps(packet),text=True,capture_output=True,env={**os.environ,'PYTHONPATH':str(ROOT/'src')},timeout=20)
    assert result.returncode==0,result.stderr+result.stdout
    assert json.loads(result.stdout)['status']=='installed'
    assert not (root/'state/state.sqlite').exists()
