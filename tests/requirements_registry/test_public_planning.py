"""Public plans expose agreement content, not inferred or hidden lineage."""
from copy import deepcopy
from pathlib import Path
import io
import json
import pytest
from conftest import WorkPoise, write_json
from poise.interfaces.requirements_registry import execute


def test_public_batch_plans_full_text_and_current_coverage(project):
    packet = {'operation': 'query', 'input': {'queries': [
        {'id': 'plan', 'kind': 'plan_task', 'task_requirements': [
            {'text': 'A published result remains traceable.', 'applications': ['FIXTURE-APPLICATION']}
        ]},
        {'id': 'current', 'kind': 'coverage', 'mode': 'current'},
        {'id': 'missing', 'kind': 'plan_task', 'task_requirements': [
            {'text': 'Propose an explicit owner.', 'applications': []}
        ]},
    ]}}
    output = io.StringIO()
    assert execute(project['config_path'], io.BytesIO(json.dumps(packet).encode()), output) == 0
    data = json.loads(output.getvalue())
    assert data['status'] == 'read_only'
    plan = data['results'][0]['value']
    assert plan['status'] == 'ready'
    assert plan['chains'][0]['systems'][0]['text'] == 'The fixture preserves an explicit Requirements chain.'
    assert plan['chains'][0]['applications'][0]['text'] == 'Fixture Tasks declare their agreed Requirements context.'
    assert data['results'][1]['value'] == {'application_without_system': [], 'system_without_application': []}
    assert data['results'][2]['value']['status'] == 'gaps'
    assert data['revision'] == 1


def test_real_task_publication_preserves_context_and_rejects_missing_agreement(project):
    runtime = WorkPoise(project['config_path'], 'requirements-owner')
    body = deepcopy(project['task'])
    del body['requirements_agreement']
    with pytest.raises(Exception, match='agreement'):
        runtime.bootstrap(body)
    with runtime.store.unit_of_work() as uow:
        assert not uow.tasks.exists(body['id'])
    runtime.bootstrap(deepcopy(project['task']))
    snapshot = runtime.task_commands.requirements_snapshot(body['id'])
    assert snapshot == project['task']['requirements_snapshot']
    runtime.requirements_commands.apply({
        'request_id': 'update-live', 'expected_revision': 1, 'operations': [
            {'kind': 'put_requirement', 'requirement': {
                'id': 'FIXTURE-SYSTEM', 'level': 'system', 'status': 'obsolete', 'text': 'Changed live only.'
            }}
        ],
    })
    assert runtime.task_commands.requirements_snapshot(body['id']) == snapshot


def test_requirements_storage_cannot_alias_telemetry(project):
    cfg = deepcopy(project['cfg'])
    cfg['paths']['requirements_database'] = cfg['accounting']['storage']['database']
    write_json(project['config_path'], cfg)
    from poise.common import load_config
    with pytest.raises(Exception, match='storage|хранилищ'):
        load_config(project['config_path'])


def test_agreement_validation_reads_one_consistent_registry(project):
    from poise.modules.requirements_registry.service import TaskRequirementsGate
    calls = []
    def observe():
        calls.append(True)
        assert len(calls) == 1, 'one agreement must not combine two registry observations'
        return project['requirements_registry']
    gate = TaskRequirementsGate.enabled(observe)
    _, context = gate.prepare_contract(deepcopy(project['task']))
    assert context['snapshot'] == project['task']['requirements_snapshot']
    assert calls == [True]


def test_requirements_database_stays_outside_served_code(project):
    cfg = deepcopy(project['cfg'])
    cfg['paths']['state'] = str(project['app'] / 'mutable-project-data')
    write_json(project['config_path'], cfg)
    from poise.common import load_config
    with pytest.raises(Exception, match='Requirements.*codebase'):
        load_config(project['config_path'])


def test_requirements_database_and_lock_cannot_be_hardlink_aliases(project):
    from poise.common import load_config
    from pathlib import Path
    import os
    cfg = deepcopy(project['cfg'])
    root = project['root'] / cfg['paths']['state']
    db = root / cfg['paths']['requirements_database']
    alias = root / 'requirements-alias.lock'
    os.link(db, alias)
    cfg['paths']['requirements_lock'] = alias.name
    write_json(project['config_path'], cfg)
    with pytest.raises(Exception, match='Requirements storage'):
        load_config(project['config_path'])


def test_sprint_publication_persists_each_agreed_snapshot_atomically(project):
    from poise.application.work import WorkTools
    from sprints.helpers import setup, task, draft, publish
    setup(project)
    a, b = task(project, 'A'), task(project, 'B')
    expected = {'A': deepcopy(a['requirements_snapshot']), 'B': deepcopy(b['requirements_snapshot'])}
    runtime = WorkPoise(project['config_path'], 'sprint-planner')
    tools = WorkTools(runtime)
    planned = draft(tools, [a, b])
    publish(tools, planned['revision'])
    for task_id, snapshot in expected.items():
        assert runtime.task_commands.requirements_snapshot(task_id) == snapshot


def test_sprint_missing_agreement_rejects_entire_publication(project):
    from poise.application.work import WorkTools
    from sprints.helpers import setup, task, draft, publish
    setup(project)
    a, b = task(project, 'A'), task(project, 'B')
    del b['requirements_agreement']
    runtime = WorkPoise(project['config_path'], 'sprint-planner')
    tools = WorkTools(runtime)
    planned = draft(tools, [a, b])
    with runtime.store.unit_of_work() as unit:
        before = {name: unit.tasks.load_newborn(name) for name in ('A', 'B')}
    with pytest.raises(Exception, match='agreement'):
        publish(tools, planned['revision'])
    with runtime.store.unit_of_work() as unit:
        assert {name: unit.tasks.load_newborn(name) for name in ('A', 'B')} == before
    assert runtime.task_queries.summary() == []


def test_all_shipped_project_templates_publish_requirements_paths():
    from poise.common import digest
    root = Path(__file__).resolve().parents[2]
    settings = json.loads((root / 'config/project-setup.json').read_text())
    for path in sorted((root / 'config/project-templates').glob('*.json')):
        entry = settings['templates'][path.stem]
        assert root / entry['path'] == path
        blueprint = json.loads(path.read_text())
        assert blueprint['config']['paths']['requirements_database']
        assert blueprint['config']['paths']['requirements_lock']
        assert entry['version'] == blueprint['version']
        assert entry['digest'] == digest(blueprint)


def test_shipped_poise_configuration_keeps_requirements_outside_served_code():
    from poise.common import load_config
    root = Path(__file__).resolve().parents[2]
    _, config, _ = load_config(root / 'config/projects/ai-poise/project.json')
    for key in ('requirements_database', 'requirements_lock'):
        path = Path(config['paths'][key])
        assert path.is_absolute()
        assert not path.is_relative_to(config['git']['repository'])


def test_explicit_absolute_requirements_storage_is_not_rebased(project, tmp_path):
    from poise.composition import requirements_tools
    cfg = deepcopy(project['cfg'])
    external = tmp_path / 'separate-requirements'
    cfg['paths']['requirements_database'] = str(external / 'registry.sqlite')
    cfg['paths']['requirements_lock'] = str(external / 'registry.lock')
    write_json(project['config_path'], cfg)
    commands, _ = requirements_tools(project['config_path'])
    assert commands.store.database == external / 'registry.sqlite'
    assert commands.store.lock == external / 'registry.lock'
    assert commands.query([{'id': 'empty', 'kind': 'registry'}])['revision'] == 0
