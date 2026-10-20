"""Permanent Workspace context through actual CLI, Git and independent SQL oracles."""
from contextlib import closing
from copy import deepcopy
import json
import os
from pathlib import Path
import shlex
import shutil
import signal
import sqlite3
import subprocess
import sys
import time

import pytest

from apps.sequencing.tests.test_registration import Client, current_state

ROOT = Path(__file__).resolve().parents[3]
DATA = json.loads((ROOT / 'apps/sequencing/fixtures/workspace-contract.json').read_text())
A, B, D = DATA['task_ids']
OWNER, OTHER = 'SESSION-A', 'SESSION-B'


def render(value, tokens):
    if isinstance(value, str):
        return deepcopy(tokens.get(value, value))
    if isinstance(value, list):
        return [render(item, tokens) for item in value]
    if isinstance(value, dict):
        return {tokens.get(key, key): render(item, tokens) for key, item in value.items()}
    return value


def assert_exact(actual, expected):
    assert actual == expected


class Work:
    def __init__(self, root, reference='statement', form='independent', occupied=False):
        self.root = root
        root.mkdir(parents=True, exist_ok=True)
        self.repo, self.trees, self.aux = root / 'repo', root / 'trees', root / 'aux'
        self.repo.mkdir()
        self.trees.mkdir()
        self.real_git = shutil.which('git')
        assert self.real_git
        self.git(self.repo, 'init', '-q', '--initial-branch=sprint')
        self.git(self.repo, 'config', 'user.name', 'Workspace fixture')
        self.git(self.repo, 'config', 'user.email', 'workspace@example.test')
        self.git(self.repo, 'config', 'gc.auto', '0')
        self.git(self.repo, 'config', 'core.autocrlf', 'false')
        self.git(self.repo, 'config', 'core.hooksPath', '/dev/null')
        (self.repo / 'payload.txt').write_text(DATA['payload'])
        self.commit_source('Initial fixture')
        self.base = self.git(self.repo, 'rev-parse', 'HEAD')
        self.client = Client(root / 'client')
        self.database = self.client.database
        cfg = json.loads(self.client.config.read_text())
        cfg['repository'] = {'id': 'fixture-repo', 'path': str(self.repo), 'base_ref': 'refs/heads/sprint'}
        cfg['worktree']['root'] = str(self.trees)
        cfg['worktree']['branch_prefix'] = 'tasks/'
        cfg['transfer']['root'] = str(root / 'transfer')
        cfg['identifiers']['task']['pattern'] = '^[0-9]{4}$'
        self.client.config.write_text(json.dumps(cfg))
        self.config_bytes = self.client.config.read_bytes()
        self.reference, self.form = 'requirements_' + reference + '_revision', form
        self.owner, self.claim_id, self.sequence = OWNER, None, 0
        names = {'$CODE1': 'z code', '$CODE2': 'a-code', '$MAIN1': 'z-main',
                 '$MAIN2': 'a-main', '$ARTIFACT1': 'z-artifact', '$ARTIFACT2': 'a-artifact'}
        self.tokens = {}
        for token, name in names.items():
            path = self.aux / name
            path.mkdir(parents=True)
            (path / 'sentinel.txt').write_text(DATA['marker'])
            self.tokens[token] = str(path)
        self.roots = render(DATA['input_roots'], self.tokens)
        self.boundary = root / 'boundary.json'
        self.boundary_values = {'mode': 'none', 'real_git': self.real_git, 'target': str(self.target),
            **{key: str(root / key) for key in ('trace', 'used', 'entered', 'release',
                                               'filter_entered', 'filter_release')}}
        self.boundary.write_text(json.dumps(self.boundary_values))
        proxy = root / 'bin' / 'git'
        proxy.parent.mkdir()
        driver = ROOT / 'apps/sequencing/tests/workspace_git_boundary.py'
        proxy.write_text('#!' + sys.executable + '\nimport runpy\nrunpy.run_path(' +
                         repr(str(driver)) + ', run_name="__main__")\n')
        proxy.chmod(0o755)
        self.environment = dict(os.environ, PYTHONDONTWRITEBYTECODE='1',
            STRIDE_TEST_GIT_BOUNDARY=str(self.boundary),
            PATH=str(proxy.parent) + os.pathsep + os.environ['PATH'])
        operations = [{'id': 'register-' + task, 'kind': 'register_task', 'task_id': task,
                       self.reference: 2} for task in DATA['task_ids']]
        if form == 'sprint':
            operations.append({'id': 'group', 'kind': 'register_sprint', 'sprint_id': 'SPR-WORKSPACE',
                               'members': [A, B], 'dependencies': []})
        self.apply(operations, revision=0)
        self.claim_id = self.claim(A, OWNER)
        if occupied:
            primary = self.aux / 'foreign-context'
            primary.mkdir()
            (primary / 'uncommitted.txt').write_text(DATA['marker'])
            self.seed_workspace('WS-D', D, 'ready', primary, binding=OWNER, read_only=True)

    @property
    def target(self):
        return self.trees / A

    @property
    def revision(self):
        return self.rows('SELECT value FROM meta WHERE key=?', ('revision',))[0][0]

    def rows(self, sql, values=()):
        with closing(sqlite3.connect(self.database)) as db:
            return db.execute(sql, values).fetchall()

    def write(self, sql, values=()):
        with closing(sqlite3.connect(self.database)) as db:
            db.execute('PRAGMA foreign_keys=ON')
            db.execute(sql, values)
            db.commit()
            assert db.execute('PRAGMA foreign_key_check').fetchall() == []

    def git(self, path, *arguments, exit_code=0):
        proc = subprocess.run([self.real_git, '-C', str(path), *arguments],
                              text=True, capture_output=True)
        assert proc.returncode == exit_code, (proc.args, proc.stdout, proc.stderr)
        return proc.stdout.strip()

    def commit_source(self, message):
        self.git(self.repo, 'add', '--all')
        self.git(self.repo, '-c', 'core.hooksPath=/dev/null', '-c', 'commit.gpgsign=false',
                 'commit', '-qm', message)

    def argv(self):
        return [sys.executable, '-B', str(ROOT / 'apps/sequencing/bin/sequencing'),
                '--config', str(self.client.config)]

    def invoke(self, packet, exit_code=0):
        process = subprocess.run(self.argv(), input=json.dumps(packet) + '\n',
            text=True, capture_output=True, env=self.environment, cwd=self.client.root, timeout=30)
        assert process.returncode == exit_code, (process.stdout, process.stderr)
        assert process.stderr == ''
        return json.loads(process.stdout)

    def apply_packet(self, operations, request=None, revision=None):
        self.sequence += 1
        return {'schema': DATA['schema'], 'operation': 'apply', 'input': {
            'request_id': request or 'apply-' + str(self.sequence),
            'expected_revision': self.revision if revision is None else revision,
            'operations': operations}}

    def apply(self, operations, request=None, revision=None):
        response = self.invoke(self.apply_packet(operations, request, revision))
        assert response['status'] == 'applied' and response['replayed'] is False
        return response['results']

    def claim(self, task, owner):
        self.sequence += 1
        value = self.apply([{'id': 'claim', 'kind': 'claim_task', 'task_id': task,
            'claimant': owner, 'claim_request_id': 'claim-intent-' + str(self.sequence)}])[0]
        assert isinstance(value['claim_id'], str) and value['claim_id']
        return value['claim_id']

    def packet(self, request=None, read_only=False, empty=False):
        self.sequence += 1
        return render(DATA['prepare'], {'$REQUEST': request or 'prepare-' + str(self.sequence),
            '$CLAIM': self.claim_id, '$OWNER': self.owner, '$READ_ONLY': read_only,
            '$ROOTS': {'code': [], 'main': [], 'artifact': []} if empty else self.roots})

    def task(self):
        response = self.invoke({'schema': DATA['schema'], 'operation': 'query', 'input': {
            'queries': [{'id': 'task', 'kind': 'task', 'task_id': A}]}})
        assert set(response) == {'schema', 'status', 'revision', 'results'}
        assert response['schema'] == DATA['schema'] and response['status'] == 'read_only'
        assert response['revision'] == self.revision
        assert len(response['results']) == 1 and response['results'][0]['id'] == 'task'
        return response['results'][0]['value']

    def root_values(self, primary, empty=False):
        return render(DATA['root_templates']['empty' if empty else 'full'],
                      dict(self.tokens, **{'$PRIMARY': str(primary)}))

    def expected_workspace(self, identifier, primary, read_only=False, empty=False, binding=None):
        return render(DATA['workspace'], {'$WORKSPACE': identifier, '$BASE': self.base,
            '$BRANCH': '' if read_only else 'tasks/' + A, '$READ_ONLY': read_only,
            '$EXPECTED_ROOTS': self.root_values(primary, empty), '$PRIMARY': str(primary),
            '$BINDING': binding})

    def assert_response(self, response, revision, read_only=False, empty=False, identifier=None,
                        primary=None, binding=None):
        actual_id = response['workspace']['workspace_id']
        assert isinstance(actual_id, str) and actual_id
        if identifier is not None:
            assert actual_id == identifier
        primary = primary or (self.repo if read_only else self.target)
        expected = self.expected_workspace(actual_id, primary, read_only, empty, binding)
        assert_exact(response, render(DATA['response'], {'$REVISION': revision,
                                                       '$WORKSPACE_VALUE': expected}))
        expected_task = render(DATA['task'], {'$REFERENCE': self.reference, '$CLAIM': self.claim_id,
            '$OWNER': self.owner, '$SPRINT': 'SPR-WORKSPACE' if self.form == 'sprint' else None,
            '$WORKSPACE_VALUE': expected})
        assert_exact(self.task(), expected_task)
        return actual_id

    def snapshot(self):
        files = {}
        for root in (self.repo, self.trees, self.aux):
            for path in root.rglob('*'):
                if '.git' not in path.parts and path.is_file():
                    files[str(path.relative_to(self.root))] = path.read_bytes()
        return {'database': current_state(self.database), 'files': files,
                'refs': self.git(self.repo, 'for-each-ref', '--format=%(refname) %(objectname)'),
                'trees': self.git(self.repo, 'worktree', 'list', '--porcelain'),
                'config': self.client.config.read_bytes()}

    def assert_preserved(self, before, request, workspace_id):
        after = self.snapshot()
        old, new = before['database'], after['database']
        assert old['schema'] == new['schema'] and old['version'] == new['version'] == 2
        for table in ('tasks', 'task_claims', 'workspace_bindings', 'sprints', 'sprint_revisions',
                      'sprint_members', 'sprint_dependencies'):
            assert new['rows'][table] == old['rows'][table], table
        for row in old['rows']['workspaces']:
            if row[0] != workspace_id:
                assert row in new['rows']['workspaces']
        assert all(row in new['rows']['workspace_roots'] for row in old['rows']['workspace_roots'])
        assert all(row in new['rows']['sequencing_history'] for row in old['rows']['sequencing_history'])
        assert [row for row in old['rows']['meta'] if row[0] != 'revision'] == [
            row for row in new['rows']['meta'] if row[0] != 'revision']
        for row in old['rows']['requests']:
            if row[0] != request:
                assert row in new['rows']['requests']
        old_refs = dict(line.split(' ', 1) for line in before['refs'].splitlines())
        new_refs = dict(line.split(' ', 1) for line in after['refs'].splitlines())
        assert all(new_refs.get(ref) == commit for ref, commit in old_refs.items())
        assert before['config'] == after['config'] == self.config_bytes
        assert all(after['files'].get(path) == value for path, value in before['files'].items())
        assert self.rows('SELECT task_id,state FROM tasks WHERE task_id=?', (D,)) == [(D, 'available')]

    def reject(self, packet, reason=None):
        before = self.snapshot()
        response = self.invoke(packet, 2)
        assert set(response) == {'schema', 'status', 'reason'}
        assert response['schema'] == DATA['schema'] and response['status'] == 'rejected'
        assert isinstance(response['reason'], str) and response['reason']
        if reason is not None:
            assert response['reason'].split(':', 1)[0] == reason
        assert self.snapshot() == before
        return response

    def seed_workspace(self, identifier, task, state, primary, binding=None, read_only=False,
                       empty=False, legacy_event=True):
        self.write('INSERT INTO workspaces(workspace_id,task_id,repository_id,base_commit,branch,status,read_only) '
                   'VALUES(?,?,?,?,?,?,?)', (identifier, task, 'fixture-repo', self.base,
                    '' if read_only else 'tasks/' + task, state, int(read_only)))
        for root in self.root_values(primary, empty):
            self.write('INSERT INTO workspace_roots(workspace_id,kind,position,path) VALUES(?,?,?,?)',
                       (identifier, root['kind'], root['position'], root['path']))
        if binding is not None:
            self.write('INSERT INTO workspace_bindings(workspace_id,session_id) VALUES(?,?)',
                       (identifier, binding))
        if legacy_event:
            self.write('INSERT INTO sequencing_history(task_id,sprint_id,workspace_id,actor,event,data,at) '
                       'VALUES(?,?,?,?,?,?,?)', (task, None, identifier, 'legacy-owner', 'workspace.retained',
                                                '{"retained":true}', '2026-10-01T00:00:00Z'))

    def mode(self, name):
        self.boundary_values['mode'] = name
        self.boundary.write_text(json.dumps(self.boundary_values))

    def spawn(self, packet, mode='none'):
        self.mode(mode)
        process = subprocess.Popen(self.argv(), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, env=self.environment, cwd=self.client.root,
            start_new_session=True)
        process.stdin.write(json.dumps(packet) + '\n')
        process.stdin.close()
        process.stdin = None
        return process

    def wait(self, process, marker='entered'):
        limit = time.monotonic() + 20
        path = Path(self.boundary_values[marker])
        while not path.exists():
            if process.poll() is not None:
                out, err = process.communicate()
                pytest.fail('Prepare exited before the real Git boundary: ' +
                            repr((process.returncode, out, err)))
            assert time.monotonic() < limit, 'Git fixture did not reach its declared boundary'
            time.sleep(0.01)

    @staticmethod
    def stop(process):
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        return process.communicate(timeout=30)

    def pending(self):
        value = self.task()['workspace']
        assert value is not None and value['status'] == 'reserved'
        assert value['base_commit'] == self.base and value['roots'] == self.root_values(self.target)
        return value['workspace_id']

    def adds(self):
        path = Path(self.boundary_values['trace'])
        calls = [] if not path.exists() else [json.loads(line) for line in path.read_text().splitlines()]
        return [args for args in calls if any(args[index:index + 2] == ['worktree', 'add']
                                             for index in range(len(args) - 1))]


