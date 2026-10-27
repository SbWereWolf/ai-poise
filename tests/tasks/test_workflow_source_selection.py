"""Public source selection with literal, independently owned test templates."""
from copy import deepcopy
from contextlib import closing
import json
from pathlib import Path
import sqlite3

import pytest

from batch.helpers import configure, request
from conftest import WorkPoise, write_json
from poise.application.work import WorkTools
from poise.modules.foundation.errors import PoiseError
from projects.test_update import (
    project_revision, update_request, update_tools, workflow_change,
)
from tasks.test_planning_flexibility import POLICY


FIXTURES = Path(__file__).resolve().parents[2] / 'fixtures/workflow-source-retirement'


@pytest.fixture
def sources(project):
    forms = json.loads((FIXTURES / 'source-forms.json').read_text())
    setup = json.loads((FIXTURES / 'catalogue-setup.json').read_text())
    root = project['config_path'].parent
    catalogue_root = root / 'source-fixture'
    selected = write_json(root / 'selected/development.json', forms['project_a'])
    write_json(root / 'config/workflows/development.json', forms['decoy'])
    write_json(catalogue_root / 'catalogue-development.json', forms['catalogue_development'])
    write_json(catalogue_root / 'catalogue-only.json', forms['catalogue_only'])
    write_json(catalogue_root / 'development-task.json', setup['blueprints']['development'])
    write_json(catalogue_root / 'catalogue-only-task.json', setup['blueprints']['catalogue_only'])
    write_json(catalogue_root / 'editor.json', setup['editor'])
    catalogue = write_json(catalogue_root / 'catalogue.json', setup['catalogue'])
    project['cfg']['workflows'] = {'development': 'selected/development.json'}
    project['cfg']['automatic_checks'] = []
    project['cfg']['task_planning'] = {
        'catalogue': str(catalogue), 'restart_revision_policy': deepcopy(POLICY),
    }
    configure(project)
    return project, forms, selected, catalogue_root


def api(project, actor='source-owner'):
    return WorkTools(WorkPoise(project['config_path'], actor))


def create_packet(task_id='SOURCE-1', goal_type='development'):
    packet = json.loads((FIXTURES / 'requests.json').read_text())['create']
    packet['input'].update(
        request_id='create-' + task_id, task_id=task_id, goal_type=goal_type,
    )
    return packet


def bootstrap(client, task):
    return client.invoke(request('bootstrap', {
        'task': task, 'decision': None, 'feedback': None, 'rework_stage': None,
    }))


def saved_task(project, task_id):
    """Observe every persisted Task value needed to prove unchanged state."""
    root = project['config_path'].parent
    state = root / project['cfg']['paths']['state']
    database = state / project['cfg']['paths']['database']
    with closing(sqlite3.connect(database)) as db:
        return db.execute(
            'SELECT id,status,claimed_by,metadata,version,stage_index,iteration,'
            'current_submission_id FROM tasks WHERE id=?', (task_id,),
        ).fetchone()


def test_newborn_uses_explicit_project_source_above_draft(sources, tmp_path, monkeypatch):
    project, forms, _, _ = sources
    monkeypatch.chdir(tmp_path)
    out = api(project).invoke(create_packet())
    assert out['status'] == 'newborn'
    assert out['workflow'] == forms['project_a']
    assert out['workflow']['stages'][0]['instruction'] == 'Selected project A'
    assert out['draft']['goal_type'] == 'development'
    assert 'workflow' not in out['draft']


def test_catalogue_only_goal_uses_a_test_owned_registered_template(sources):
    project, forms, _, _ = sources
    client = api(project)
    out = client.invoke(create_packet(goal_type='catalogue_only'))
    assert out['status'] == 'newborn'
    assert out['workflow'] == forms['catalogue_only']
    assert out['workflow']['stages'][0]['instruction'] == 'Selected catalogue-only starter'
    assert out['draft']['planning']['template'] == {'id': 'test-catalogue-only'}
    assert 'catalogue_only' not in client.runtime.workflows


