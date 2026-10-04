"""Integration subjects: public project/Requirements adapters and real local Git.

Expected paths, file bytes and Git fixture facts are test owned.
"""
import io
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from poise.interfaces.projects import execute as setup_execute
from poise.interfaces.project_config import execute as update_execute
from poise.interfaces.requirements_registry import execute as requirements_execute
from tests.conftest import write_json
from tests.projects.helpers import setup_case
from tests.projects.test_update import update_request


def invoke(adapter, path, packet):
    output = io.StringIO()
    code = adapter(path, io.BytesIO(json.dumps(packet).encode()), output)
    return code, json.loads(output.getvalue())


def git(repo, *args):
    return subprocess.run(['git', '-C', str(repo), *args], capture_output=True, text=True)


def case(project):
    settings, _, packet = setup_case(project)
    # Keep the template/source material explicit while placing the destination
    # inside the served repository. These files are test arrangement, not oracle.
    shutil.copytree(project['root'] / 'config', project['app'] / 'config')
    raw = json.loads(settings.read_text())
    raw['root'] = str(project['app'])
    settings = write_json(project['root'] / 'storage-setup.json', raw)
    write_json(project['app'] / 'state/project-registry.json', {
        'schema': 'configured-project-registry-1', 'projects': {},
    })
    packet['destination'] = 'projects/demo'
    packet['probe_repository'] = False
    return settings, packet, project['app'] / 'projects/demo/project.json'


def edit(packet, key, value):
    packet['edits'].append({'path': ['paths', key], 'value': str(value)})


def ignore(repo, rule):
    (repo / '.gitignore').write_text(rule)


def assert_git_fact(repo, file, *, ignored, tracked=False):
    relative = str(file.relative_to(repo))
    assert git(repo, 'check-ignore', '--no-index', '-q', '--', relative).returncode == (0 if ignored else 1)
    observed = git(repo, 'ls-files', '--error-unmatch', '--', relative)
    assert (observed.returncode == 0) is tracked


def pair_bytes(db, lock):
    return {str(p): p.read_bytes() for folder in {db.parent, lock.parent}
            if folder.exists() for p in folder.glob('*') if p.is_file()}


def query(config):
    return invoke(requirements_execute, config, {
        'operation': 'query', 'input': {'queries': [{'id': 'registry', 'kind': 'registry'}]},
    })


def assert_empty_registry(config, db, lock):
    code, reply = query(config)
    assert code == 0, reply
    assert reply['status'] == 'read_only'
    assert reply['revision'] == 0
    assert db.is_file() and lock.is_file()


@pytest.mark.parametrize('existing', [False, True])
def test_external_storage_preserves_absolute_paths_without_git(project, tmp_path, monkeypatch, existing):
    settings, packet, config = case(project)
    db, lock = tmp_path / 'external/registry.sqlite', tmp_path / 'external/registry.lock'
    if existing:
        db.parent.mkdir()
        db.touch()
        lock.touch()
    edit(packet, 'requirements_database', db)
    edit(packet, 'requirements_lock', lock)
    monkeypatch.setenv('PATH', str(tmp_path / 'missing-executables'))
    code, reply = invoke(setup_execute, settings, packet)
    assert code == 0, reply
    assert reply['status'] == 'created'
    cfg = json.loads(config.read_text())
    assert cfg['paths']['requirements_database'] == str(db)
    assert cfg['paths']['requirements_lock'] == str(lock)
    assert_empty_registry(config, db, lock)
    assert not (config.parent / 'state/registry.sqlite').exists()