@pytest.mark.parametrize('form', ['independent', 'sprint'])
@pytest.mark.parametrize('reference', ['statement', 'snapshot'])
@pytest.mark.parametrize('read_only', [False, True])
def test_prepare_context_persists_across_claims(tmp_path, form, reference, read_only):
    work = Work(tmp_path, reference, form, occupied=True)
    if read_only:
        (work.repo / 'uncommitted.txt').write_text(DATA['marker'])
    packet = work.packet(read_only=read_only)
    before = work.snapshot()
    revision = work.revision
    response = work.invoke(packet)
    identity = work.assert_response(response, revision + (1 if read_only else 2), read_only)
    assert identity != 'WS-D'
    work.assert_preserved(before, packet['input']['request_id'], identity)
    primary = work.repo if read_only else work.target
    assert work.git(primary, 'rev-parse', 'HEAD') == work.base
    assert (primary / 'payload.txt').read_text() == DATA['payload']
    assert len(work.adds()) == (0 if read_only else 1)
    (primary / 'uncommitted.txt').write_text(DATA['marker'])
    if not read_only:
        (primary / 'payload.txt').write_text('Local uncommitted edit\n')
    work.apply([{'id': 'release', 'kind': 'release_claim', 'task_id': A,
                 'claim_id': work.claim_id, 'claimant': OWNER, 'reason': 'Передать работу'}])
    handoff = work.rows('SELECT data FROM sequencing_history WHERE task_id=? AND event=? ORDER BY seq',
                        (A, 'claim.released'))[-1][0]
    handoff_id = json.loads(handoff)['handoff']['handoff_id']
    before_replay = work.snapshot()
    assert_exact(work.invoke(packet), {**response, 'replayed': True})
    assert work.snapshot() == before_replay
    resumed = work.apply([{'id': 'resume', 'kind': 'resume_claim', 'task_id': A,
                          'claimant': OTHER, 'handoff_id': handoff_id}])[0]
    assert resumed['claim_id'] != work.claim_id
    work.claim_id, work.owner = resumed['claim_id'], OTHER
    next_packet = work.packet(read_only=read_only)
    before = work.snapshot()
    current_revision = work.revision
    result = work.invoke(next_packet)
    work.assert_response(result, current_revision + 1, read_only, identifier=identity)
    work.assert_preserved(before, next_packet['input']['request_id'], identity)
    assert (primary / 'uncommitted.txt').read_text() == DATA['marker']
    if not read_only:
        assert (primary / 'payload.txt').read_text() == 'Local uncommitted edit\n'
    assert len(work.adds()) == (0 if read_only else 1)
    assert work.rows('SELECT workspace_id,session_id FROM workspace_bindings') == [('WS-D', OWNER)]
    assert work.rows('SELECT workspace_id,task_id FROM workspaces WHERE task_id=?', (A,)) == [(identity, A)]
    before_replay = work.snapshot()
    assert_exact(work.invoke(packet), {**response, 'replayed': True})
    assert work.snapshot() == before_replay


