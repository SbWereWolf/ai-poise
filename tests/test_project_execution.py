"""A real-source verification stops and resumes in the same assigned working area."""
import json
from copy import deepcopy
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from poise.application.work import WorkTools
from poise.composition import project_tools
from poise.common import load_config
from conftest import WorkPoise as Poise
from examples.project_execution import continue_execution
from tests.conftest import bind_task_requirements, git, seed_fixture_requirements, write_json


def snapshot_source(tmp_path):
    source = Path(__file__).resolve().parents[1]
    repo = tmp_path / 'actual-source'
    repo.mkdir()
    for folder in ('src', 'tests', 'config', 'examples', 'tools', '.agents'):
        shutil.copytree(source / folder, repo / folder,
                        ignore=shutil.ignore_patterns('__pycache__', '.pytest_cache'))
    for filename in ('pyproject.toml', '.gitignore', 'AGENTS.md'):
        shutil.copy2(source / filename, repo / filename)
    git(repo, 'init', '-b', 'main')
    git(repo, 'config', 'user.name', 'Pilot fixture')
    git(repo, 'config', 'user.email', 'pilot@example.invalid')
    git(repo, 'add', '.')
    git(repo, 'commit', '-m', 'Actual Poise sources, no toy implementation')
    return repo


def plan_real_source(project, repo, tmp_path):
    """Arrange a current supported verification Task through public owners.

    The historical pilot hard-codes a whole package invocation. This fixture
    selects one actual-source setup oracle and keeps the live execution example
    as the subject; it does not repair or mock that historical pilot.
    """
    workflow = deepcopy(project['workflow'])
    names = ['planning', 'execution', 'interpretation']
    stages = workflow['stages'][:3]
    for index, stage in enumerate(stages):
        stage.update(id=names[index], handler='produce', role='executor',
                     purpose=['design', 'execution', 'observation'][index],
                     read_only=True, allowed_paths=[], rework_targets=[names[index]],
                     transitions={'complete': names[index + 1] if index < 2 else None})
    workflow.update(goal_type='verification', route={'entry': 'planning'}, stages=stages,
                    benefit={'git_categories': [], 'sections': []})
    root = project['root']
    workflow_path = write_json(root / 'execution-workflow.json', workflow)
    settings_path = root / 'execution-setup.json'
    config = deepcopy(project['cfg'])
    config['git']['repository'] = str(repo)
    config['automatic_checks'] = []
    config['workflows'] = {'verification': 'workflows/verification.json'}
    config['paths'].update(state=str(tmp_path / 'pilot-data'),
                           worktrees=str(repo / 'pilot-worktrees'))
    config['runtime_resources'] = {'project_setup': str(settings_path), 'hooks': []}
    blueprint = write_json(root / 'execution-blueprint.json', {
        'schema': 'project-blueprint-1', 'config': config,
        'workflow_sources': {'verification': {'path': str(workflow_path)}},
        'questions': [{'id': 'project', 'path': ['project'], 'type': 'text',
                       'prompt': 'Идентификатор проверяемого проекта'}],
    })
    settings = json.loads((Path(__file__).parents[1] / 'config/project-setup.example.json').read_text())
    settings.update(root=str(root), templates={'execution': {'path': str(blueprint)}},
                    registry='execution-registry.json', lock='execution-setup.lock')
    write_json(settings_path, settings)
    write_json(root / 'execution-registry.json', {'schema': 'configured-project-registry-1', 'projects': {}})
    created = project_tools(settings_path).apply({
        'schema': 'project-setup-1', 'request_id': 'execution-project',
        'destination': 'execution-config', 'template': {'id': 'execution'},
        'edits': [{'path': ['project'], 'value': 'execution-fixture'}], 'probe_repository': True,
    })
    config_path = Path(created['config_path'])
    configured_root, configured, _ = load_config(config_path)
    registry = seed_fixture_requirements(configured_root, configured)
    method = json.loads((Path(__file__).parent / 'projects/fixtures/project_execution_method.json').read_text())
    method['argv'][0] = sys.executable
    task = deepcopy(project['task'])
    task.update(id='VERIFY-SOURCE', goal_type='verification',
                goal='Проверить настройку проекта на фактическом коде.',
                requirements=['Исполнение сохраняет проверенные исходники и рабочую область.'],
                definition_of_done=['Сохранена квитанция адресной проверки без неявной приёмки.'],
                methods=[method], method_inputs=[{'method_id': 'VERIFY',
                    'repository_inputs': ['tests/projects/test_reference.py'], 'future_outputs': [],
                    'reference_profile': {'runner': 'pytest', 'parser': 'positional-paths', 'version': 1}}],
                checks={name: ['VERIFY'] if name == 'execution' else [] for name in names},
                artifact_requirements=[],
                evidence_plan={name: {'subject_methods': {}, 'arguments': [], 'review_arguments': []} for name in names},
                stage_contracts=[{'stage_id': name, 'allowed_paths': [], 'entry_requirements': [],
                    'exit_requirements': [], 'criteria': {'requirements': [], 'definition_of_ready': [], 'definition_of_done': []}}
                    for name in names],
                decomposition={'kind': 'ordinary', 'integration': None,
                    'phases': [{'stage': name, 'skills': ['workflow'], 'areas': []} for name in names]})
    bind_task_requirements(task, registry)
    exclude = repo / '.git/info/exclude'
    exclude.write_text(exclude.read_text() + '\n/pilot-worktrees/\n')
    tools = WorkTools(Poise(config_path, 'VERIFY-SOURCE'))
    context = tools.invoke({'operation': 'bootstrap', 'input': {
        'task': task, 'decision': None, 'feedback': None, 'rework_stage': None}, 'messages': []})
    payload = deepcopy(context['result_template'])
    payload['sections']['report'] = 'Проверить один адресный oracle настройки проекта на фактическом коде.'
    planned = tools.invoke({'operation': 'verify', 'input': {'result': payload, 'artifacts': []}, 'messages': []})
    assert planned['status'] == 'verified' and planned['checks'] == []
    return {'setup': created}


