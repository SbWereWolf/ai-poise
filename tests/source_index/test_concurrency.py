"""Different real dispatcher-independent clients and a shared on-disk index."""
import json
import subprocess
import time

from tests.conftest import git
from .helpers import FIXTURES, Scene, literal, wait_file


def blocked_update(scene, operation='update', arguments=()):
    barrier = scene.parent / 'updater'
    scene.controls.update(barrier=str(barrier), barrier_checkout=str(scene.checkout))
    scene.save()
    child = scene.start(operation, arguments, label='updater')
    wait_file(barrier.with_suffix('.reached'), child[0])
    return barrier, child


def release(barrier):
    barrier.with_suffix('.release').write_text('release the actual peer')


def test_query_waits_for_other_process_update(project, tmp_path):
    scene = Scene(project, tmp_path); scene.require(); scene.ready()
    barrier, updater = blocked_update(scene)
    query = scene.start('query', ['usages', 'net_total', '--format', 'json'], label='query')
    try:
        wait_file(scene.parent / 'query.lock-wait', query[0])
        assert query[0].pid != updater[0].pid
        assert query[0].poll() is None
        rows = scene.records()
        assert sum(row['operation'] == 'update' and row['phase'] == 'entered' for row in rows) == 1
        assert not any(row['operation'] == 'usages' for row in rows)
    finally:
        release(barrier)
    assert scene.finish(updater)[0] == 0
    code, result = scene.finish(query)
    assert (code, result['status']) == (0, 'complete')
    rows = scene.records()
    updates = [row for row in rows if row['operation'] == 'update' and row['phase'] == 'entered']
    assert len(updates) == 2
    assert {row['parent_pid'] for row in updates} == {updater[0].pid, query[0].pid}
    first_finished = next(i for i, row in enumerate(rows)
                          if row['operation'] == 'update' and row['phase'] == 'finished')
    query_entered = next(i for i, row in enumerate(rows) if row['operation'] == 'usages')
    assert first_finished < query_entered
    assert all(row['database'] == str(scene.database) for row in rows)


def test_busy_refusal_is_bounded_and_actionable(project, tmp_path):
    scene = Scene(project, tmp_path); scene.require(); scene.ready()
    barrier, updater = blocked_update(scene)
    scene.policy_data['limits']['lock_seconds'] = 0.15; scene.save()
    try:
        code, result = scene.query()
        assert (code, result['status']) == (1, 'busy')
        assert result['reason'] and result['recovery']
        assert not any(row['operation'] == 'usages' for row in scene.records())
    finally:
        release(barrier)
    assert scene.finish(updater)[0] == 0
    scene.policy_data['limits']['lock_seconds'] = 5.0; scene.save()
    code, result = scene.query()
    assert (code, result['status']) == (0, 'complete')


def test_independent_worktree_query_is_not_blocked(project, tmp_path):
    scene = Scene(project, tmp_path); scene.require(); scene.ready()
    sibling = scene.sibling()
    code, _ = scene.invoke('rebuild', checkout=sibling); assert code == 0
    scene.controls['query_by_checkout'] = {str(sibling): str(FIXTURES / 'query-beta.json')}
    scene.save()
    barrier, updater = blocked_update(scene)
    other = scene.start('query', ['usages', 'net_total', '--format', 'json'],
                        checkout=sibling, label='independent')
    try:
        code, result = scene.finish(other)
        assert (code, result['status']) == (0, 'complete')
        assert result['database'] == str(sibling / '.runtime/ast-index/index.db')
        assert json.loads(scene.output(result)) == literal('query-beta.json')
        assert updater[0].poll() is None
        assert not (scene.parent / 'independent.lock-wait').exists()
        assert any(row['operation'] == 'usages' and row['cwd'] == str(sibling)
                   for row in scene.records())
    finally:
        release(barrier)
    assert scene.finish(updater)[0] == 0


def test_observed_head_drift_is_inconclusive(project, tmp_path):
    scene = Scene(project, tmp_path); scene.require(); scene.ready(); scene.hooks()
    before = git(scene.checkout, 'rev-parse', 'HEAD')
    barrier, query = blocked_update(scene, 'query', ['usages', 'net_total', '--format', 'json'])
    (scene.checkout / 'src/late.py').write_text('def saved_later(): return 13\n')
    git(scene.checkout, 'add', 'src/late.py')
    with (scene.parent / 'drift-git.stdout').open('w') as out, (scene.parent / 'drift-git.stderr').open('w') as err:
        commit = subprocess.Popen(['git', 'commit', '-m', 'Advance source during index update'],
                                  cwd=scene.checkout, env=scene.environment, stdout=out,
                                  stderr=err, start_new_session=True)
    scene.peers.append(commit)
    try:
        deadline = time.monotonic() + 10
        while git(scene.checkout, 'rev-parse', 'HEAD') == before:
            assert commit.poll() is None
            assert time.monotonic() < deadline, 'Commit did not reach its new real HEAD'
            time.sleep(0.01)
    finally:
        release(barrier)
    code, result = scene.finish(query)
    assert (code, result['status']) == (1, 'inconclusive')
    assert result['recovery']
    commit.wait(timeout=20); assert commit.returncode == 0
    assert git(scene.checkout, 'rev-parse', 'HEAD') != before
    code, result = scene.query()
    assert (code, result['status']) == (0, 'complete')