@pytest.mark.parametrize('read_only', [False, True])
def test_empty_auxiliary_roots_keep_only_intrinsic_code_root(tmp_path, read_only):
    work = Work(tmp_path)
    before = work.snapshot()
    packet = work.packet(read_only=read_only, empty=True)
    revision = work.revision
    response = work.invoke(packet)
    identity = work.assert_response(response, revision + (1 if read_only else 2), read_only, empty=True)
    work.assert_preserved(before, packet['input']['request_id'], identity)
    assert response['workspace']['roots'] == work.root_values(work.repo if read_only else work.target, True)
    assert len(work.adds()) == (0 if read_only else 1)
    assert work.git(work.repo, 'for-each-ref', '--format=%(refname)').splitlines() == (
        ['refs/heads/sprint'] if read_only else ['refs/heads/sprint', 'refs/heads/tasks/' + A])


@pytest.mark.parametrize('state', DATA['legacy_states'])
def test_saved_workspace_state_has_explicit_outcome(tmp_path, state):
    work = Work(tmp_path)
    primary = work.target
    primary.mkdir()
    (primary / 'uncommitted.txt').write_text(DATA['marker'])
    work.seed_workspace('WS-A', A, state, primary, binding=OTHER)
    if state == 'released':
        assert work.task()['workspace'] is None
    work.reject(work.packet(), 'workspace_' + state)
    assert work.rows('SELECT workspace_id,status FROM workspaces WHERE task_id=?', (A,)) == [('WS-A', state)]
    assert not work.adds()


