"""Caller SQLite locks and explicit filesystem inputs at real preflight boundaries."""
from contextlib import closing
from copy import deepcopy
import io
import json
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from poise.common import PoiseError, load_config
from poise.infrastructure.project_availability import ReadOnlyProjectAvailability
from poise.interfaces.projects import execute
from tests.conftest import write_json
from .test_project_preflight import case, expected, fixture, invoke, request, snapshot
from .test_preflight_store_preservation import selected_stores
from .test_preflight_regressions import forbid_later_owners


DATA = Path(__file__).parent / 'fixtures' / 'project_preflight'


def data(name):
    return json.loads((DATA / name).read_text(encoding='utf-8'))


@pytest.fixture
def optional_case(case):
    # Literal test-owned valid policies, never production-generated expectations.
    case[2].update(deepcopy(data('lock-path-optional-inputs.json')))
    write_json(case[0]['config_path'], case[2])
    return case


def field_parent(config, field):
    parts = field.split('.')
    parent = config
    for part in parts[:-1]:
        parent = parent[part]
    return parent, parts[-1]


def negative_rows():
    fields = data('lock-path-fields.json')
    values = [('missing', None), ('null', None), ('integer', 5),
              ('boolean', True), ('list', []), ('object', {}),
              ('empty', ''), ('nul', '\x00')]
    rows = [(field, kind, value) for field in fields for kind, value in values]
    rows += [(field, 'whitespace', '   ')
             for field in data('lock-path-nonblank-fields.json')]
    rows.append(('paths.runtime', 'float', 3.2))
    return rows


NEGATIVE_ROWS = negative_rows()


def wanted_context(case, field):
    context = expected(case)['context']
    for name in data('lock-path-context-masks.json').get(field, []):
        context[name] = None
    return context


def forbid_all_later_owners(monkeypatch):
    from poise.modules.actions.domain import CommitMessagePolicy
    calls = forbid_later_owners(monkeypatch)
    def forbidden(*args, **kwargs):
        calls.append('commit_policy')
        raise AssertionError('Commit policy called after configuration refusal')
    monkeypatch.setattr(CommitMessagePolicy, 'require', forbidden)
    return calls


@pytest.mark.parametrize('field,kind,value', NEGATIVE_ROWS,
                         ids=[f'{field}-{kind}' for field, kind, _ in NEGATIVE_ROWS])
def test_mandatory_filesystem_input_classification(optional_case, monkeypatch,
                                                 field, kind, value):
    case = optional_case
    parent, key = field_parent(case[2], field)
    if kind == 'missing':
        del parent[key]
    else:
        parent[key] = value
    write_json(case[0]['config_path'], case[2])
    calls = forbid_all_later_owners(monkeypatch)
    before = snapshot(case[0]['root'].parent)
    failure = None
    try:
        load_config(case[0]['config_path'])
    except Exception as exc:
        # Classification is the subject, so a wrong owner exception is an
        # assertion failure rather than a fixture/pytest execution error.
        failure = exc
    assert snapshot(case[0]['root'].parent) == before
    assert isinstance(failure, PoiseError), repr(failure)
    reason = str(failure)
    if kind == 'missing':
        assert reason == data('lock-path-missing-causes.json')[field]
    else:
        assert field in reason, f'Invalid field not attributed: {field}: {reason}'
    wanted = expected(case, status='rejected', ready=False, reason=reason)
    wanted['context'] = wanted_context(case, field)
    wanted['checks'] = fixture('checks-configuration-refused.json', {'cause': reason})
    assert invoke(case, request(case)) == (23, wanted)
    assert calls == []


POSITIVE_CASES = [
    'ordinary', 'relative-worktree', 'absolute-worktree', 'absolute-state',
    'absolute-requirements', 'relative-requirements', 'handoff-unicode-spaces',
    'transfer-whitespace-name', 'process-whitespace-name', 'optional-present',
]