def test_real_execution_is_not_accepted_and_resumes_in_the_same_working_area(project, tmp_path):
    repo = snapshot_source(tmp_path)
    base = git(repo, 'rev-parse', 'HEAD')
    first = plan_real_source(project, repo, tmp_path)
    # User decision is an explicit fixture. The test does not simulate a human reviewer.
    result = continue_execution(config_path=Path(first['setup']['config_path']),
                               session='VERIFY-SOURCE', task_id='VERIFY-SOURCE',
                               user_decision='continue', release_request_id='saved-execution')
    assert result['status'] == 'verified'
    assert result['stage'] == 'execution' and result['execution_accepted'] is False
    assert result['next_stage_started'] is False and result['target_changed'] is False
    assert result['repeat_replayed'] is True and result['target_revision'] == base
    observations = [o for o in result['report']['evidence']['observations'] if o['method'] == 'VERIFY']
    assert len(observations) == 1 and observations[0]['actual_exit_code'] == 0
    assert result['release']['status'] == 'ownership_released'
    saved = Poise(first['setup']['config_path'], 'observing-session').task_queries.record('VERIFY-SOURCE')
    original_worktree = saved['worktree']
    h = Poise(first['setup']['config_path'], 'next-agent')
    client = WorkTools(h)
    def call(op, args):
        return client.invoke({'operation': op, 'input': args, 'messages': []})
    context = call('bootstrap', {'task': {'id': 'VERIFY-SOURCE'}, 'decision': None,
                                'feedback': None, 'rework_stage': None})
    assert context['stage'] == 'execution' and context['status'] == 'verified'
    assert context['iteration'] == 1
    assert context['worktree'] == original_worktree
    assert Path(context['worktree']).is_relative_to(repo)
    replay = call('verify', {'result': None, 'artifacts': []})
    assert replay['replayed']
    check = next(c for c in replay['checks'] if c['method'] == 'VERIFY')
    details = call('show', {'queries': [{'id': 'output', 'kind': 'tool_result',
                                        'receipt_id': check['id'], 'representation': 'full', 'range': None}]})
    assert 'passed' in details['results'][0]['value']['text']
    assert git(repo, 'rev-parse', 'main') == base


def test_execution_requires_explicit_continue_before_opening_store(tmp_path):
    with pytest.raises(ValueError, match='continue'):
        continue_execution(config_path=tmp_path/'missing.json', session='S', task_id='T',
                           user_decision='accepted-but-stop', release_request_id='release')
