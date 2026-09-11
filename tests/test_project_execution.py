"""A real-source verification can stop and survive disappearance of its first store."""
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from poise.application.work import WorkTools
from poise.composition import project_tools
from poise.runtime import Poise
from examples.project_execution import continue_execution
from examples.project_pilot import run as plan_real_source
from tests.conftest import git


def snapshot_source(tmp_path):
    source = Path(__file__).resolve().parents[1]
    repo = tmp_path / 'actual-source'
    repo.mkdir()
    for folder in ('src', 'tests', 'config', 'examples', 'tools', 'skills'):
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


def setup_destination(repo, destination):
    setup = json.loads((repo / 'config/project-setup.json').read_text())
    selected = setup['templates']['linux-reference']
    template = json.loads((repo / selected['path']).read_text())
    values = {'project': 'poise-pilot', 'repository': str(repo), 'base': 'main',
              'remote': 'not-configured', 'author_name': 'Poise pilot',
              'author_email': 'pilot@example.invalid', 'push': False,
              'state': 'state', 'environment': ['PATH', 'HOME']}
    return project_tools(repo / 'config/project-setup.json').apply({
        'schema': 'project-setup-1', 'request_id': 'receiving-project',
        'destination': destination,
        'template': {'id': 'linux-reference', 'version': selected['version'], 'digest': selected['digest']},
        'edits': [{'path': q['path'], 'value': values[q['id']]} for q in template['questions']],
        'probe_repository': True})


def test_real_execution_is_not_accepted_and_can_be_resumed_without_source_store(tmp_path):
    repo = snapshot_source(tmp_path)
    base = git(repo, 'rev-parse', 'HEAD')
    first = plan_real_source(repo, repo, 'main', 'state/planned', 'VERIFY-SOURCE', 60)
    # User decision is an explicit fixture. The test does not simulate a human reviewer.
    result = continue_execution(config_path=Path(first['setup']['config_path']),
                               session='VERIFY-SOURCE', task_id='VERIFY-SOURCE',
                               user_decision='continue', export_request_id='saved-execution')
    assert result['status'] == 'verified'
    assert result['stage'] == 'execution' and result['execution_accepted'] is False
    assert result['next_stage_started'] is False and result['target_changed'] is False
    assert result['repeat_replayed'] is True and result['target_revision'] == base
    observations = [o for o in result['report']['evidence']['observations'] if o['method'] == 'VERIFY']
    assert len(observations) == 1 and observations[0]['actual_exit_code'] == 0
    assert result['transfer']['status'] == 'exported'
    incoming = tmp_path / 'execution.zip'
    shutil.copy2(result['transfer']['package_path'], incoming)
    receiving_repo = tmp_path / 'receiving-source'
    subprocess.run(['git', 'clone', str(repo), str(receiving_repo)], check=True, capture_output=True)
    setup = setup_destination(receiving_repo, 'state/received')
    # Remove access to the original operational store before importing/reading evidence.
    (repo / 'state/planned').rename(repo / 'state/offline')
    h = Poise(setup['config_path'], 'next-agent')
    client = WorkTools(h)
    def call(op, args):
        return client.invoke({'operation': op, 'input': args, 'messages': []})
    imported = call('transfer', {'action': 'import', 'request_id': 'restore-execution',
                                'package_path': str(incoming), 'package_digest': result['transfer']['package_digest']})
    assert imported['status'] == 'imported'
    context = call('bootstrap', {'task': {'id': 'VERIFY-SOURCE'}, 'decision': None,
                                'feedback': None, 'rework_stage': None})
    assert context['stage'] == 'execution' and context['status'] == 'verified'
    assert context['iteration'] == 1
    assert Path(context['worktree']).is_relative_to(receiving_repo)
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
                           user_decision='accepted-but-stop', export_request_id='export')