@pytest.mark.parametrize('variant', POSITIVE_CASES)
def test_supported_filesystem_input_controls(case, variant):
    project, _, cfg, values = case
    if variant == 'relative-worktree':
        cfg['paths']['worktrees'] = 'worktrees/nested'
    elif variant == 'absolute-worktree':
        cfg['paths']['worktrees'] = str(project['app'] / 'task-worktrees')
    elif variant == 'absolute-state':
        state = project['root'].parent / 'absolute mutable state'
        cfg['paths']['state'] = str(state)
        values.update(state=str(state), task_db=str(state / 'state.sqlite'),
                      task_lock=str(state / 'state.lock'),
                      requirements_db=str(state / 'requirements.sqlite'),
                      requirements_lock=str(state / 'requirements.lock'))
    elif variant == 'absolute-requirements':
        outside = project['root'].parent / 'explicit requirements'
        cfg['paths']['requirements_database'] = str(outside / 'requirements.sqlite')
        cfg['paths']['requirements_lock'] = str(outside / 'requirements.lock')
        values.update(requirements_db=str(outside / 'requirements.sqlite'),
                      requirements_lock=str(outside / 'requirements.lock'))
    elif variant == 'relative-requirements':
        cfg['paths']['requirements_database'] = 'external/data.sqlite'
        cfg['paths']['requirements_lock'] = 'external/data.lock'
        values.update(requirements_db=str(project['root'] / 'state/external/data.sqlite'),
                      requirements_lock=str(project['root'] / 'state/external/data.lock'))
    elif variant == 'handoff-unicode-spaces':
        cfg['runtime_services']['handoff'].update(
            receipt='отчёт передачи.json', bundle='source with spaces.bundle',
            preserved_directory='сохранённые файлы')
    elif variant == 'transfer-whitespace-name':
        cfg['runtime_services']['transfer']['archive'] = '   '
    elif variant == 'process-whitespace-name':
        original = project['root'] / cfg['processes']['development']
        target = project['root'] / 'config/processes/   '
        target.write_bytes(original.read_bytes())
        cfg['processes']['development'] = 'config/processes/   '
    elif variant == 'optional-present':
        cfg.update(deepcopy(data('lock-path-optional-inputs.json')))
    write_json(project['config_path'], cfg)
    assert invoke(case, request(case)) == (17, expected(case))


def test_unexpected_owner_fault_remains_unknown(optional_case, monkeypatch):
    import poise.infrastructure.project_preflight as subject
    case = optional_case
    calls = forbid_all_later_owners(monkeypatch)
    def unexpected(path, manifest):
        assert manifest['project'] == 'demo'
        raise RuntimeError('unexpected mandatory filesystem owner fault')
    monkeypatch.setattr(subject, 'load_config_document', unexpected)
    reason = 'RuntimeError: unexpected mandatory filesystem owner fault'
    wanted = expected(case, ready=False, reason=reason)
    wanted['checks'] = fixture('checks-configuration-unknown.json', {'cause': reason})
    assert invoke(case, request(case)) == (23, wanted)
    assert calls == []


# Test-owned separate-process observer. It neither imports nor uses production
# snapshot/locking algorithms. No inherited caller SQLite connection is used.
OBSERVER = '''
import base64, json, os, pathlib, sqlite3, stat, sys
operation, selected = sys.argv[1:]
if operation == 'snapshot':
    root = pathlib.Path(selected)
    entries = {}
    for path in [root, *sorted(root.rglob('*'))]:
        info = path.lstat()
        content = None
        if stat.S_ISLNK(info.st_mode):
            content = ['symlink', os.readlink(path)]
        elif stat.S_ISREG(info.st_mode):
            content = ['file', base64.b64encode(path.read_bytes()).decode('ascii')]
        entries[str(path.relative_to(root))] = [info.st_mode, info.st_ino,
            info.st_mtime_ns, info.st_ctime_ns, content]
    print(json.dumps(entries, sort_keys=True))
elif operation in ('writer', 'exclusive'):
    db = sqlite3.connect(selected, timeout=0)
    try:
        try:
            db.execute('BEGIN IMMEDIATE' if operation == 'writer' else 'BEGIN EXCLUSIVE')
        except sqlite3.OperationalError as exc:
            print(json.dumps({'sqlite_errorcode': exc.sqlite_errorcode}))
        else:
            print(json.dumps({'sqlite_errorcode': None}))
    finally:
        db.rollback()
        db.close()
elif operation == 'refusal':
    from poise.common import load_config
    from poise.infrastructure.project_availability import ReadOnlyProjectAvailability
    from poise.modules.foundation.errors import PoiseError
    path = pathlib.Path(selected)
    _, cfg, _ = load_config(path)
    try:
        ReadOnlyProjectAvailability().require_task(path, cfg, 'EXISTS-HERE')
    except PoiseError as exc:
        print(json.dumps({'reason': str(exc)}))
    else:
        raise AssertionError('Independent shared-owner writer refusal did not occur')
else:
    raise AssertionError(operation)
'''