def test_setup_relative_final_only_ignore_accepts_absent_pair(project):
    settings, packet, config = case(project)
    ignore(project['app'], '/projects/demo/state/\n')
    db, lock = config.parent / 'state/requirements.sqlite', config.parent / 'state/requirements.lock'
    assert not config.parent.exists()
    assert_git_fact(project['app'], db, ignored=True)
    assert_git_fact(project['app'], lock, ignored=True)
    assert_git_fact(project['app'], project['app'] / 'projects/.project-setup-test/candidate/state/requirements.sqlite', ignored=False)
    code, reply = invoke(setup_execute, settings, packet)
    assert code == 0, reply
    assert reply['status'] == 'created'
    assert json.loads(config.read_text())['paths']['state'] == 'state'
    assert (config.parent / 'config/processes/development.json').is_file()
    assert_empty_registry(config, db, lock)
    assert not list(config.parent.parent.glob('.project-setup-*'))


@pytest.mark.parametrize('coordinate', ['absolute-state', 'absolute-files'])
def test_setup_existing_internal_pair_outside_absent_destination(project, coordinate):
    settings, packet, config = case(project)
    shared = project['app'] / 'shared-data'
    db, lock = shared / 'requirements.sqlite', shared / 'requirements.lock'
    shared.mkdir()
    db.write_bytes(b'existing-database')
    lock.write_bytes(b'existing-lock')
    ignore(project['app'], '/shared-data/\n')
    if coordinate == 'absolute-state':
        edit(packet, 'state', shared)
    else:
        edit(packet, 'requirements_database', db)
        edit(packet, 'requirements_lock', lock)
    assert not config.parent.exists()
    assert_git_fact(project['app'], db, ignored=True)
    assert_git_fact(project['app'], lock, ignored=True)
    before = pair_bytes(db, lock)
    code, reply = invoke(setup_execute, settings, packet)
    assert code == 0, reply
    assert reply['status'] == 'created'
    assert pair_bytes(db, lock) == before  # Setup validates; it does not open a DB.


