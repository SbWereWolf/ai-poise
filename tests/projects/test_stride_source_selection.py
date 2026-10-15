"""Bash launcher selects one explicit Poise source and sends a project ID."""
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize('project_id', ['alpha', 'beta'])
def test_launcher_uses_selected_source_and_passes_project_id(tmp_path, project_id):
    selected = tmp_path / 'shared Poise/app'
    package = selected / 'src/poise'
    package.mkdir(parents=True)
    (package / '__init__.py').write_text('')
    shutil.copyfile(Path(__file__).parent / 'fixtures/stride/source_selection_probe.py', package / 'stride.py')
    settings = tmp_path / 'launcher.json'
    settings.write_text('{}')
    bootstrap = tmp_path / 'launcher.env'
    bootstrap.write_text('STRIDE_PYTHON=' + shlex.quote(sys.executable) + '\nSTRIDE_CONFIG=' + shlex.quote(str(settings)) + '\n')
    unrelated = tmp_path / 'unrelated'
    unrelated.mkdir()
    result = subprocess.run(
        ['bash', str(ROOT / 'stride.sh'), '--poise-root', str(selected), '--project', project_id, str(bootstrap), 'work'],
        cwd=unrelated, env=dict(os.environ), text=True, capture_output=True,
    )

    assert result.returncode == 0, result.stderr
    observed = json.loads(result.stdout)
    assert observed == {'source': str(package / 'stride.py'),
                        'arguments': ['--config', str(settings), '--project', project_id, '--', 'work']}


def test_poise_receives_explicit_project_instead_of_a_project_config_path(monkeypatch):
    from poise import stride
    from poise.modules.projects.launch import ProjectLaunchSettings
    selected = ProjectLaunchSettings('/tools', '/data', 'old-project', 'config/setup.json',
                                     ('python', '-m', 'poise'), {}, 2)
    observed = []

    class SettingsReader:
        def read_settings(self, path):
            assert path == '/launcher/settings.json'
            return selected

    class Launch:
        def __init__(self, port):
            pass

        def execute(self, settings, arguments):
            observed.append((settings.project_id, arguments))

    monkeypatch.setattr(stride, 'FileProjectLaunch', SettingsReader)
    monkeypatch.setattr(stride, 'ProjectLaunchCommands', Launch)
    monkeypatch.setattr(sys, 'argv', ['poise.stride', '--config', '/launcher/settings.json',
                                    '--project', 'alpha', '--', 'work'])

    assert stride.main() == 0
    assert observed == [('alpha', ['work'])]


@pytest.mark.parametrize('operation,expected', [
    (['work'], ['/configured/python', '-P', '-s', '-B', '-m', 'poise', 'work']),
    (['next'], ['/configured/other-python', '-P', '-s', '-B', '-m', 'poise', 'next', '--settings', '/registry/setup.json']),
])
def test_poise_child_keeps_configured_python_and_selected_source(monkeypatch, operation, expected):
    from poise.infrastructure.project_launch import FileProjectLaunch
    from poise.modules.projects.launch import ProjectLaunchSettings
    selected = ProjectLaunchSettings('/registry', '/data', 'alpha', 'setup.json',
        ('/configured/python', '-P', '-s', '-B', '-m', 'poise'),
        {'next': ('/configured/other-python', '-P', '-s', '-B', '-m', 'poise', 'next', '--settings', '/registry/setup.json')}, 2)
    monkeypatch.setenv('PYTHONPATH', '/selected/app/src')
    parent = dict(os.environ)
    observed = []
    monkeypatch.setattr(os, 'execvpe', lambda executable, argv, environment: observed.append((executable, argv, environment)))

    FileProjectLaunch().execute(selected.plan_command(operation), '/registry/alpha/project.json')

    assert len(observed) == 1
    executable, argv, environment = observed[0]
    assert executable == expected[0]
    assert argv == expected
    assert environment == {**parent, 'POISE_CONFIG': '/registry/alpha/project.json'}
    assert environment['PYTHONPATH'] == '/selected/app/src'
    assert dict(os.environ) == parent
