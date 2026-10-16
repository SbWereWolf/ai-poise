"""Public process reads must not acquire the independent writer's boundary."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import selectors
import sqlite3
import subprocess
import sys

import pytest


FIXTURES = Path(__file__).parent / 'fixtures'
SOURCE = Path(__file__).resolve().parents[2] / 'src'


def environment(project):
    removed = {'CODEX_SESSION_ID', 'CODEX_THREAD_ID', 'POISE_CALLER_BINDING', 'POISE_SESSION'}
    return {
        **{k: v for k, v in os.environ.items() if k not in removed},
        'PYTHONPATH': str(SOURCE),
        'POISE_CONFIG': str(project['config_path']),
        'CODEX_THREAD_ID': 'public-read-test-native',
    }


def invoke(project, fixture, env=None):
    return subprocess.run(
        [sys.executable, '-B', '-m', 'poise', 'work'],
        input=(FIXTURES / fixture).read_text(),
        capture_output=True, text=True, env=environment(project) if env is None else env, timeout=15,
    )


def state_paths(project):
    state = project['root'] / project['cfg']['paths']['state']
    return state / project['cfg']['paths']['database'], state / project['cfg']['paths']['lock']


def prepare_task(project):
    # Existing explicit test-owned Task document, submitted through the public API.
    packet = json.loads((FIXTURES / 'public-read-bootstrap.json').read_text())
    packet['input']['task'] = json.loads(project['task_path'].read_text())
    result = subprocess.run(
        [sys.executable, '-B', '-m', 'poise', 'work'], input=json.dumps(packet),
        capture_output=True, text=True, env=environment(project), timeout=20,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def query_state(database):
    with sqlite3.connect(database.as_uri() + '?mode=ro', uri=True) as db:
        return {
            'version': db.execute('PRAGMA user_version').fetchone()[0],
            'bindings': db.execute(
                'SELECT identity_key,session_id,inventory FROM runtime_bindings ORDER BY identity_key'
            ).fetchall(),
            'sessions': db.execute('SELECT id,task_id FROM sessions ORDER BY id').fetchall(),
            'tasks': db.execute('SELECT id,claimed_by,version,metadata FROM tasks ORDER BY id').fetchall(),
            'cursors': db.execute('SELECT session_id,path,data FROM runtime_cursors ORDER BY session_id,path').fetchall(),
            'journal': db.execute('SELECT seq,event,data FROM journal ORDER BY seq').fetchall(),
            'messages': db.execute('SELECT id,data FROM interaction_events ORDER BY id').fetchall(),
            'message_bindings': db.execute(
                'SELECT event_id,task_id,sprint_id,goal_type,stage,iteration,session_id,project '
                'FROM interaction_bindings ORDER BY event_id'
            ).fetchall(),
        }


@contextmanager
def writer(project, kind, paths=None, release='RELEASE'):
    database, lock = state_paths(project) if paths is None else paths
    process = subprocess.Popen(
        [sys.executable, '-B', str(FIXTURES / 'hold_public_read_writer.py'),
         kind, str(database), str(lock), str(project['root'])],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, bufsize=1,
    )
    try:
        with selectors.DefaultSelector() as ready:
            ready.register(process.stdout, selectors.EVENT_READ)
            assert ready.select(5), 'Independent test writer failed to become ready'
            assert process.stdout.readline() == 'READY\n'
        yield
    finally:
        stdout, stderr = process.communicate(release + '\n', timeout=5)
        assert process.returncode == 0, stdout + stderr


@pytest.mark.parametrize('kind', ['local-lock', 'sqlite-writer'])
def test_fresh_public_read_during_independent_writer(project, kind):
    initialized = invoke(project, 'public-read-bootstrap.json')
    assert initialized.returncode == 0, initialized.stdout + initialized.stderr
    database, _ = state_paths(project)
    with sqlite3.connect(database) as db:
        db.execute('UPDATE runtime_bindings SET inventory=?', ('["baseline-inventory"]',))
    before = query_state(database)
    with writer(project, kind):
        result = invoke(project, 'public-read-show.json')
        assert result.returncode == 0, 'PUBLIC_READ_WAITS_FOR_WRITER: ' + result.stdout + result.stderr
        envelope = json.loads(result.stdout)
        full = json.loads(Path(envelope['response_path']).read_text())
        assert full['status'] == 'read_only'
        assert full['interaction']['user_messages_count'] == 0
        assert query_state(database) == before
    assert query_state(database) == before


def test_idle_public_read_preserves_inventory_and_business_state(project):
    assert invoke(project, 'public-read-bootstrap.json').returncode == 0
    database, _ = state_paths(project)
    with sqlite3.connect(database) as db:
        db.execute('UPDATE runtime_bindings SET inventory=?', ('["baseline-inventory"]',))
    before = query_state(database)
    result = invoke(project, 'public-read-show.json')
    assert result.returncode == 0, result.stdout + result.stderr
    assert query_state(database) == before, 'PUBLIC_READ_REWRITES_INVENTORY'


@pytest.mark.parametrize('kind', ['local-lock', 'sqlite-writer'])
def test_existing_generated_caller_can_read_without_writer_admission(project, tmp_path, kind):
    env = environment(project)
    env.pop('CODEX_THREAD_ID')
    binding = tmp_path / 'generated-caller.json'
    env['POISE_CALLER_BINDING'] = str(binding)
    first = invoke(project, 'public-read-bootstrap.json', env)
    assert first.returncode == 0, first.stdout + first.stderr
    caller_bytes = binding.read_bytes()
    before = query_state(state_paths(project)[0])
    with writer(project, kind):
        result = invoke(project, 'public-read-show.json', env)
        assert result.returncode == 0, 'GENERATED_READ_WAITS_FOR_WRITER: ' + result.stdout + result.stderr
        assert json.loads(result.stdout)['session'] == json.loads(first.stdout)['session']
        assert binding.read_bytes() == caller_bytes
        assert query_state(state_paths(project)[0]) == before


def test_read_does_not_initialize_absent_task_store(project):
    database, lock = state_paths(project)
    assert not database.exists()
    result = invoke(project, 'public-read-show.json')
    assert result.returncode == 2, 'READ_INITIALIZED_ABSENT_STORE: ' + result.stdout + result.stderr
    assert result.stdout or result.stderr
    assert not database.exists()
    assert not lock.exists()


@pytest.mark.parametrize('version', [0, 12, 99])
def test_read_never_initializes_or_migrates_empty_old_or_unknown_schema(project, version):
    database, _ = state_paths(project)
    database.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(database) as db:
        db.execute(f'PRAGMA user_version={version}')
    result = invoke(project, 'public-read-show.json')
    assert result.returncode == 2, 'READ_INITIALIZED_OR_MIGRATED_SCHEMA: ' + result.stdout + result.stderr
    assert 'read-only' in result.stdout + result.stderr, 'Schema refusal must explain observation boundary'
    with sqlite3.connect(database.as_uri() + '?mode=ro', uri=True) as db:
        assert db.execute('PRAGMA user_version').fetchone()[0] == version
        assert db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall() == []


@pytest.mark.parametrize('kind', ['local-lock', 'sqlite-writer'])
def test_real_writer_still_refuses_then_succeeds_after_release(project, kind):
    assert invoke(project, 'public-read-bootstrap.json').returncode == 0
    database, _ = state_paths(project)
    before = query_state(database)
    with writer(project, kind):
        blocked = invoke(project, 'public-read-bootstrap.json')
        assert blocked.returncode == 2
        assert query_state(database) == before
    assert invoke(project, 'public-read-bootstrap.json').returncode == 0


def test_unmapped_native_read_is_observed_without_registration(project):
    assert invoke(project, 'public-read-bootstrap.json').returncode == 0
    database, _ = state_paths(project)
    before = query_state(database)
    env = {**environment(project), 'CODEX_THREAD_ID': 'unmapped-read-native'}
    with writer(project, 'local-lock'):
        result = invoke(project, 'public-read-show.json', env)
        assert result.returncode == 0, 'UNMAPPED_NATIVE_READ_WAITS_FOR_ADMISSION: ' + result.stderr
        assert json.loads(result.stdout)['session'] == 'unmapped-read-native'
    assert query_state(database) == before


def test_foreign_reserved_native_identity_is_rejected(project):
    assert invoke(project, 'public-read-bootstrap.json').returncode == 0
    database, _ = state_paths(project)
    with sqlite3.connect(database) as db:
        db.execute('INSERT INTO runtime_bindings(identity_key,session_id,inventory) VALUES(?,?,?)',
                   ('foreign-caller-key', 'reserved-read-native', '[]'))
    before = query_state(database)
    env = {**environment(project), 'CODEX_THREAD_ID': 'reserved-read-native'}
    result = invoke(project, 'public-read-show.json', env)
    assert result.returncode == 2
    assert 'Native session is already assigned to another caller' in result.stdout + result.stderr
    assert query_state(database) == before


def test_first_generated_admission_rejection_leaves_no_binding_file(project, tmp_path):
    assert invoke(project, 'public-read-bootstrap.json').returncode == 0
    env = environment(project)
    env.pop('CODEX_THREAD_ID')
    binding = tmp_path / 'unadmitted-caller.json'
    env['POISE_CALLER_BINDING'] = str(binding)
    before = query_state(state_paths(project)[0])
    with writer(project, 'local-lock'):
        result = invoke(project, 'public-read-show.json', env)
        assert result.returncode == 2
        assert not binding.exists(), 'REJECTED_ADMISSION_CREATED_CALLER_FILE'
        assert query_state(state_paths(project)[0]) == before
    admitted = invoke(project, 'public-read-show.json', env)
    assert admitted.returncode == 0, admitted.stdout + admitted.stderr
    first = query_state(state_paths(project)[0])
    assert len(first['bindings']) == 2
    assert invoke(project, 'public-read-show.json', env).returncode == 0
    assert query_state(state_paths(project)[0]) == first


def test_invalid_read_preserves_primary_error_and_journal(project):
    assert invoke(project, 'public-read-bootstrap.json').returncode == 0
    database, _ = state_paths(project)
    before = query_state(database)
    with writer(project, 'local-lock'):
        result = invoke(project, 'public-read-invalid.json')
        assert result.returncode == 2
        assert 'Unknown batch query kind' in result.stdout + result.stderr, 'PRIMARY_QUERY_ERROR_REPLACED_BY_LOCK'
    assert query_state(database) == before


def test_read_does_not_create_absent_requirements_store(project):
    assert invoke(project, 'public-read-bootstrap.json').returncode == 0
    state = project['root'] / project['cfg']['paths']['state']
    requirements = state / project['cfg']['paths']['requirements_database']
    requirements.unlink()
    before = query_state(state_paths(project)[0])
    result = invoke(project, 'public-read-show.json')
    assert result.returncode == 2, 'READ_INITIALIZED_REQUIREMENTS_STORE: ' + result.stdout + result.stderr
    assert not requirements.exists()
    assert query_state(state_paths(project)[0]) == before


@pytest.mark.parametrize('kind', ['local-lock', 'sqlite-writer'])
def test_reported_message_ingestion_remains_writer_owned(project, kind):
    assert invoke(project, 'public-read-bootstrap.json').returncode == 0
    database, _ = state_paths(project)
    before = query_state(database)
    with writer(project, kind):
        blocked = invoke(project, 'public-read-reported.json')
        assert blocked.returncode == 2
        assert query_state(database) == before
    accepted = invoke(project, 'public-read-reported.json')
    assert accepted.returncode == 0, accepted.stdout + accepted.stderr
    first = query_state(database)
    assert len(first['messages']) == 1
    assert json.loads(first['messages'][0][1])['subject'] == 'read-subject'
    assert invoke(project, 'public-read-reported.json').returncode == 0
    assert query_state(database) == first
    rejected = invoke(project, 'public-read-conflicting.json')
    assert rejected.returncode == 2
    assert 'Message identity reused with conflicting event content' in rejected.stdout + rejected.stderr
    after = query_state(database)
    assert after['messages'] == first['messages']
    assert len(after['journal']) == len(first['journal']) + 1


def message_count(result):
    assert result.returncode == 0, result.stdout + result.stderr
    envelope = json.loads(result.stdout)
    return json.loads(Path(envelope['response_path']).read_text())['interaction']['user_messages_count']


def test_read_observes_committed_wal_state_before_and_after_writer_commit(project):
    assert invoke(project, 'public-read-bootstrap.json').returncode == 0
    database, _ = state_paths(project)
    with sqlite3.connect(database) as db:
        assert db.execute('PRAGMA journal_mode=WAL').fetchone()[0] == 'wal'
    before = query_state(database)
    with writer(project, 'wal-message', release='COMMIT'):
        result = invoke(project, 'public-read-show.json')
        assert result.returncode == 0, 'WAL_READ_WAITS_FOR_UNCOMMITTED_WRITER: ' + result.stderr
        assert message_count(result) == 0
        assert query_state(database) == before
    assert message_count(invoke(project, 'public-read-show.json')) == 1
    after = query_state(database)
    assert len(after['messages']) == 1
    assert json.loads(after['messages'][0][1])['subject'] == 'committed-value'
    assert {k: v for k, v in after.items() if k != 'messages'} == {
        k: v for k, v in before.items() if k != 'messages'
    }


@pytest.mark.parametrize('boundary', ['task-local-lock', 'task-sqlite-writer', 'hook-local-lock', 'hook-sqlite-writer'])
def test_fresh_hook_query_observes_already_recorded_source_without_writes(project, tmp_path, boundary):
    service, binding = prepare_hook(project, tmp_path)
    paths = ((service.settings.database, service.settings.lock) if boundary.startswith('hook-')
             else state_paths(project))
    kind = 'local-lock' if boundary.endswith('local-lock') else 'sqlite-writer'
    before = query_state(state_paths(project)[0])
    assert len(before['messages']) == 1
    assert before['tasks'][0][:2] == ('T1', 'public-read-test-native')
    assert len(before['message_bindings']) == 1
    assert before['message_bindings'][0][0] == before['messages'][0][0]
    assert before['message_bindings'][0][1:] == (
        'T1', None, 'development', 'tests', 1, 'public-read-test-native', 'demo',
    )
    with sqlite3.connect(service.settings.database.as_uri() + '?mode=ro', uri=True) as db:
        registry_before = db.execute('SELECT id,data,latest_message FROM bindings ORDER BY id').fetchall()
        events_before = db.execute('SELECT id,at,binding,event,data FROM hook_events ORDER BY id').fetchall()
    with writer(project, kind, paths=paths):
        result = invoke_hook(binding)
        assert result.returncode == 0, 'HOOK_QUERY_WAITS_FOR_WRITER: ' + result.stdout + result.stderr
        assert message_count(result) == 1
        assert query_state(state_paths(project)[0]) == before
        with sqlite3.connect(service.settings.database.as_uri() + '?mode=ro', uri=True) as db:
            assert db.execute('SELECT id,data,latest_message FROM bindings ORDER BY id').fetchall() == registry_before
            assert db.execute('SELECT id,at,binding,event,data FROM hook_events ORDER BY id').fetchall() == events_before


@pytest.mark.parametrize('boundary', ['task-local-lock', 'task-sqlite-writer', 'requirements-local-lock', 'requirements-sqlite-writer'])
def test_current_task_query_preserves_claim_and_all_store_state(project, boundary):
    prepare_task(project)
    database, _ = state_paths(project)
    before = query_state(database)
    assert before['tasks'][0][:2] == ('T1', 'public-read-test-native')
    state = project['root'] / project['cfg']['paths']['state']
    requirements = state / project['cfg']['paths']['requirements_database']
    requirements_lock = state / project['cfg']['paths']['requirements_lock']
    paths = (requirements, requirements_lock) if boundary.startswith('requirements-') else state_paths(project)
    with sqlite3.connect(requirements.as_uri() + '?mode=ro', uri=True) as db:
        requirements_before = db.execute('SELECT id,level,status,text FROM requirements ORDER BY id').fetchall()
        revision_before = db.execute('SELECT revision FROM requirements_meta').fetchall()
    with writer(project, 'local-lock' if boundary.endswith('local-lock') else 'sqlite-writer', paths=paths):
        result = invoke(project, 'public-read-task-queries.json')
        assert result.returncode == 0, 'TASK_QUERY_WAITS_FOR_WRITER: ' + result.stdout + result.stderr
        envelope = json.loads(result.stdout)
        full = json.loads(Path(envelope['response_path']).read_text())
        assert full['results'][0]['value']['task'] == 'T1'
        assert full['results'][1]['value']['status'] == 'delivery_unagreed'
        assert query_state(database) == before
        with sqlite3.connect(requirements.as_uri() + '?mode=ro', uri=True) as db:
            assert db.execute('SELECT id,level,status,text FROM requirements ORDER BY id').fetchall() == requirements_before
            assert db.execute('SELECT revision FROM requirements_meta').fetchall() == revision_before


def test_optional_telemetry_does_not_make_foreground_query_a_writer(project):
    assert invoke(project, 'public-read-bootstrap.json').returncode == 0
    database, _ = state_paths(project)
    before = query_state(database)
    with writer(project, 'local-lock'):
        result = invoke(project, 'public-read-telemetry.json')
        assert result.returncode == 0, 'TELEMETRY_QUERY_WAITS_FOR_WRITER: ' + result.stdout + result.stderr
        assert message_count(result) == 0
        assert query_state(database) == before


def prepare_hook(project, tmp_path):
    from hook_transport.helpers import settings, install, event
    from poise.infrastructure.hook_transport import HookService

    service = HookService(settings(project, tmp_path))
    installed = install(service)
    start = event(session='public-read-test-native')
    start['cwd'] = str(service.settings.root)
    service.event(installed['definition_path'], start)
    prepare_task(project)
    prompt = event('UserPromptSubmit', session='public-read-test-native')
    prompt['cwd'] = str(service.settings.root)
    service.event(installed['definition_path'], prompt)
    return service, service.latest_binding('public-read-test-native', 'primary')


def invoke_hook(binding):
    from hook_transport.helpers import host_environment

    return subprocess.run(
        ['bash', binding['launcher']], input=(FIXTURES / 'public-read-show.json').read_text(),
        capture_output=True, text=True, env=host_environment('public-read-test-native'), timeout=15,
    )


def test_primary_binding_oracle_detects_isolated_stage_and_session_rebinding(project, tmp_path):
    prepare_hook(project, tmp_path)
    database, _ = state_paths(project)
    before = query_state(database)
    assert len(before['message_bindings']) == 1
    row = before['message_bindings'][0]
    assert row[1:] == ('T1', None, 'development', 'tests', 1, 'public-read-test-native', 'demo')
    try:
        with sqlite3.connect(database) as db:
            db.execute('UPDATE interaction_bindings SET stage=?,session_id=? WHERE event_id=?',
                       ('changed-stage', 'changed-session', row[0]))
        changed = query_state(database)
        assert changed != before
        assert changed['message_bindings'][0][4] == 'changed-stage'
        assert changed['message_bindings'][0][6] == 'changed-session'
        assert {k: v for k, v in changed.items() if k != 'message_bindings'} == {
            k: v for k, v in before.items() if k != 'message_bindings'
        }
    finally:
        with sqlite3.connect(database) as db:
            db.execute('UPDATE interaction_bindings SET stage=?,session_id=? WHERE event_id=?',
                       (row[4], row[6], row[0]))
    assert query_state(database) == before


@pytest.mark.parametrize('condition', ['absent', 'empty', 'unsupported'])
def test_hook_observation_never_initializes_or_migrates_registry(project, tmp_path, condition):
    service, binding = prepare_hook(project, tmp_path)
    before = query_state(state_paths(project)[0])
    database = service.settings.database
    if condition == 'absent':
        database.unlink()
    elif condition == 'empty':
        database.unlink()
        with sqlite3.connect(database) as db:
            db.execute('PRAGMA user_version=0')
    else:
        with sqlite3.connect(database) as db:
            db.execute('PRAGMA user_version=99')
    original = database.read_bytes() if database.exists() else None
    result = invoke_hook(binding)
    assert result.returncode == 2
    envelope = json.loads(result.stdout)
    full = json.loads(Path(envelope['response_path']).read_text())
    assert full['status'] == 'rejected'
    assert full['reason']
    assert query_state(state_paths(project)[0]) == before
    if original is None:
        assert not database.exists(), 'HOOK_QUERY_INITIALIZED_ABSENT_REGISTRY'
    else:
        assert database.read_bytes() == original, 'HOOK_QUERY_CHANGED_REGISTRY_SCHEMA_OR_ROWS'


@pytest.mark.parametrize('version', [1, 99])
def test_unsupported_requirements_observation_preserves_other_store_state(project, version):
    assert invoke(project, 'public-read-bootstrap.json').returncode == 0
    database, _ = state_paths(project)
    with sqlite3.connect(database) as db:
        db.execute('UPDATE runtime_bindings SET inventory=?', ('["baseline-inventory"]',))
    state = project['root'] / project['cfg']['paths']['state']
    requirements = state / project['cfg']['paths']['requirements_database']
    with sqlite3.connect(requirements) as db:
        db.execute(f'PRAGMA user_version={version}')
    original = requirements.read_bytes()
    before = query_state(database)
    result = invoke(project, 'public-read-show.json')
    assert result.returncode == 2
    assert result.stdout or result.stderr
    assert requirements.read_bytes() == original
    assert query_state(database) == before, 'REJECTED_REQUIREMENTS_READ_CHANGED_CALLER_STATE'


def test_public_adapter_query_without_transcript_preserves_state_during_writer(project, tmp_path):
    from runtime_services.test_adapter import settings

    options = tmp_path / 'adapter-settings.json'
    options.write_text(json.dumps(settings(project, tmp_path)))
    packet = json.loads((FIXTURES / 'public-read-adapter-bootstrap.json').read_text())

    def call():
        return subprocess.run(
            [sys.executable, '-B', '-m', 'poise', 'runtime', '--settings', str(options)],
            input=json.dumps(packet), capture_output=True, text=True,
            env=environment(project), timeout=15,
        )

    initialized = call()
    assert initialized.returncode == 0, initialized.stdout + initialized.stderr
    native = json.loads(initialized.stdout)['session']
    database, _ = state_paths(project)
    with sqlite3.connect(database) as db:
        db.execute('INSERT INTO runtime_cursors(session_id,path,data) VALUES(?,?,?)',
                   (native, 'existing-transcript', '{"offset":17}'))
    before = query_state(database)
    packet['work'] = json.loads((FIXTURES / 'public-read-show.json').read_text())
    with writer(project, 'local-lock'):
        result = call()
        assert result.returncode == 0, 'ADAPTER_QUERY_WAITS_FOR_WRITER: ' + result.stdout + result.stderr
        assert json.loads(result.stdout)['session'] == native
        assert message_count(result) == 0
        assert query_state(database) == before
    assert query_state(database) == before