def test_released_history_does_not_replace_ready_workspace(tmp_path):
    work = Work(tmp_path)
    old = work.aux / 'old-workspace'
    old.mkdir()
    (old / 'uncommitted.txt').write_text(DATA['marker'])
    work.seed_workspace('WS-OLD', A, 'released', old, binding=OTHER)
    work.git(work.repo, 'worktree', 'add', '-b', 'tasks/' + A, str(work.target), work.base)
    work.seed_workspace('WS-A', A, 'ready', work.target)
    before = work.snapshot()
    packet = work.packet()
    revision = work.revision
    response = work.invoke(packet)
    work.assert_response(response, revision + 1, identifier='WS-A')
    work.assert_preserved(before, packet['input']['request_id'], 'WS-A')
    assert work.rows('SELECT workspace_id,status FROM workspaces WHERE task_id=? ORDER BY workspace_id',
                     (A,)) == [('WS-A', 'ready'), ('WS-OLD', 'released')]
    assert work.git(work.target, 'rev-parse', 'HEAD') == work.base
    assert not work.adds()


@pytest.mark.parametrize('kind', ['missing-intent', 'other-request'])
def test_reserved_workspace_requires_original_intent(tmp_path, kind):
    work = Work(tmp_path)
    packet = work.packet()
    if kind == 'missing-intent':
        work.seed_workspace('WS-A', A, 'reserved', work.target)
        identity = 'WS-A'
    else:
        process = work.spawn(packet, 'before')
        try:
            work.wait(process)
            identity = work.pending()
        finally:
            work.stop(process)
        packet = work.packet()
    work.reject(packet, 'workspace_pending')
    assert work.task()['workspace']['workspace_id'] == identity
    assert not work.git(work.repo, 'for-each-ref', '--format=%(refname)').endswith('tasks/' + A)


