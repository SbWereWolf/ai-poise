"""Public relocation/replanning with test-owned immutable-history oracles."""
from copy import deepcopy
import json
from pathlib import Path
import sqlite3

import pytest

from batch.helpers import configure, request
from conftest import WorkPoise, seed_fixture_requirements, git
from poise.application.work import WorkTools
from poise.common import load_config
from poise.modules.foundation.errors import PoiseError
from projects.test_update import (
    installed_project, project_revision, relocation, update_request, update_tools,
)
from sprints.helpers import draft, publish, setup, task
from sprints.test_terminal_integration import deliver_member, set_execution, snapshot
from sprints.test_unpublish import (
    git_state, intent, invoke, prepare_member,
)


@pytest.fixture
def delivered(project, tmp_path):
    project = deepcopy(project)
    setup(project)
    project['cfg']['workflows'].pop('documentation')
    project['cfg']['paths']['worktrees'] = str(project['app'] / 'task-worktrees')
    project['cfg']['paths']['requirements_database'] = str(tmp_path / 'requirements.sqlite')
    project['cfg']['paths']['requirements_lock'] = str(tmp_path / 'requirements.lock')
    project['cfg']['runtime_resources'] = {
        'project_setup': str(project['root'] / 'setup.json'), 'hooks': [],
    }
    # Test-owned Git arrangement precedes installation and Task allocation.
    exclude = project['app'] / '.git/info/exclude'
    exclude.write_text(exclude.read_text() + '\n/task-worktrees/\n')
    configure(project)
    settings, config_path, _ = installed_project(project)
    root, cfg, workflows = load_config(config_path)
    project.update(root=root, cfg=cfg, config_path=config_path,
                   workflow=workflows['development'],
                   requirements_registry=seed_fixture_requirements(root, cfg))
    planner = WorkTools(WorkPoise(config_path, 'planner'))
    born = draft(planner, [task(project, 'A'), task(project, 'B')], [
        {'predecessor': 'A', 'successor': 'B', 'kind': 'completion'},
    ])
    published = publish(planner, born['revision'])
    context, receipt = deliver_member(project)
    worker = WorkTools(WorkPoise(config_path, 'unfinished-B'))
    current = worker.invoke(request('bootstrap', {
        'task': {'id': 'B'}, 'decision': None, 'feedback': None, 'rework_stage': None}))
    unfinished = Path(current['worktree']) / 'src/unfinished.note'
    unfinished.write_bytes(b'staged unfinished material\n')
    git(Path(current['worktree']), 'add', '--', 'src/unfinished.note')
    unfinished.write_bytes(b'unstaged unfinished material\x00\xff\n')
    project['unfinished_worktree'] = current['worktree']
    worker.invoke(request('artifacts', {'items': [{
        'scope': 'task', 'path': 'unfinished-note.txt',
        'source': {'kind': 'text', 'text': 'unfinished B material\n'},
    }]}))
    from runtime_services.test_participant_continuity import release_packet
    assert worker.invoke(release_packet(worker))['status'] == 'ownership_released'
    assert not Path(context['task_root']).exists()
    assert Path(current['worktree']).is_dir()
    return project, settings, planner, published, receipt


def immutable(planner):
    """Read exact persisted history, without a product serializer as oracle."""
    database = planner.runtime.state / planner.runtime.paths['database']
    queries = {
        'tasks': 'SELECT id,status,stage_index,iteration,claimed_by,version,metadata FROM tasks ORDER BY id',
        'execution': 'SELECT task_id,data,version FROM task_execution ORDER BY task_id',
        'submissions': 'SELECT seq,task_id,stage,iteration,digest,data FROM submissions ORDER BY seq',
        'results': 'SELECT task_id,submission_id,data FROM task_results ORDER BY task_id,submission_id',
        'evidence': 'SELECT id,task_id,stage,iteration,data FROM evidence ORDER BY id',
        'events': 'SELECT seq,task_id,version,at,data FROM task_events ORDER BY seq',
        'members': 'SELECT sprint_id,task_id FROM sprint_members ORDER BY sprint_id,task_id',
        'dependencies': 'SELECT sprint_id,predecessor,successor,kind FROM sprint_dependencies ORDER BY sprint_id,predecessor,successor,kind',
    }
    with sqlite3.connect(database.as_uri() + '?mode=ro', uri=True) as db:
        db.execute('PRAGMA query_only=ON')
        db.execute('BEGIN')
        return {name: [tuple(row) for row in db.execute(sql)]
                for name, sql in queries.items()}


