"""Cross-project discovery must never bootstrap sessions or write stores."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

import pytest

from poise.composition import project_tools
from conftest import WorkPoise, seed_fixture_requirements, write_json
from sprints.helpers import setup, task, changes
from .helpers import setup_case


def configured(project):
    setup(project)
    settings, _, _ = setup_case(project)
    runtimes = {}
    entries = {}
    for name in ('zeta', 'alpha'):
        cfg = deepcopy(project['cfg'])
        cfg['project'] = name
        cfg['paths']['state'] = f'state-{name}'
        config = write_json(project['root'] / f'{name}.json', cfg)
        seed_fixture_requirements(project['root'], cfg)
        runtimes[name] = WorkPoise(config, f'planner-{name}')
        entries[name] = {'config_path': f'{name}.json'}
    write_json(project['root'] / 'state/project-registry.json', {
        'schema': 'configured-project-registry-1', 'projects': entries,
    })
    return settings, runtimes


def available(project, runtime, identifier):
    body = task(project, identifier)
    body['sprint_id'] = None
    runtime.task_commands.create(
        body, runtime.session, runtime.processes['development'], [],
        {'config_hash': runtime.config_hash}, None, runtime.cfg.get('task_ids'),
        runtime._creation_base(), runtime.cfg['task_decomposition'],
    )


def snapshot(root):
    return {str(p.relative_to(root)): (p.read_bytes(), p.stat().st_mode, p.stat().st_mtime_ns)
            for p in root.rglob('*') if p.is_file()}


def ids(result):
    return [(item['project'], item['task']) for item in result['tasks']]


def test_next_lists_every_startable_task_ordered_without_any_writes(project):
    settings, runtimes = configured(project)
    for name, runtime in runtimes.items():
        available(project, runtime, 'Z')
        available(project, runtime, 'A')
    before = snapshot(project['root'])
    result = project_tools(settings).next()
    assert result['status'] == 'listed'
    assert result['errors'] == []
    assert ids(result) == [('alpha', 'A'), ('alpha', 'Z'), ('zeta', 'A'), ('zeta', 'Z')]
    assert all(item['goal'] and item['sprint'] is None and
               Path(item['config_path']).is_absolute() for item in result['tasks'])
    assert project_tools(settings).next() == result
    assert snapshot(project['root']) == before


@pytest.mark.parametrize('status,owner,handoff', [
    ('available', 'another-session', False),
    ('active', 'another-session', False),
    ('verified', 'another-session', False),
    ('accepted', 'another-session', False),
    ('active', None, True), ('verified', None, True), ('accepted', None, True),
    ('completed', None, False), ('cancelled', None, False),
])
def test_next_excludes_claimed_terminal_and_resume_states(project, status, owner, handoff):
    settings, runtimes = configured(project)
    runtime = runtimes['alpha']
    available(project, runtime, 'X')
    # Exact persisted-state fixture; do not execute a workflow to test a read API.
    with runtime.store.transaction() as db:
        db.execute('UPDATE tasks SET status=?,claimed_by=? WHERE id=?', (status, owner, 'X'))
        if handoff:
            db.execute('INSERT INTO handoffs(actor,request_id,task_id,state,data) VALUES(?,?,?,?,?)',
                       ('previous', 'release-X', 'X', 'released', '{}'))
    before = snapshot(project['root'])
    assert ids(project_tools(settings).next()) == []
    assert snapshot(project['root']) == before


def test_next_uses_sprint_dependencies_waivers_and_result_provenance(project):
    settings, runtimes = configured(project)
    runtime = runtimes['alpha']
    tasks = [task(project, i) for i in ('A', 'B', 'C', 'D', 'E')]
    edges = [{'predecessor': 'A', 'successor': 'B', 'kind': 'completion'},
             {'predecessor': 'C', 'successor': 'D', 'kind': 'result'}]
    result = runtime.sprint_tools.apply({
        'action': 'draft', 'sprint_id': 'S', 'request_id': 'draft-S',
        'expected_revision': None, 'template': {'id': 'basic', 'version': '1'},
        'changes': changes(tasks, edges),
    })
    runtime.sprint_tools.apply({'action': 'publish', 'sprint_id': 'S',
                               'request_id': 'publish-S', 'expected_revision': result['revision']})
    with runtime.store.transaction() as db:
        db.execute("UPDATE tasks SET status='completed' WHERE id='C'")
        db.execute("UPDATE tasks SET claimed_by='foreign' WHERE id='E'")
    before = snapshot(project['root'])
    assert ids(project_tools(settings).next()) == [('alpha', 'A')]
    assert snapshot(project['root']) == before
    with runtime.store.transaction() as db:
        db.execute("UPDATE tasks SET status='completed' WHERE id='A'")
    assert ids(project_tools(settings).next()) == [('alpha', 'B')]


def test_next_excludes_unpublished_newborn_and_entry_blocked(project):
    settings, runtimes = configured(project)
    runtime = runtimes['alpha']
    runtime.task_commands.create_newborn('DRAFT', None, runtime.session, runtime.config_hash, 'draft')
    available(project, runtime, 'BLOCKED')
    # A preexisting input required at entry is missing, while the route stays valid.
    with runtime.store.transaction() as db:
        row = db.execute("SELECT metadata FROM tasks WHERE id='BLOCKED'").fetchone()
        metadata = json.loads(row[0])
        metadata['contract']['content_contract']['requirements'].append({
            'id': 'entry-report', 'kind': 'section', 'phase': 'pre', 'section': 'report',
            'stages': ['work'], 'states': ['populated'],
        })
        metadata['contract']['stage_contracts'][0]['entry_requirements'] = ['entry-report']
        db.execute("UPDATE tasks SET metadata=? WHERE id='BLOCKED'", (json.dumps(metadata),))
        # Contract layers are the persisted ContentPolicy owner.
        layer = json.loads(db.execute("SELECT data FROM content_contracts WHERE task_id='BLOCKED' ORDER BY version DESC LIMIT 1").fetchone()[0])
        layer['task']['requirements'].append(metadata['contract']['content_contract']['requirements'][-1])
        db.execute("UPDATE content_contracts SET data=? WHERE task_id='BLOCKED'", (json.dumps(layer),))
    before = snapshot(project['root'])
    assert ids(project_tools(settings).next()) == []
    assert snapshot(project['root']) == before


def test_next_exact_filter_empty_result_and_unknown_diagnostic(project):
    settings, runtimes = configured(project)
    available(project, runtimes['alpha'], 'X')
    assert ids(project_tools(settings).next('alpha')) == [('alpha', 'X')]
    assert project_tools(settings).next('zeta') == {'status': 'listed', 'tasks': [], 'errors': []}
    for name in ('ALPHA', 'alp', 'absent'):
        result = project_tools(settings).next(name)
        assert result['status'] == 'listed_with_errors' and result['tasks'] == []
        assert result['errors'][0]['project'] == name
        assert result['errors'][0]['status'] == 'unknown'


@pytest.mark.parametrize('failure', ['missing', 'corrupt', 'old_schema', 'bad_config'])
def test_next_reports_project_failures_without_creating_or_migrating_db(project, failure):
    settings, runtimes = configured(project)
    alpha = runtimes['alpha']
    available(project, runtimes['zeta'], 'OK')
    if failure == 'missing':
        alpha.store.path.unlink()
    elif failure == 'corrupt':
        alpha.store.path.write_bytes(b'not sqlite')
    elif failure == 'old_schema':
        with alpha.store.transaction() as db:
            db.execute('PRAGMA user_version=11')
    else:
        write_json(alpha.config_path, {})
    before = snapshot(project['root'])
    result = project_tools(settings).next()
    assert result['status'] == 'listed_with_errors'
    assert ids(result) == [('zeta', 'OK')]
    assert len(result['errors']) == 1
    assert result['errors'][0]['project'] == 'alpha' and result['errors'][0]['reason']
    assert project_tools(settings).next('zeta')['errors'] == []
    assert snapshot(project['root']) == before


def test_next_cli_is_taskless_and_exits_nonzero_on_project_errors(project):
    settings, runtimes = configured(project)
    available(project, runtimes['alpha'], 'A')
    before = snapshot(project['root'])
    args = [sys.executable, '-B', '-m', 'poise', 'next', '--settings', str(settings)]
    good = subprocess.run(args + ['--project', 'alpha'], capture_output=True, text=True)
    assert good.returncode == 0, good.stdout + good.stderr
    assert ids(json.loads(good.stdout)) == [('alpha', 'A')]
    bad = subprocess.run(args + ['--project', 'missing'], capture_output=True, text=True)
    assert bad.returncode == 2
    assert json.loads(bad.stdout)['errors'][0]['status'] == 'unknown'
    assert snapshot(project['root']) == before


def entry_rule(runtime, tid, rule):
    """Persist a valid fixture contract through its actual storage layers."""
    with runtime.store.transaction() as db:
        meta = json.loads(db.execute('SELECT metadata FROM tasks WHERE id=?', (tid,)).fetchone()[0])
        meta['contract']['content_contract']['requirements'].append(rule)
        meta['contract']['stage_contracts'][0]['entry_requirements'].append(rule['id'])
        db.execute('UPDATE tasks SET metadata=? WHERE id=?', (json.dumps(meta), tid))
        data = json.loads(db.execute('SELECT data FROM content_contracts WHERE task_id=? ORDER BY version DESC LIMIT 1', (tid,)).fetchone()[0])
        data['task']['requirements'].append(rule)
        db.execute('UPDATE content_contracts SET data=? WHERE task_id=?', (json.dumps(data), tid))


def test_next_requires_actual_unchanged_registered_entry_artifacts(project):
    from poise.artifacts import inspect_paths
    from poise.infrastructure.task_paths import task_root
    settings, runtimes = configured(project)
    h = runtimes['alpha']; available(project, h, 'A')
    entry_rule(h, 'A', {'id': 'input-file', 'kind': 'artifact', 'stages': ['work'],
        'phase': 'pre', 'scope': 'task', 'pattern': 'input.txt', 'minimum': 1,
        'maximum': 1, 'source': {'kind': 'preexisting'}})
    home = task_root(h.state, h.paths, 'A', None)
    file = home / h.cfg['batch']['artifact_directories']['task'] / 'input.txt'
    file.parent.mkdir(parents=True); file.write_text('registered input')
    assert ids(project_tools(settings).next()) == []  # Existence is not registration.
    with h.store.unit_of_work() as unit:
        unit.artifacts.link_task('A', inspect_paths([str(file)], {'task': home}, {'task': 'A'}))
    before = snapshot(project['root'])
    assert ids(project_tools(settings).next()) == [('alpha', 'A')]
    assert snapshot(project['root']) == before
    file.write_text('changed input')
    assert ids(project_tools(settings).next()) == []
    file.unlink()
    assert ids(project_tools(settings).next()) == []


@pytest.mark.parametrize('history,pending,expected', [
    (False, None, []), (True, {'kind': 'unfinished'}, []), (True, None, [('alpha', 'A')]),
])
def test_next_obeys_existing_restart_execution_gate(project, history, pending, expected):
    settings, runtimes = configured(project)
    h = runtimes['alpha']; available(project, h, 'A')
    with h.store.unit_of_work() as unit:
        unit.execution.create('A', {**h._execution_reservation('A', h._creation_base(), False), 'pending': pending})
    if history:
        with h.store.transaction() as db:
            meta = json.loads(db.execute("SELECT metadata FROM tasks WHERE id='A'").fetchone()[0])
            meta['restart_history'] = [{'reason': 'explicit recovery fixture'}]
            db.execute("UPDATE tasks SET metadata=? WHERE id='A'", (json.dumps(meta),))
    before = snapshot(project['root'])
    assert ids(project_tools(settings).next()) == expected
    assert snapshot(project['root']) == before


def test_next_accepts_explicit_dependency_waiver(project):
    settings, runtimes = configured(project)
    h = runtimes['alpha']
    plan = h.sprint_tools.apply({'action': 'draft', 'sprint_id': 'S', 'request_id': 'draft',
        'expected_revision': None, 'template': {'id': 'basic', 'version': '1'},
        'changes': changes([task(project, 'A'), task(project, 'B')],
                           [{'predecessor': 'A', 'successor': 'B', 'kind': 'result'}])})
    h.sprint_tools.apply({'action': 'publish', 'sprint_id': 'S', 'request_id': 'publish',
                         'expected_revision': plan['revision']})
    with h.store.transaction() as db:
        db.execute("UPDATE tasks SET status='cancelled' WHERE id='A'")
    assert ids(project_tools(settings).next()) == []
    h.sprint_tools.apply({'action': 'waive_dependencies', 'sprint_id': 'S', 'request_id': 'waive',
        'decisions': [{'predecessor': 'A', 'successor': 'B', 'reason': 'User permits proceeding without A'}]})
    before = snapshot(project['root'])
    assert ids(project_tools(settings).next()) == [('alpha', 'B')]
    assert snapshot(project['root']) == before


def test_next_excludes_members_of_unpublished_or_cancelled_sprint(project):
    settings, runtimes = configured(project)
    h = runtimes['alpha']
    plan = h.sprint_tools.apply({'action': 'draft', 'sprint_id': 'S', 'request_id': 'draft',
        'expected_revision': None, 'template': {'id': 'basic', 'version': '1'},
        'changes': changes([task(project, 'A')])})
    assert ids(project_tools(settings).next()) == []
    h.sprint_tools.apply({'action': 'publish', 'sprint_id': 'S', 'request_id': 'publish',
                         'expected_revision': plan['revision']})
    for state in ('draft', 'cancelled'):
        with h.store.transaction() as db:
            record = json.loads(db.execute("SELECT data FROM sprints WHERE id='S'").fetchone()[0])
            record['aggregate']['state'] = state
            db.execute("UPDATE sprints SET state=?,data=? WHERE id='S'", (state, json.dumps(record)))
        before = snapshot(project['root'])
        assert ids(project_tools(settings).next()) == []
        assert snapshot(project['root']) == before


def test_next_database_connection_rejects_accidental_writes(project, monkeypatch):
    from poise.infrastructure.sqlite.tasks import SqliteTaskRepository
    import sqlite3
    settings, runtimes = configured(project)
    available(project, runtimes['alpha'], 'A')
    actual = SqliteTaskRepository.start_candidates
    def no_writes(self):
        with pytest.raises(sqlite3.OperationalError, match='readonly'):
            self.db.execute("UPDATE tasks SET claimed_by='would-be-writer'")
        return actual(self)
    monkeypatch.setattr(SqliteTaskRepository, 'start_candidates', no_writes)
    before = snapshot(project['root'])
    assert ids(project_tools(settings).next()) == [('alpha', 'A')]
    assert snapshot(project['root']) == before


def test_next_uses_task_owner_not_just_available_status(project, monkeypatch):
    from poise.modules.tasks.domain import Task
    settings, runtimes = configured(project)
    available(project, runtimes['alpha'], 'A')
    called = []
    def not_ready(self, artifacts):
        called.append(self.state.task_id)
        return False
    monkeypatch.setattr(Task, 'can_start', not_ready)
    assert ids(project_tools(settings).next()) == []
    assert called == ['A']


def test_next_reports_inaccessible_store_and_empty_registry(project, monkeypatch):
    import poise.infrastructure.project_availability as adapter
    settings, runtimes = configured(project)
    available(project, runtimes['alpha'], 'A')
    connect = adapter.sqlite3.connect
    def inaccessible(path, *args, **kwargs):
        if 'state-alpha' in path:
            raise PermissionError('injected inaccessible Task DB')
        return connect(path, *args, **kwargs)
    monkeypatch.setattr(adapter.sqlite3, 'connect', inaccessible)
    before = snapshot(project['root'])
    out = project_tools(settings).next()
    assert out['tasks'] == [] and out['errors'][0]['project'] == 'alpha'
    assert 'inaccessible' in out['errors'][0]['reason']
    assert snapshot(project['root']) == before
    write_json(project['root']/'state/project-registry.json', {
        'schema': 'configured-project-registry-1', 'projects': {}})
    assert project_tools(settings).next() == {'status': 'listed', 'tasks': [], 'errors': []}