def test_compatible_legacy_ready_context_retains_foreign_binding_and_wip(tmp_path):
    work = Work(tmp_path)
    work.git(work.repo, 'worktree', 'add', '-b', 'tasks/' + A, str(work.target), work.base)
    (work.target / 'payload.txt').write_text('Retained tracked work\n')
    (work.target / 'uncommitted.txt').write_text(DATA['marker'])
    work.seed_workspace('WS-A', A, 'ready', work.target, binding=OTHER)
    foreign = work.aux / 'independently-owned-context'
    foreign.mkdir()
    (foreign / 'uncommitted.txt').write_text(DATA['marker'])
    work.seed_workspace('WS-B', B, 'ready', foreign, binding=OWNER, read_only=True)
    before = work.snapshot()
    revision = work.revision
    packet = work.packet()
    result = work.invoke(packet)
    work.assert_response(result, revision + 1, identifier='WS-A', binding=OTHER)
    work.assert_preserved(before, packet['input']['request_id'], 'WS-A')
    assert before['files'] == work.snapshot()['files']
    assert not work.adds()


@pytest.mark.parametrize('boundary', ['before', 'after'])
def test_retry_preserves_reserved_identity(tmp_path, boundary):
    work = Work(tmp_path)
    packet = work.packet()
    process = work.spawn(packet, boundary)
    try:
        work.wait(process)
        identity = work.pending()
    finally:
        work.stop(process)
    old_base = work.base
    if boundary == 'before':
        (work.repo / 'payload.txt').write_text(DATA['updated_payload'])
        work.commit_source('Advance configured source after reservation')
        new_source = work.git(work.repo, 'rev-parse', 'refs/heads/sprint')
        assert new_source != old_base
    else:
        (work.target / 'own-commit.txt').write_text('Completed local work\n')
        work.git(work.target, 'add', 'own-commit.txt')
        work.git(work.target, '-c', 'core.hooksPath=/dev/null', '-c', 'commit.gpgsign=false',
                 'commit', '-qm', 'Lawful Task commit after Git creation')
        new_task = work.git(work.target, 'rev-parse', 'HEAD')
        assert new_task != old_base
        (work.target / 'uncommitted.txt').write_text(DATA['marker'])
    before = work.snapshot()
    revision = work.revision
    response = work.invoke(packet)
    work.assert_response(response, revision + 1, identifier=identity)
    work.assert_preserved(before, packet['input']['request_id'], identity)
    assert response['workspace']['base_commit'] == old_base
    if boundary == 'before':
        assert work.git(work.target, 'rev-parse', 'HEAD') == old_base
        assert (work.target / 'payload.txt').read_text() == DATA['payload']
        assert work.git(work.repo, 'rev-parse', 'refs/heads/sprint') == new_source
        assert (work.repo / 'payload.txt').read_text() == DATA['updated_payload']
    else:
        assert work.git(work.target, 'rev-parse', 'HEAD') == new_task
        assert (work.target / 'uncommitted.txt').read_text() == DATA['marker']
    assert len(work.adds()) == 1
    state = work.snapshot()
    assert_exact(work.invoke(packet), {**response, 'replayed': True})
    assert work.snapshot() == state


