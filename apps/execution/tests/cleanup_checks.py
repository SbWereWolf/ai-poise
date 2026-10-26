"""Test-owned cleanup packets, literal oracles and independent public boundaries."""
from copy import deepcopy
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

APP = Path(__file__).resolve().parents[1]
ROOT = APP.parents[1]
DATA = json.loads((APP / 'fixtures/cleanup-contract.json').read_text())
LOG = []


class MissingCleanup(Exception):
    """Only the declared old missing public operation establishes intended RED."""


def bind(value, mapping):
    if isinstance(value, dict):
        return {key: bind(item, mapping) for key, item in value.items()}
    if isinstance(value, list):
        return [bind(item, mapping) for item in value]
    return deepcopy(mapping.get(value, value)) if isinstance(value, str) else value


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def mark(name):
    LOG.append({'subcheck': name})


def invoke(config, packet):
    result = subprocess.run([sys.executable, '-B', str(APP / 'bin/execution'),
        '--config', str(config)], input=json.dumps(packet, ensure_ascii=False) + '\n',
        capture_output=True, text=True, cwd=config.parent,
        env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'))
    LOG.append({'boundary': 'execution', 'config': str(config), 'packet': deepcopy(packet),
                'exit_code': result.returncode, 'stdout': result.stdout, 'stderr': result.stderr})
    assert result.stderr == '', (result.returncode, result.stdout, result.stderr)
    assert result.stdout.endswith('\n') and result.stdout.count('\n') == 1, result.stdout
    value = json.loads(result.stdout)
    if packet['operation'] == 'cleanup' and result.returncode == 2 and value == DATA['missing']:
        raise MissingCleanup()
    return result.returncode, value


def physical(root, work):
    result = subprocess.run([sys.executable, '-B', str(APP / 'tests/cleanup_owner_fixture.py'),
        '--root', str(root)], input=json.dumps(work) + '\n', capture_output=True, text=True)
    LOG.append({'boundary': 'test-physical-owner', 'work': deepcopy(work),
                'exit_code': result.returncode, 'stdout': result.stdout, 'stderr': result.stderr})
    assert result.returncode == 0 and result.stderr == '', result.stderr
    return json.loads(result.stdout)


def git(root, *args):
    result = subprocess.run(['git', '-C', str(root), *args], capture_output=True, text=True)
    LOG.append({'boundary': 'git', 'root': str(root), 'argv': list(args),
                'exit_code': result.returncode, 'stdout': result.stdout, 'stderr': result.stderr})
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def rows(database):
    """Read explicit columns of every actual own table, including future additions."""
    with sqlite3.connect(database) as db:
        tables = db.execute("SELECT name FROM sqlite_master WHERE type='table' "
                            "AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall()
        result = {}
        for (table,) in tables:
            quoted = '"' + table.replace('"', '""') + '"'
            columns = [item[1] for item in db.execute('PRAGMA table_info(' + quoted + ')')]
            names = ','.join('"' + name.replace('"', '""') + '"' for name in columns)
            values = db.execute('SELECT ' + names + ' FROM ' + quoted).fetchall()
            result[table] = {'columns': columns, 'rows': sorted(values, key=repr)}
        return result


def revision(database):
    with sqlite3.connect(database) as db:
        return db.execute('SELECT value FROM meta WHERE key=?', ('revision',)).fetchone()[0]


def original_records(database):
    with sqlite3.connect(database) as db:
        return db.execute('SELECT seq,task_id,kind,record_id,record_revision,generation,data_json '
                          'FROM execution_records ORDER BY seq').fetchall()


def mutate(value, recipe):
    value = deepcopy(value)
    parent = value
    for key in recipe['path'][:-1]:
        parent = parent[key]
    key = recipe['path'][-1]
    if recipe.get('remove'):
        del parent[key]
    else:
        parent[key] = deepcopy(recipe['value'])
    return value


class CleanupSubject:
    def __init__(self, root, *, terminal=True):
        self.root = root
        root.mkdir(parents=True)
        self.config = root / 'execution.json'
        self.database = root / 'state/execution.sqlite'
        self.counter = 0
        write_json(self.config, DATA['config'])
        packet = bind(DATA['setup'], {'@CONTRACT': DATA['contract']})
        code, result = invoke(self.config, packet)
        assert code == 0 and result['status'] == 'applied', result
        self.contract_revision = result['results'][0]['contract_revision']
        if terminal:
            packet = bind(DATA['cancel'], self.mapping())
            code, result = invoke(self.config, packet)
            assert code == 0 and result['status'] == 'outcome_recorded', result
            assert result['fact']['kind'] == 'cancelled'
            assert result['fact']['task_id'] == '0042'
        self.origin_records = original_records(self.database)
        self.owner_root = root / 'physical-owner'
        repository = self.owner_root / 'repository'
        repository.mkdir(parents=True)
        git(repository, 'init', '--initial-branch=main')
        git(repository, 'config', 'user.name', 'Cleanup test owner')
        git(repository, 'config', 'user.email', 'cleanup@example.test')
        (repository / 'payload.txt').write_text('Existing task payload.\n')
        git(repository, 'add', 'payload.txt')
        git(repository, 'commit', '-m', 'Prepare independent cleanup fixture')
        self.commit = git(repository, 'rev-parse', 'HEAD')
        self.worktree = self.owner_root / 'worktree'
        git(repository, 'worktree', 'add', '-b', 'task-cleanup', str(self.worktree))
        self.temporary = self.owner_root / 'temporary.txt'
        self.backup = self.owner_root / 'temporary-backup.txt'
        self.temporary.write_text('Temporary owner data.\n')
        self.backup.write_text('Temporary owner recovery data.\n')
        self.foreign = root / 'foreign-marker.txt'
        self.foreign.write_text('Separate owner data.\n')
        write_json(self.owner_root / 'owner-state.json', {
            'attempts': {}, 'effects': {}, 'calls': [], 'fail_once': None, 'unknown': False})
        self.resources = bind(DATA['resources'], {
            '@WORKTREE': str(self.worktree), '@COMMIT': self.commit,
            '@TEMPORARY': str(self.temporary), '@BACKUP': str(self.backup)})
        self.disposition = {'kind': 'discard_authorized', 'expected_commit': self.commit}

    def identity(self):
        self.counter += 1
        return 'cleanup-call-' + str(self.counter)

    def mapping(self):
        return {'@REQUEST': self.identity(), '@REVISION': revision(self.database),
                '@CONTRACT_REVISION': self.contract_revision}

    def packet(self, kind, *, resources=None, disposition='selected', work=None, observation=None):
        mapping = self.mapping()
        mapping.update({'@RESOURCES': self.resources if resources is None else resources,
                        '@DISPOSITION': self.disposition if disposition == 'selected' else disposition})
        if work is not None:
            mapping.update({'@STEP': work['step_id'], '@ATTEMPT': work['attempt_id'],
                            '@OBSERVATION': observation})
        return bind(DATA[kind], mapping)

    def write(self, packet):
        code, result = invoke(self.config, packet)
        assert code == 0 and set(result) == set(DATA['fields']['response']), result
        assert result['schema'] == 'stride-execution/v1' and result['status'] == 'cleanup_recorded'
        assert result['replayed'] is False
        assert type(result['revision']) is int and result['revision'] == packet['input']['expected_revision'] + 1
        self.inspect(result['cleanup'])
        return result

    def prepare(self, **options):
        return self.write(self.packet('prepare', **options))['cleanup']

    def proceed(self, **options):
        return self.write(self.packet('continue', **options))['cleanup']

    def observe(self, work, observation):
        packet = self.packet('observe', work=work, observation=observation)
        return self.write(packet)['cleanup']

    def inspect(self, cleanup):
        assert set(cleanup) == set(DATA['fields']['cleanup']), cleanup
        assert cleanup['cleanup_id'] == 'cleanup-1' and cleanup['task_id'] == '0042'
        assert cleanup['generation'] == 1 and cleanup['outcome_id'] == 'outcome-cleanup'
        assert isinstance(cleanup['resources'], list) and isinstance(cleanup['removed'], list)
        assert isinstance(cleanup['history'], list) and cleanup['history']
        for event in cleanup['history']:
            assert set(event) == set(DATA['fields']['event']), event
        work = cleanup['work']
        if work is not None:
            assert set(work) == set(DATA['fields']['work']), work
            assert isinstance(work['step_id'], str) and work['step_id']
            assert isinstance(work['attempt_id'], str) and work['attempt_id']
            assert work['mode'] in ('execute', 'reconcile')
            assert work['owner'] == work['resource']['owner']

    def query(self):
        before = rows(self.database)
        code, result = invoke(self.config, DATA['query'])
        assert code == 0 and set(result) == set(DATA['fields']['query']), result
        assert result['status'] == 'read_only' and result['revision'] == revision(self.database)
        assert len(result['results']) == 1
        item = result['results'][0]
        assert set(item) == {'id', 'kind', 'result'}
        assert item['id'] == 'cleanup-read' and item['kind'] == 'cleanup'
        self.inspect(item['result'])
        assert rows(self.database) == before
        return item['result']

    def owner_state(self):
        return json.loads((self.owner_root / 'owner-state.json').read_text())

    def owner_setting(self, **values):
        state = self.owner_state()
        state.update(values)
        write_json(self.owner_root / 'owner-state.json', state)

    def effect_observation(self):
        branch = subprocess.run(['git', '-C', str(self.owner_root / 'repository'),
                                 'show-ref', '--verify', '--quiet', 'refs/heads/task-cleanup'])
        assert branch.returncode in (0, 1)
        return (self.worktree.exists(), branch.returncode == 0,
                self.temporary.exists(), self.backup.exists(), self.foreign.read_text())

    def refuse(self, packet, reason_any=()):
        before = rows(self.database), self.owner_state(), self.effect_observation()
        code, result = invoke(self.config, packet)
        assert code == 2 and set(result) == {'schema', 'status', 'reason'}, result
        assert result['schema'] == 'stride-execution/v1' and result['status'] == 'rejected'
        assert isinstance(result['reason'], str) and result['reason'].strip()
        if reason_any:
            assert any(word in result['reason'].lower() for word in reason_any), result
        after = rows(self.database), self.owner_state(), self.effect_observation()
        assert before == after
        LOG.append({'rejection_no_effect': True, 'before': before, 'after': after})
        return result

    def preserved_original_records(self):
        records = original_records(self.database)
        assert records[:len(self.origin_records)] == self.origin_records

    def drain(self, cleanup, expected_order):
        remaining = deepcopy(cleanup['resources'])
        removed = deepcopy(cleanup['removed'])
        operations = []
        for operation, resource_id in expected_order:
            work = cleanup['work']
            assert work is not None and work['mode'] == 'execute', cleanup
            assert (work['operation'], work['resource']['resource_id']) == (operation, resource_id)
            observed = physical(self.owner_root, work)
            assert observed['status'] == 'succeeded'
            cleanup = self.observe(work, observed)
            operations.append((operation, resource_id))
            if operation == 'remove':
                resource = next(value for value in remaining if value['resource_id'] == resource_id)
                remaining.remove(resource)
                removed.append(resource)
            assert cleanup['resources'] == remaining and cleanup['removed'] == removed
            assert cleanup['history'][-1]['observation'] == observed
            assert cleanup['history'][-1]['attempt_id'] == work['attempt_id']
        assert cleanup['status'] == 'cleanup_complete' and cleanup['work'] is None
        assert cleanup['resources'] == [] and cleanup['issue'] is None
        assert self.query() == cleanup
        self.preserved_original_records()
        return cleanup, operations


def caller_loss(config, owner_root, packet_path, position):
    """An actual caller process exits before ever sending observe."""
    packet = json.loads(packet_path.read_text())
    code, result = invoke(config, packet)
    assert code == 0
    write_json(owner_root / 'caller-boundary.json', result)
    if position == 'after':
        physical(owner_root, result['cleanup']['work'])
    else:
        assert position == 'before'
    os._exit(42)


if __name__ == '__main__':
    assert len(sys.argv) == 6 and sys.argv[1] == '--caller-loss'
    caller_loss(Path(sys.argv[2]), Path(sys.argv[3]), Path(sys.argv[4]), sys.argv[5])
