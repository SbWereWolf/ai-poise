from copy import deepcopy
import io
import json
from pathlib import Path
import subprocess
import sys

import pytest

from poise.common import PoiseError
from poise.composition import project_tools
from poise.interfaces.projects import interactive
from tests.conftest import write_json
from .helpers import setup_case


def file_state(path):
    path = Path(path)
    if not path.exists():
        return None
    stat = path.stat()
    return {
        'content': path.read_bytes(),
        'mode': stat.st_mode,
        'inode': stat.st_ino,
        'size': stat.st_size,
        'mtime_ns': stat.st_mtime_ns,
        'ctime_ns': stat.st_ctime_ns,
    }


def test_project_list_cli_returns_empty_registry(project):
    settings, _, _ = setup_case(project)
    registry = project['root'] / 'state/project-registry.json'
    before = {path: file_state(path) for path in (settings, registry)}

    result = subprocess.run(
        [sys.executable, '-m', 'poise', 'project', 'list', '--settings', str(settings)],
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0, result.stderr + result.stdout
    assert json.loads(result.stdout) == {
        'status': 'listed',
        'projects': [],
        'errors': [],
    }
    assert {path: file_state(path) for path in before} == before


def test_list_orders_several_configured_projects(project):
    settings, _, request = setup_case(project)
    commands = project_tools(settings)
    second = deepcopy(request)
    second['request_id'] = 'setup-2'
    second['destination'] = 'configured/alpha'
    second['edits'][0]['value'] = 'alpha'

    commands.apply(request)
    commands.apply(second)

    result = commands.list()
    assert result['status'] == 'listed'
    assert result['errors'] == []
    assert result['projects'] == [
        {
            'project': 'alpha',
            'config_path': str((project['root'] / 'configured/alpha/project.json').resolve()),
        },
        {
            'project': 'pilot',
            'config_path': str((project['root'] / 'configured/pilot/project.json').resolve()),
        },
    ]


def test_batch_creation_registers_stable_identity_and_rejects_rebinding(project):
    settings, _, request = setup_case(project)
    commands = project_tools(settings)

    created = commands.apply(request)
    conflicting = deepcopy(request)
    conflicting['request_id'] = 'setup-other-path'
    conflicting['destination'] = 'configured/other-pilot'

    assert commands.list()['projects'] == [{
        'project': 'pilot',
        'config_path': created['config_path'],
    }]
    with pytest.raises(PoiseError, match='already registered'):
        commands.apply(conflicting)
    assert not (project['root'] / 'configured/other-pilot').exists()


def test_interactive_creation_uses_same_registry(project):
    settings, _, request = setup_case(project)
    request['edits'] = []
    output = io.StringIO()
    prompts = io.StringIO()

    code = interactive(
        settings,
        request,
        io.StringIO('set "interactive"\nkeep\nkeep\nkeep\npublish\n'),
        output,
        prompts,
    )

    assert code == 0, prompts.getvalue() + output.getvalue()
    assert project_tools(settings).list()['projects'] == [{
        'project': 'interactive',
        'config_path': str((project['root'] / 'configured/pilot/project.json').resolve()),
    }]


def test_list_separates_missing_invalid_and_mismatched_entries(project):
    settings, _, request = setup_case(project)
    commands = project_tools(settings)
    created = commands.apply(request)
    invalid = project['root'] / 'configured/invalid/project.json'
    write_json(invalid, {})
    write_json(project['root'] / 'state/project-registry.json', {
        'schema': 'configured-project-registry-1',
        'projects': {
            'pilot': {'config_path': str(Path(created['config_path']).relative_to(project['root']))},
            'other': {'config_path': str(Path(created['config_path']).relative_to(project['root']))},
            'missing': {'config_path': 'configured/missing/project.json'},
            'invalid': {'config_path': 'configured/invalid/project.json'},
        },
    })
    registry = project['root'] / 'state/project-registry.json'
    missing = project['root'] / 'configured/missing/project.json'
    observed_paths = (settings, registry, Path(created['config_path']), invalid, missing)
    before = {path: file_state(path) for path in observed_paths}

    result = commands.list()

    assert result['status'] == 'listed_with_errors'
    assert result['projects'] == [{
        'project': 'pilot',
        'config_path': created['config_path'],
    }]
    assert [error['project'] for error in result['errors']] == ['invalid', 'missing', 'other']
    assert [error['status'] for error in result['errors']] == ['invalid', 'missing', 'invalid']
    assert all(Path(error['config_path']).is_absolute() for error in result['errors'])
    assert all(error['reason'] for error in result['errors'])
    assert {path: file_state(path) for path in before} == before