def test_owned_branch_only_continues_without_reallocation(tmp_path):
    work = Work(tmp_path)
    work.mode('permission')
    packet = work.packet()
    response = work.invoke(packet, 2)
    assert set(response) == {'schema', 'status', 'reason'}
    assert response['schema'] == DATA['schema'] and response['status'] == 'rejected'
    assert response['reason']
    identity = work.pending()
    assert not work.target.exists()
    assert work.git(work.repo, 'rev-parse', 'refs/heads/tasks/' + A) == work.base
    assert str(work.target) not in work.git(work.repo, 'worktree', 'list', '--porcelain')
    before = work.snapshot()
    revision = work.revision
    result = work.invoke(packet)
    work.assert_response(result, revision + 1, identifier=identity)
    work.assert_preserved(before, packet['input']['request_id'], identity)
    assert work.git(work.target, 'rev-parse', 'HEAD') == work.base
    assert (work.target / 'payload.txt').read_text() == DATA['payload']
    assert len(work.adds()) == 2


def test_initializing_checkout_stays_pending_until_operator_finishes(tmp_path):
    work = Work(tmp_path)
    driver = ROOT / 'apps/sequencing/tests/workspace_git_boundary.py'
    smudge = shlex.quote(sys.executable) + ' ' + shlex.quote(str(driver)) + ' --smudge'
    work.git(work.repo, 'config', 'filter.waiting.smudge', smudge)
    work.git(work.repo, 'config', 'filter.waiting.clean', 'cat')
    work.git(work.repo, 'config', 'filter.waiting.required', 'true')
    (work.repo / '.gitattributes').write_text('payload.txt filter=waiting\n')
    work.commit_source('Define ordinary checkout filter')
    work.base = work.git(work.repo, 'rev-parse', 'HEAD')
    packet = work.packet()
    process = work.spawn(packet)
    try:
        work.wait(process, 'filter_entered')
        identity = work.pending()
    finally:
        work.stop(process)
    assert 'locked initializing' in work.git(work.repo, 'worktree', 'list', '--porcelain')
    assert not (work.target / 'payload.txt').exists()
    (work.target / 'uncommitted.txt').write_text(DATA['marker'])
    incomplete = work.reject(packet, 'workspace_checkout_incomplete')
    for fact in [str(work.target), 'tasks/' + A, work.base]:
        assert fact in incomplete['reason']
    assert work.task()['workspace']['workspace_id'] == identity
    assert work.task()['workspace']['status'] == 'reserved'
    # Explicit operator work on this known initial, stopped, single-file fixture.
    work.git(work.repo, 'config', 'filter.waiting.smudge', 'cat')
    lock_text = work.git(work.target, 'rev-parse', '--git-path', 'index.lock')
    index_lock = Path(lock_text)
    if not index_lock.is_absolute():
        index_lock = work.target / index_lock
    index_lock.unlink(missing_ok=True)
    work.git(work.target, 'read-tree', '--reset', work.base)
    checkout = subprocess.run([work.real_git, '-C', str(work.target), 'checkout-index', '--all'],
                              text=True, capture_output=True)
    assert checkout.returncode in (0, 1), (checkout.stdout, checkout.stderr)
    assert (work.target / 'payload.txt').read_text() == DATA['payload']
    assert (work.target / 'uncommitted.txt').read_text() == DATA['marker']
    work.git(work.repo, 'worktree', 'unlock', str(work.target))
    assert 'locked initializing' not in work.git(work.repo, 'worktree', 'list', '--porcelain')
    before = work.snapshot()
    revision = work.revision
    response = work.invoke(packet)
    work.assert_response(response, revision + 1, identifier=identity)
    work.assert_preserved(before, packet['input']['request_id'], identity)
    assert (work.target / 'uncommitted.txt').read_text() == DATA['marker']
    assert len(work.adds()) == 1


