"""Public Requirements queries preserve reader and explicit writer contracts."""
import json
import sqlite3
import subprocess
import sys
import pytest
from batch.test_public_read_transactions import environment, writer, state_paths


def packet(name):
    from batch.test_public_read_transactions import FIXTURES
    return json.loads((FIXTURES / 'requirements-read-packets.json').read_text())[name]


def paths(project):
    state = project['root'] / project['cfg']['paths']['state']
    return state / project['cfg']['paths']['requirements_database'], state / project['cfg']['paths']['requirements_lock']


def invoke(project, request):
    return subprocess.run([sys.executable, '-B', '-m', 'poise', 'requirements', '--config', str(project['config_path'])], input=json.dumps(request), capture_output=True, text=True, env=environment(project), timeout=15)


def query(project):
    return invoke(project, packet('query'))


def snapshot(database):
    with sqlite3.connect(database.as_uri() + '?mode=ro', uri=True) as db:
        return '\n'.join(db.iterdump())


@pytest.mark.parametrize('kind', ['local-lock', 'sqlite-writer'])
def test_requirements_query_reads_committed_state_during_writer(project, kind):
    database, lock = paths(project)
    before = snapshot(database)
    with writer(project, kind, paths=(database, lock)):
        result = query(project)
        assert result.returncode == 0, 'REQUIREMENTS_QUERY_WAITS_FOR_WRITER: ' + result.stdout + result.stderr
        response = json.loads(result.stdout)
        assert response['status'] == 'read_only'
        assert response['revision'] == 1
        registry = response['results'][0]['value']
        assert set(registry['requirements']) == {'FIXTURE-SYSTEM', 'FIXTURE-APPLICATION'}
        assert registry['links'] == [{'system': 'FIXTURE-SYSTEM', 'application': 'FIXTURE-APPLICATION'}]
        assert snapshot(database) == before
    assert snapshot(database) == before
    assert not state_paths(project)[0].exists()


def test_requirements_query_does_not_initialize_absent_store(project):
    database, lock = paths(project)
    database.unlink()
    lock.unlink(missing_ok=True)
    result = query(project)
    assert result.returncode == 2, 'REQUIREMENTS_QUERY_INITIALIZED_STORE: ' + result.stdout + result.stderr
    assert not database.exists()
    assert not lock.exists()
    assert not state_paths(project)[0].exists()


@pytest.mark.parametrize('kind', ['local-lock', 'sqlite-writer'])
def test_requirements_command_preserves_writer_admission(project, kind):
    database, lock = paths(project)
    before = snapshot(database)
    request = packet('apply')
    with writer(project, kind, paths=(database, lock)):
        result = invoke(project, request)
        assert result.returncode == 2, result.stdout + result.stderr
        assert snapshot(database) == before
    result = invoke(project, request)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)['revision'] == 2
    response = json.loads(query(project).stdout)
    assert response['results'][0]['value']['requirements']['WRITER-CONTROL']['text'] == 'The authorized change is saved.'


def test_old_requirements_read_preserves_explicit_migration_recovery(project):
    from batch.test_public_read_transactions import FIXTURES
    database, _ = paths(project)
    database.unlink()
    with sqlite3.connect(database) as db:
        db.executescript((FIXTURES.parents[1] / 'requirements_registry/fixtures/schema_v1.sql').read_text())
        db.execute('INSERT INTO requirements_meta(revision) VALUES(?)', (7,))
    before = snapshot(database)
    result = query(project)
    assert result.returncode == 2
    reason = json.loads(result.stdout)['reason']
    assert 'schema 1' in reason
    assert '{"operation":"migrate","input":{}}' in reason
    assert 'python -m poise requirements --config' in reason
    assert snapshot(database) == before
    result = invoke(project, packet('migrate'))
    assert result.returncode == 0, result.stdout + result.stderr
    result = query(project)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)['revision'] == 7