@pytest.mark.parametrize('members', ['absent', 'existing', 'mixed'])
def test_update_absolute_internal_pair_preserves_staged_process(project, tmp_path, members):
    settings, packet, config = case(project)
    # Initial public setup serves a separate explicit repository path; storage
    # is external to that path. The real public update changes the served
    # repository to app and must validate the now-internal pair and candidate.
    packet['edits'][1]['value'] = str(project['root'])
    db, lock = project['app'] / 'shared-data/requirements.sqlite', project['app'] / 'shared-data/requirements.lock'
    ignore(project['app'], '/shared-data/\n')
    if members != 'absent':
        db.parent.mkdir()
        db.write_bytes(b'existing-database')
        if members == 'existing':
            lock.write_bytes(b'existing-lock')
    edit(packet, 'requirements_database', db)
    edit(packet, 'requirements_lock', lock)
    code, created = invoke(setup_execute, settings, packet)
    assert code == 0, created
    process_path = config.parent / 'config/processes/development.json'
    prior = json.loads(process_path.read_text())
    import hashlib
    revision = hashlib.sha256(json.dumps(prior, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()
    request = update_request(config, created['revision'], manifest_edits=[
        {'path': ['git', 'repository'], 'value': str(project['app'])},
    ], process_updates=[{'goal_type': 'development', 'expected_revision': revision,
                         'changes': [{'op': 'patch_stage', 'id': 'tests', 'set': {'instruction': 'Candidate process text survives validation.'}}]}])
    before = pair_bytes(db, lock)
    code, reply = invoke(update_execute, settings, request)
    assert code == 0, reply
    assert reply['status'] == 'updated'
    assert json.loads(process_path.read_text())['stages'][0]['instruction'] == 'Candidate process text survives validation.'
    assert pair_bytes(db, lock) == before
    if members == 'absent':
        assert_empty_registry(config, db, lock)


@pytest.mark.parametrize('members', ['absent', 'existing', 'mixed'])
def test_update_and_live_relative_existing_or_missing_pair(project, members):
    settings, packet, config = case(project)
    ignore(project['app'], '/projects/demo/state/\n')
    db, lock = config.parent / 'state/requirements.sqlite', config.parent / 'state/requirements.lock'
    assert_git_fact(project['app'], db, ignored=True)
    assert_git_fact(project['app'], lock, ignored=True)
    assert_git_fact(project['app'], project['app'] / 'projects/.project-update-test/state/requirements.sqlite', ignored=False)
    code, created = invoke(setup_execute, settings, packet)
    assert code == 0, created
    if members != 'absent':
        db.parent.mkdir()
        db.touch()
        if members == 'existing':
            lock.touch()
    request = update_request(config, created['revision'], manifest_edits=[
        {'path': ['limits', 'preview_chars'], 'value': 301},
    ])
    code, reply = invoke(update_execute, settings, request)
    assert code == 0, reply
    assert reply['status'] == 'updated'
    assert json.loads(config.read_text())['paths']['requirements_database'] == 'requirements.sqlite'
    assert_empty_registry(config, db, lock)


@pytest.mark.parametrize('member', ['database', 'lock'])
@pytest.mark.parametrize('invalid', ['nonignored-missing', 'nonignored-existing', 'tracked-existing', 'tracked-missing'])
def test_denial_preserves_entire_pair_before_storage_effects(project, member, invalid):
    settings, packet, config = case(project)
    data = project['app'] / 'shared-data'
    data.mkdir()
    db, lock = data / 'requirements.sqlite', data / 'requirements.lock'
    bad = db if member == 'database' else lock
    valid = lock if member == 'database' else db
    valid.write_bytes(b'valid-member-sentinel')
    ignore(project['app'], '/shared-data/\n' if invalid.startswith('tracked') else '/' + str(valid.relative_to(project['app'])) + '\n')
    if invalid != 'nonignored-missing':
        bad.write_bytes(b'invalid-member-sentinel')
    if invalid.startswith('tracked'):
        assert git(project['app'], 'add', '-f', '--', str(bad.relative_to(project['app']))).returncode == 0
        if invalid == 'tracked-missing':
            bad.unlink()
    assert_git_fact(project['app'], bad, ignored=invalid.startswith('tracked'), tracked=invalid.startswith('tracked'))
    edit(packet, 'requirements_database', db)
    edit(packet, 'requirements_lock', lock)
    before = pair_bytes(db, lock)
    code, reply = invoke(setup_execute, settings, packet)
    assert code == 2, reply
    assert reply['status'] == 'rejected'
    assert 'Requirements' in reply['reason']
    assert pair_bytes(db, lock) == before
    assert not config.exists()
    # Live public Requirements boundary must deny before constructing either store.
    raw = json.loads((project['root'] / 'project.json').read_text())
    raw['paths']['requirements_database'] = str(db)
    raw['paths']['requirements_lock'] = str(lock)
    live = write_json(project['root'] / 'denied.json', raw)
    code, reply = query(live)
    assert code == 2 and reply['status'] == 'rejected'
    assert pair_bytes(db, lock) == before


@pytest.mark.parametrize('failure', ['missing-executable', 'nonzero-command'])
def test_internal_git_failure_is_explicit_and_preserves_pair(project, tmp_path, monkeypatch, failure):
    settings, packet, config = case(project)
    data = project['app'] / 'shared-data'
    data.mkdir()
    db, lock = data / 'requirements.sqlite', data / 'requirements.lock'
    db.write_bytes(b'database-sentinel')
    ignore(project['app'], '/shared-data/\n')
    assert_git_fact(project['app'], db, ignored=True)
    edit(packet, 'requirements_database', db)
    edit(packet, 'requirements_lock', lock)
    executable = tmp_path / 'executables'
    executable.mkdir()
    if failure == 'nonzero-command':
        script = executable / 'git'
        script.write_text('#!/bin/sh\necho unavailable-index-check >&2\nexit 73\n')
        script.chmod(0o700)
    monkeypatch.setenv('PATH', str(executable))
    before = pair_bytes(db, lock)
    code, reply = invoke(setup_execute, settings, packet)
    assert code == 2 and reply['status'] == 'rejected', reply
    assert 'git' in reply['reason'].lower(), reply
    assert pair_bytes(db, lock) == before
    assert not config.exists()


@pytest.mark.parametrize('physical', [False, True])
@pytest.mark.parametrize('pair', [('requirements_database', 'requirements_lock'),
                                 ('requirements_database', 'database'),
                                 ('requirements_database', 'lock'),
                                 ('requirements_lock', 'database'),
                                 ('requirements_lock', 'lock')])
def test_resolved_and_physical_storage_aliases_are_rejected(project, physical, pair):
    raw = json.loads(project['config_path'].read_text())
    state = project['root'] / 'state'
    left, right = pair
    first, second = state / raw['paths'][left], state / raw['paths'][right]
    if physical:
        first.unlink(missing_ok=True)
        second.unlink(missing_ok=True)
        first.write_bytes(b'physical-identity-sentinel')
        os.link(first, second)
    else:
        raw['paths'][left] = raw['paths'][right]
    config = write_json(project['root'] / 'alias.json', raw)
    before = pair_bytes(first, second)
    code, reply = query(config)
    assert code == 2 and reply['status'] == 'rejected', reply
    assert 'storage' in reply['reason'].lower() or 'DB/lock' in reply['reason'], reply
    assert pair_bytes(first, second) == before


@pytest.mark.parametrize('members', ['absent', 'existing', 'mixed'])
def test_live_internal_admission_creates_only_configured_pair(project, members):
    raw = json.loads(project['config_path'].read_text())
    data = project['app'] / 'shared-data'
    db, lock = data / 'requirements.sqlite', data / 'requirements.lock'
    ignore(project['app'], '/shared-data/\n')
    if members != 'absent':
        data.mkdir()
        db.touch()
        if members == 'existing':
            lock.touch()
    raw['paths']['requirements_database'] = str(db)
    raw['paths']['requirements_lock'] = str(lock)
    config = write_json(project['root'] / 'live-storage.json', raw)
    assert_empty_registry(config, db, lock)
    assert json.loads(config.read_text())['paths']['requirements_database'] == str(db)


def test_ignored_internal_physical_alias_is_rejected_as_identity(project):
    raw = json.loads(project['config_path'].read_text())
    data = project['app'] / 'shared-data'
    data.mkdir()
    db, lock = data / 'requirements.sqlite', data / 'requirements.lock'
    db.write_bytes(b'identity-sentinel')
    os.link(db, lock)
    ignore(project['app'], '/shared-data/\n')
    raw['paths']['requirements_database'] = str(db)
    raw['paths']['requirements_lock'] = str(lock)
    config = write_json(project['root'] / 'internal-alias.json', raw)
    before = pair_bytes(db, lock)
    code, reply = query(config)
    assert code == 2 and reply['status'] == 'rejected', reply
    assert 'физически' in reply['reason'], reply
    assert pair_bytes(db, lock) == before


def test_invalid_update_process_cannot_publish_candidate(project):
    settings, packet, config = case(project)
    edit(packet, 'requirements_database', project['root'] / 'external.sqlite')
    edit(packet, 'requirements_lock', project['root'] / 'external.lock')
    code, created = invoke(setup_execute, settings, packet)
    assert code == 0, created
    process = config.parent / 'config/processes/development.json'
    before = config.read_bytes(), process.read_bytes()
    import hashlib
    raw = json.loads(process.read_text())
    revision = hashlib.sha256(json.dumps(raw, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()
    request = update_request(config, created['revision'], process_updates=[{
        'goal_type': 'development', 'expected_revision': revision,
        'changes': [{'op': 'patch_stage', 'id': 'tests', 'set': {'instruction': ''}}],
    }])
    code, reply = invoke(update_execute, settings, request)
    assert code == 2 and reply['status'] == 'rejected', reply
    assert (config.read_bytes(), process.read_bytes()) == before
    assert not (project['app'] / 'operations/project-update-1.json').exists()