@pytest.mark.parametrize('kind', ['path', 'branch'])
def test_foreign_git_state_is_preserved(tmp_path, kind):
    work = Work(tmp_path)
    packet = work.packet()
    process = work.spawn(packet, 'before')
    try:
        work.wait(process)
        identity = work.pending()
    finally:
        work.stop(process)
    if kind == 'path':
        work.git(work.repo, 'worktree', 'add', '-b', 'foreign-owner', str(work.target), work.base)
        (work.target / 'uncommitted.txt').write_text(DATA['marker'])
    else:
        (work.repo / 'payload.txt').write_text(DATA['updated_payload'])
        work.commit_source('Another legitimate source revision')
        foreign = work.git(work.repo, 'rev-parse', 'HEAD')
        assert foreign != work.base
        work.git(work.repo, 'branch', 'tasks/' + A, foreign)
    work.reject(packet, 'workspace_conflict')
    assert work.task()['workspace']['workspace_id'] == identity
    assert work.task()['workspace']['status'] == 'reserved'


def test_pending_request_namespace_does_not_hold_database_during_git(tmp_path):
    work = Work(tmp_path)
    packet = work.packet(request='pending-X')
    process = work.spawn(packet, 'before')
    try:
        work.wait(process)
        identity = work.pending()
        claim_b = work.claim(B, OTHER)
        operations = [{'id': 'release-b', 'kind': 'release_claim', 'task_id': B,
                       'claim_id': claim_b, 'claimant': OTHER, 'reason': 'Передать работу B'}]
        conflict = work.apply_packet(operations, request='pending-X')
        work.reject(conflict, 'request_reuse_conflict')
        changed = deepcopy(packet)
        changed['input']['read_only'] = True
        work.reject(changed, 'request_reuse_conflict')
        # The exact same valid operation with a different request finishes before Git is released.
        work.apply(operations, request='different-Y')
        assert work.rows('SELECT task_id,state FROM tasks WHERE task_id=?', (B,)) == [(B, 'available')]
        assert process.poll() is None
        before = work.snapshot()
        revision = work.revision
        Path(work.boundary_values['release']).touch()
        out, err = process.communicate(timeout=30)
        assert process.returncode == 0 and err == '', (process.returncode, out, err)
        response = json.loads(out)
        work.assert_response(response, revision + 1, identifier=identity)
        work.assert_preserved(before, packet['input']['request_id'], identity)
        assert work.rows('SELECT task_id,state FROM tasks WHERE task_id=?', (B,)) == [(B, 'available')]
        state = work.snapshot()
        assert_exact(work.invoke(packet), {**response, 'replayed': True})
        assert work.snapshot() == state
    finally:
        work.stop(process)


