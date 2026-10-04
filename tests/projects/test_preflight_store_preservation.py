"""Real selected stores: duplicate labels, claims, pending work and recovery."""
from copy import deepcopy
import json
from pathlib import Path
import sqlite3

import pytest

from poise.application.work import WorkTools
from poise.common import PoiseError
from tests.conftest import WorkPoise, seed_fixture_requirements, write_json
from sprints.helpers import setup, task
from .test_project_preflight import case, expected, fixture, invoke, request, snapshot
from .test_update import installed_project, update_request, update_tools


def create_available(project, config, identifier):
    seed_fixture_requirements(Path(config).parent, json.loads(Path(config).read_text()))
    runtime = WorkPoise(config, f'fixture-planner-{identifier}')
    body = task(project, identifier)
    body['sprint_id'] = None
    runtime.task_commands.create(
        body, runtime.session, runtime.processes['development'], [],
        {'config_hash': runtime.config_hash}, None, runtime.cfg.get('task_ids'),
        runtime._creation_base(), runtime.cfg['task_decomposition'])
    return runtime


def selected_stores(case):
    project, _, cfg, values = case
    setup(project)
    first = create_available(project, project['config_path'], 'EXISTS-HERE')
    second_cfg = deepcopy(cfg)
    second_cfg['paths']['state'] = 'other-state'
    second = write_json(project['root'] / 'other-project.json', second_cfg)
    seed_fixture_requirements(project['root'], second_cfg)
    other = WorkPoise(second, 'fixture-other-store')
    # A real domain-created Task plus exact persisted nonterminal ownership and
    # interrupted execution. Diagnostic must not repair either owner.
    with sqlite3.connect(values['task_db']) as db:
        db.execute('UPDATE tasks SET claimed_by=? WHERE id=?', ('foreign-owner', 'EXISTS-HERE'))
        row = db.execute('SELECT data FROM task_execution WHERE task_id=?', ('EXISTS-HERE',)).fetchone()
        if row is not None:
            execution = json.loads(row[0])
            execution['pending'] = {'kind': 'checks', 'fixture': 'preserve-exactly'}
            db.execute('UPDATE task_execution SET data=? WHERE task_id=?',
                       (json.dumps(execution), 'EXISTS-HERE'))
        else:
            db.execute('INSERT INTO task_execution(task_id,data,version) VALUES(?,?,?)',
                       ('EXISTS-HERE', '{"pending":{"kind":"checks","fixture":"preserve-exactly"}}', 0))
    artifact = project['root'] / 'state/standalone/EXISTS-HERE/artifacts/preserved.txt'
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text('Existing immutable fixture material\n')
    with sqlite3.connect(values['task_db']) as db:
        db.execute('INSERT INTO artifacts(id,owner,scope,path,digest) VALUES(?,?,?,?,?)',
                   ('preserved-artifact', 'EXISTS-HERE', 'task', str(artifact), 'fixture-digest'))
        db.execute('INSERT INTO task_artifacts(task_id,artifact_id) VALUES(?,?)',
                   ('EXISTS-HERE', 'preserved-artifact'))
    (project['app'] / 'untracked-wip.txt').write_text('Uncommitted operator material\n')
    (project['app'] / 'src/double.py').write_text('def double(n):\n    return n + 7\n')
    return first, other, second


def test_exact_task_in_selected_store_is_found_without_acquisition(case):
    selected_stores(case)
    body = request(case)
    body['task_id'] = 'EXISTS-HERE'
    wanted = expected(case)
    wanted['context']['requested_task_id'] = 'EXISTS-HERE'
    wanted['checks'][3]['status'] = 'passed'
    assert invoke(case, body) == (17, wanted)


def test_same_project_label_never_searches_another_store(case):
    _, _, other_config = selected_stores(case)
    body = request(case)
    body.update(config_path=str(other_config), task_id='EXISTS-HERE')
    code, actual = invoke(case, body)
    assert code == 23 and actual['status'] == 'rejected' and actual['ready'] is False
    assert actual['context'] == fixture('success.json', {
        **case[3], 'config': str(other_config),
        'state': str(case[0]['root'] / 'other-state'),
        'task_db': str(case[0]['root'] / 'other-state/state.sqlite'),
        'task_lock': str(case[0]['root'] / 'other-state/state.lock'),
        'requirements_db': str(case[0]['root'] / 'other-state/requirements.sqlite'),
        'requirements_lock': str(case[0]['root'] / 'other-state/requirements.lock'),
    })['context'] | {'requested_task_id': 'EXISTS-HERE'}
    assert actual['checks'][:3] == expected(case)['checks'][:3]
    assert actual['checks'][3]['status'] == 'rejected'
    assert actual['reason'] == actual['checks'][3]['reason']
    assert 'EXISTS-HERE' in actual['reason']
    assert actual['recovery'] == fixture('recovery.json', {**case[3], 'config': str(other_config)})