def observe(operation, selected):
    result = subprocess.run([sys.executable, '-B', '-c', OBSERVER,
                             operation, str(selected)], capture_output=True, text=True)
    assert result.returncode == 0, (operation, result.returncode, result.stderr)
    assert result.stderr == '', result.stderr
    return json.loads(result.stdout)


def public_locked_check(case, identifier):
    body = request(case)
    body['task_id'] = identifier
    output = io.StringIO()
    code = execute(case[1], io.BytesIO(json.dumps(body).encode()), output, action='check')
    return code, json.loads(output.getvalue())


@pytest.fixture
def rollback_store(case):
    selected_stores(case)
    with closing(sqlite3.connect(case[3]['task_db'])) as db:
        assert db.execute('PRAGMA journal_mode=DELETE').fetchone() == ('delete',)
        assert db.execute('SELECT claimed_by FROM tasks WHERE id=?',
                          ('EXISTS-HERE',)).fetchone() == ('foreign-owner',)
    return case


@pytest.mark.parametrize('variant', ['writer-refusal', 'reader-success',
                                     'reader-refusal', 'reader-exception'])
def test_preexisting_caller_sqlite_lock_survives(rollback_store, variant):
    case = rollback_store
    project, _, cfg, values = case
    _, loaded, _ = load_config(project['config_path'])
    assert loaded == cfg
    operation = 'writer' if variant == 'writer-refusal' else 'exclusive'
    connection = sqlite3.connect(values['task_db'])
    try:
        connection.execute('BEGIN IMMEDIATE' if operation == 'writer' else 'BEGIN')
        assert connection.execute('SELECT claimed_by FROM tasks WHERE id=?',
                                  ('EXISTS-HERE',)).fetchone() == ('foreign-owner',)
        assert observe(operation, values['task_db']) == {'sqlite_errorcode': 5}
        before = observe('snapshot', project['root'].parent)
        reason = None
        if variant == 'writer-refusal':
            reason = observe('refusal', project['config_path'])['reason']
            assert reason
            # The independent upstream observation must not weaken caller locks.
            assert observe(operation, values['task_db']) == {'sqlite_errorcode': 5}
            actual = public_locked_check(case, 'EXISTS-HERE')
        elif variant == 'reader-success':
            actual = public_locked_check(case, 'EXISTS-HERE')
        elif variant == 'reader-refusal':
            actual = public_locked_check(case, 'MISSING-HERE')
            reason = 'Configured Task DB has no Task: MISSING-HERE'
        else:
            def consumer(db, state):
                assert db.execute('SELECT claimed_by FROM tasks WHERE id=?',
                                  ('EXISTS-HERE',)).fetchone()[0] == 'foreign-owner'
                raise RuntimeError('literal consumer failure')
            with pytest.raises(RuntimeError, match='^literal consumer failure$'):
                ReadOnlyProjectAvailability._with_store(project['root'], cfg, consumer)
        # No parent content read or close on the original inode between these
        # assertions. The child is the only observer of actual kernel exclusion.
        assert connection.in_transaction is True
        assert observe(operation, values['task_db']) == {'sqlite_errorcode': 5}
        assert observe('snapshot', project['root'].parent) == before
        if variant != 'reader-exception':
            identifier = 'MISSING-HERE' if variant == 'reader-refusal' else 'EXISTS-HERE'
            wanted = expected(case, status='rejected' if reason else 'checked',
                              ready=reason is None, reason=reason)
            wanted['context']['requested_task_id'] = identifier
            if reason:
                wanted['checks'] = fixture('checks-task-refused.json', {'cause': reason})
            else:
                wanted['checks'] = fixture('lock-path-checks-task-passed.json', {})
            assert actual == (23 if reason else 17, wanted)
    finally:
        connection.rollback()
        connection.close()
    assert observe(operation, values['task_db']) == {'sqlite_errorcode': None}