def test_saved_newborn_replay_and_reselection_survive_removed_starter(sources):
    project, forms, _, catalogue = sources
    client = api(project)
    packet = create_packet()
    first = client.invoke(packet)
    before = saved_task(project, 'SOURCE-1')
    conflicting = deepcopy(packet)
    conflicting['input']['patch']['goal'] = 'A conflicting source-selection intent.'
    with pytest.raises(PoiseError):
        client.invoke(conflicting)
    assert saved_task(project, 'SOURCE-1') == before
    (catalogue / 'development-task.json').unlink()
    replay = client.invoke(packet)
    assert replay['replayed'] is True
    assert replay['workflow'] == forms['project_a']
    assert saved_task(project, 'SOURCE-1') == before
    reselect = json.loads((FIXTURES / 'requests.json').read_text())['reselect']
    reselect['input']['expected_revision'] = first['revision']
    selected = client.invoke(reselect)
    assert selected['workflow'] == forms['project_a']
    assert selected['draft']['goal'] == 'Exercise explicit workflow source selection.'
    assert 'workflow' not in selected['draft']


def test_saved_active_uses_a_while_new_work_uses_b(sources):
    project, forms, _, _ = sources
    first = bootstrap(api(project), deepcopy(project['task']))
    assert first['stage'] == 'tests'
    assert first['instruction'] == 'Selected project A'
    assert first['workflow']['role'] == 'executor'
    settings = Path(project['cfg']['runtime_resources']['project_setup'])
    update_tools(settings).apply(update_request(
        str(project['config_path']), project_revision(project['config_path']),
        workflow_updates=[workflow_change(forms['project_a'], 'Selected project B')],
    ))
    # Reconstruct the runtime so a stale in-memory project graph cannot mask failure.
    resumed = bootstrap(api(project), {'id': 'T1'})
    assert resumed['stage'] == 'tests'
    assert resumed['instruction'] == 'Selected project A'
    assert resumed['workflow']['role'] == 'executor'
    assert 'stages' not in resumed['workflow']
    metadata = json.loads(saved_task(project, 'T1')[3])
    assert metadata['workflow'] == forms['project_a']
    other = deepcopy(project['task'])
    other['id'] = 'SOURCE-2'
    fresh = bootstrap(api(project, 'new-source-owner'), other)
    assert fresh['stage'] == 'tests'
    assert fresh['instruction'] == 'Selected project B'
    assert json.loads(saved_task(project, 'SOURCE-2')[3])['workflow'] == forms['project_b']


@pytest.mark.parametrize('damage', ['missing', 'malformed', 'wrong-goal'])
def test_invalid_selected_source_preserves_saved_task_instead_of_using_decoy(sources, damage):
    project, forms, selected, _ = sources
    api(project).invoke(create_packet())
    before = saved_task(project, 'SOURCE-1')
    if damage == 'missing':
        selected.unlink()
    elif damage == 'malformed':
        write_json(selected, {})
    else:
        write_json(selected, forms['catalogue_only'])
    with pytest.raises(PoiseError) as failure:
        api(project)
    reasons = json.loads((FIXTURES / 'failure-reasons.json').read_text())
    assert reasons[damage] in str(failure.value)
    if damage == 'missing':
        assert str(selected) in str(failure.value)
    assert saved_task(project, 'SOURCE-1') == before
    assert json.loads((selected.parent.parent / 'config/workflows/development.json').read_text()) == forms['decoy']


def test_starter_identity_mismatch_preserves_existing_task(sources):
    project, forms, _, catalogue = sources
    client = api(project)
    client.invoke(create_packet())
    before = saved_task(project, 'SOURCE-1')
    path = catalogue / 'development-task.json'
    blueprint = json.loads(path.read_text())
    blueprint['goal_type'] = 'catalogue_only'
    blueprint['task']['goal_type'] = 'catalogue_only'
    write_json(path, blueprint)
    with pytest.raises(PoiseError, match='identity mismatch'):
        client.invoke(create_packet('SOURCE-2'))
    assert saved_task(project, 'SOURCE-1') == before
    assert saved_task(project, 'SOURCE-2') is None