def test_absent_selected_database_is_not_created(case):
    body = request(case)
    body['task_id'] = 'ABSENT'
    path = Path(case[3]['task_db'])
    assert not path.exists()
    code, actual = invoke(case, body)
    assert code == 23 and actual['ready'] is False
    assert actual['checks'][3]['status'] == 'rejected'
    assert str(path) in actual['checks'][3]['reason']
    assert not path.exists()


def test_configuration_denial_preserves_existing_claim_pending_history_and_artifacts(case):
    selected_stores(case)
    project, _, cfg, values = case
    cfg['paths']['requirements_lock'] = str(project['app'] / 'forbidden.lock')
    write_json(project['config_path'], cfg)
    values['requirements_lock'] = str(project['app'] / 'forbidden.lock')
    code, actual = invoke(case, request(case))
    assert code == 23 and actual['ready'] is False
    assert actual['reason'] == 'Requirements storage must be outside the served codebase'
    with sqlite3.connect(f"file:{values['task_db']}?mode=ro", uri=True) as db:
        assert db.execute('SELECT claimed_by FROM tasks WHERE id=?', ('EXISTS-HERE',)).fetchone() == ('foreign-owner',)
        assert 'preserve-exactly' in db.execute('SELECT data FROM task_execution WHERE task_id=?', ('EXISTS-HERE',)).fetchone()[0]
        assert db.execute('SELECT artifact_id FROM task_artifacts WHERE task_id=?', ('EXISTS-HERE',)).fetchall() == [('preserved-artifact',)]


def test_existing_work_unknown_task_names_actual_selected_context(case):
    _, other, config = selected_stores(case)
    before = snapshot(case[0]['root'].parent)
    with pytest.raises(PoiseError) as failure:
        WorkTools(other).invoke({
            'operation': 'bootstrap',
            'input': {'task': {'id': 'EXISTS-HERE'}, 'decision': None,
                      'feedback': None, 'rework_stage': None}, 'messages': []})
    # The existing work transport has its separate optional telemetry owner;
    # this assertion proves Task/store/Git/material preservation, not absence
    # of work-transport accounting. The new check tests exclude no paths.
    def business(entries):
        return {p: (v[0], v[4]) for p, v in entries.items()
                if 'fixture-telemetry' not in Path(p).parts}
    assert business(snapshot(case[0]['root'].parent)) == business(before)
    reason = str(failure.value)
    assert 'Неизвестный task/sprint ID' in reason
    for identity in (case[3]['source'], str(config), 'demo',
                     str(case[0]['root'] / 'other-state'), case[3]['repository'], 'POISE_CONFIG'):
        assert identity in reason


@pytest.mark.parametrize('condition', ['stale', 'claimed', 'pending'])
def test_advertised_git_update_keeps_revision_claim_and_pending_guards(project, condition):
    setup(project)
    del project['cfg']['processes']['documentation']
    write_json(project['config_path'], project['cfg'])
    settings, config, created = installed_project(project)
    if condition != 'stale':
        create_available(project, config, 'OWNED')
        db_path = config.parent / 'state/state.sqlite'
        with sqlite3.connect(db_path) as db:
            if condition == 'claimed':
                db.execute('UPDATE tasks SET claimed_by=? WHERE id=?', ('another-owner', 'OWNED'))
            else:
                # Explicit pending metadata on this fixture's nonterminal row;
                # active-work refusal must precede any publication or cleanup.
                db.execute('INSERT OR REPLACE INTO task_execution(task_id,data,version) VALUES(?,?,?)',
                           ('OWNED', '{"pending":{"kind":"checks","request_id":"preserved"}}', 0))
    packet = update_request(config, '0' * 64 if condition == 'stale' else created['revision'],
                            manifest_edits=[{'path': ['git', 'base_ref'], 'value': 'corrected-branch'}])
    before = snapshot(project['root'].parent)
    with pytest.raises(PoiseError):
        update_tools(settings).apply(packet)
    # The existing config owner may hold a lock file; test persistent input/data
    # content separately from that existing operation's transient lock metadata.
    after = snapshot(project['root'].parent)
    assert {p: (v[0], v[4]) for p, v in after.items() if not p.endswith('.lock')} == {
        p: (v[0], v[4]) for p, v in before.items() if not p.endswith('.lock')}
    assert json.loads(config.read_text())['git']['base_ref'] == 'main'
