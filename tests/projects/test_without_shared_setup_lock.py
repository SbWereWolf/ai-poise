"""Public project operations never acquire the installation-wide setup lock."""
import fcntl
from pathlib import Path

from conftest import WorkPoise
from poise.application.work import WorkTools
from poise.common import load_config
from poise.composition import project_tools
from poise.infrastructure.projects import ProjectSettings
from poise.infrastructure.project_config import FileProjectConfigUpdate
from .helpers import setup_case
from .material_relocation_helpers import prepare, relocate
from .test_update import installed_project, project_revision, update_request, update_tools


def forbid_setup_lock(monkeypatch, settings_path):
    lock = ProjectSettings(settings_path).lock
    original = fcntl.flock

    def check(fd, operation):
        if operation & fcntl.LOCK_EX and Path(f'/proc/self/fd/{fd}').readlink() == lock:
            raise AssertionError('Installation-wide setup lock must not be acquired')
        return original(fd, operation)

    monkeypatch.setattr(fcntl, 'flock', check)


def test_runtime_reads_and_records_without_shared_setup_lock(project, monkeypatch):
    _, config, _ = load_config(project['config_path'])
    forbid_setup_lock(monkeypatch, config['runtime_resources']['project_setup'])
    tools = WorkTools(WorkPoise(project['config_path'], 'no-shared-lock'))
    response = tools.invoke({'operation': 'show', 'input': {'queries': [{'id': 'state', 'kind': 'task'}]}, 'messages': []})
    assert response['results'][0]['value']['status'] == 'read_only'


def test_project_creation_without_shared_setup_lock(project, monkeypatch):
    settings, _, packet = setup_case(project)
    forbid_setup_lock(monkeypatch, settings)
    assert project_tools(settings).apply(packet)['replayed'] is False


def test_configuration_publication_without_shared_setup_lock(project, monkeypatch):
    settings, config_path, _ = installed_project(project)
    forbid_setup_lock(monkeypatch, settings)
    outcome = update_tools(settings).apply(update_request(config_path, project_revision(config_path)))
    assert outcome['status'] == 'updated'


def test_state_relocation_without_shared_setup_lock(project, tmp_path, monkeypatch):
    case = prepare(project, tmp_path)
    forbid_setup_lock(monkeypatch, case['settings'])
    moved = relocate(case)
    assert moved.runtime.state == case['destination']


def test_stage_criteria_publication_without_shared_setup_lock(project, monkeypatch):
    settings, _, _ = installed_project(project)
    forbid_setup_lock(monkeypatch, settings)
    with FileProjectConfigUpdate(ProjectSettings(settings)).stage_criteria_lock():
        pass