def materials(planner):
    database = planner.runtime.state / planner.runtime.paths['database']
    with sqlite3.connect(database.as_uri() + '?mode=ro', uri=True) as db:
        records = db.execute('SELECT id,owner,scope,path,digest FROM artifacts ORDER BY id').fetchall()
    return {row[0]: (*row[1:], Path(row[3]).read_bytes()) for row in records}


def move(project, settings, planner, destination, number):
    source = planner.runtime.state
    proposal = relocation(project['config_path'], destination)
    proposal.update(schema='project-state-relocation-1', hook_destinations=[])
    packet = update_request(project['config_path'], project_revision(project['config_path']),
                            request_id=f'terminal-relocation-{number}', state_relocation=proposal)
    packet['receipt_path'] = f'operations/terminal-relocation-{number}.json'
    result = update_tools(settings).apply(packet)
    assert result['state_relocation']['status'] == 'completed'
    assert not source.exists()
    fresh = WorkTools(WorkPoise(project['config_path'], 'planner'))
    assert fresh.runtime.state == destination
    return fresh


def test_relocated_terminal_delivery_preserves_history_and_replay(delivered, tmp_path):
    project, settings, planner, published, receipt = delivered
    original = immutable(planner)
    original_state = planner.runtime.state
    proof = materials(planner)
    assert proof, 'The unfinished member must retain its actual registered material'
    assert {item[0] for item in proof.values()} == {'B'}
    repository = git_state(project['app'])
    unfinished = Path(project['unfinished_worktree'])
    working_material = ((unfinished / 'src/unfinished.note').read_bytes(), git_state(unfinished))
    revision = published['revision']
    for number in (1, 2):
        planner = move(project, settings, planner, tmp_path / f'relocated-state-{number}', number)
        assert immutable(planner) == original
        assert git_state(project['app']) == repository
        assert ((unfinished / 'src/unfinished.note').read_bytes(), git_state(unfinished)) == working_material
        expected = {}
        for identifier, (owner, scope, path, digest, content) in proof.items():
            prior = Path(path)
            moved = (planner.runtime.state / prior.relative_to(original_state)
                     if prior.is_relative_to(original_state) else prior)
            expected[identifier] = (owner, scope, str(moved), digest, content)
        assert materials(planner) == expected
        packet = intent(revision, request_id=f'terminal-relocated-unpublish-{number}')
        try:
            reopened = invoke(planner, packet)
        except PoiseError as exc:
            if str(exc) == 'Member has an unresolved pending external publication: A':
                print('EXPECTED_RED: relocated terminal integration rejected')
                pytest.fail('Confirmed delivery survives an admitted state relocation.', pytrace=False)
            raise
        assert reopened['sprint'] == 'S'
        assert reopened['status'] == 'draft'
        assert reopened['revision'] == revision + 1
        assert immutable(planner) == original
        after = snapshot(planner)
        assert invoke(planner, packet) == reopened
        assert snapshot(planner) == after
        assert materials(planner) == expected
        assert git_state(project['app']) == repository
        if number == 1:
            republished = publish(planner, reopened['revision'], 'terminal-relocated-publish-1')
            assert republished['revision'] == reopened['revision'] + 1
            revision = republished['revision']
    execution = next(row[1] for row in original['execution'] if row[0] == 'A')
    assert json.loads(execution)['pending'] == receipt


def test_relocated_terminal_ignores_inert_foreign_backup_reference(delivered, tmp_path):
    project, settings, planner, published, receipt = delivered
    original_runtime = planner.runtime.state / planner.runtime.paths['runtime']
    suffix = Path(receipt['temporary_backup_directory']).relative_to(original_runtime)
    planner = move(project, settings, planner, tmp_path / 'relocated-state', 1)
    foreign = deepcopy(receipt)
    foreign['temporary_backup_directory'] = str(tmp_path / 'foreign-runtime' / suffix)
    set_execution(planner, 'A', foreign)
    before = immutable(planner)
    repository = git_state(project['app'])
    reopened = invoke(planner, intent(published['revision']))
    assert reopened['status'] == 'draft' and reopened['revision'] == published['revision'] + 1
    assert immutable(planner) == before
    assert git_state(project['app']) == repository