def test_prepare_authority_and_packet_admission(tmp_path):
    work = Work(tmp_path)
    valid = work.packet()
    assert len(DATA['admission']) == 28
    for case in DATA['admission']:
        packet = deepcopy(valid)
        if 'remove' in case:
            packet['input'].pop(case['remove'])
        if 'set' in case:
            packet['input'].update(deepcopy(case['set']))
        if 'root_group' in case:
            packet['input']['roots'][case['root_group']] = deepcopy(case['value'])
        if 'root_entry' in case:
            packet['input']['roots'][case['root_entry']] = [deepcopy(case['value'])]
        work.reject(packet)
    for values, reason in [({'task_id': '0099'}, 'task_not_found'),
                           ({'claim_id': 'different-claim'}, 'claim_owner_mismatch'),
                           ({'claimant': OTHER}, 'claim_owner_mismatch')]:
        packet = deepcopy(valid)
        packet['input'].update(values)
        work.reject(packet, reason)
    revision = work.revision
    response = work.invoke(valid)
    work.assert_response(response, revision + 2)


def test_ready_context_intent_is_immutable(tmp_path):
    work = Work(tmp_path)
    original = work.packet()
    revision = work.revision
    response = work.invoke(original)
    identity = work.assert_response(response, revision + 2)
    for change in ['mode', 'roots']:
        packet = work.packet()
        if change == 'mode':
            packet['input']['read_only'] = True
        else:
            packet['input']['roots']['main'].reverse()
        work.reject(packet, 'workspace_conflict')
    changed = deepcopy(original)
    changed['input']['read_only'] = True
    work.reject(changed, 'request_reuse_conflict')
    before = work.snapshot()
    packet = work.packet()
    revision = work.revision
    response = work.invoke(packet)
    work.assert_response(response, revision + 1, identifier=identity)
    work.assert_preserved(before, packet['input']['request_id'], identity)
