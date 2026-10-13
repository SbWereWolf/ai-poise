"""Preserve arbitrary working databases and reject unusable known settings."""
from copy import deepcopy
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from conftest import track_runtime_dispatchers
from hook_transport.helpers import install, settings as hook_settings
from poise.common import PoiseError
from poise.infrastructure.hook_transport import HookSettings
from .material_relocation_helpers import configured_hook, immutable, prepare
from .test_update import update_tools


def files(root):
    return {path.relative_to(root).as_posix(): path.read_bytes()
            for path in root.rglob('*') if path.is_file()}


def drain(dispatchers, request):
    assert dispatchers, 'The fixture must observe every actual runtime writer'
    for dispatcher in dispatchers:
        request.addfinalizer(dispatcher.close)
        dispatcher.flush()


def invoke(case, packet):
    return subprocess.run(
        [sys.executable, '-B', '-m', 'poise', 'project-config', '--settings', str(case['settings'])],
        input=json.dumps(packet), text=True, capture_output=True,
        env={**os.environ, 'PYTHONPATH': str(Path.cwd() / 'src')})


def assert_rejected(process, field):
    assert process.returncode == 2 and process.stderr == '', (
        'Unusable input escaped structured domain refusal: ' + process.stderr[-400:])
    result = json.loads(process.stdout)
    assert result['status'] == 'rejected'
    assert field in result['reason'], 'Refusal must identify the unusable field'


@pytest.mark.parametrize('fault', ['rowid', 'application-id'])
def test_unregistered_sqlite_conflict_preserves_both_copies(
        project, tmp_path, monkeypatch, request, fault):
    import poise.infrastructure.project_config as effects
    dispatchers = track_runtime_dispatchers(monkeypatch)
    case = prepare(project, tmp_path)
    drain(dispatchers, request)
    original = case['source'] / 'operator-working.sqlite'
    with sqlite3.connect(original) as db:
        db.execute('CREATE TABLE working(value TEXT NOT NULL)')
        db.executemany('INSERT INTO working(value) VALUES (?)',
                       [('original-A',), ('original-B',)])
        db.execute('PRAGMA application_id=1234')
    original_bytes = original.read_bytes()
    history = immutable(case['tools'].runtime)
    observed = []

    def interrupted(*args):
        observed.append('retirement')
        raise OSError('Interruption immediately before actual source retirement')

    with monkeypatch.context() as patch:
        patch.setattr(effects, 'remove_source_state', interrupted)
        with pytest.raises(PoiseError, match='interrupted'):
            update_tools(case['settings']).apply(case['packet'])
    assert observed == ['retirement'] and original.read_bytes() == original_bytes
    copied = case['destination'] / original.name
    assert copied.read_bytes() == original_bytes
    with sqlite3.connect(copied) as db:
        assert db.execute('SELECT rowid,value FROM working ORDER BY rowid').fetchall() == [
            (1, 'original-A'), (2, 'original-B')]
        if fault == 'rowid':
            db.execute('UPDATE working SET rowid=rowid+10')
        else:
            db.execute('PRAGMA application_id=5678')
    conflict_bytes = copied.read_bytes()
    assert conflict_bytes != original_bytes
    try:
        outcome = update_tools(case['settings']).apply(case['packet'])
    except PoiseError as exc:
        outcome = {'refused': str(exc)}
    assert 'refused' in outcome and original.is_file(), (
        'Conflicting unregistered SQLite bytes authorized deletion of the original')
    assert outcome['refused'].strip()
    assert original.read_bytes() == original_bytes
    assert copied.read_bytes() == conflict_bytes
    with sqlite3.connect(original) as db:
        assert db.execute('SELECT rowid,value FROM working ORDER BY rowid').fetchall() == [
            (1, 'original-A'), (2, 'original-B')]
        assert db.execute('PRAGMA application_id').fetchone() == (1234,)
    assert immutable(case['tools'].runtime) == history


def installed_case(project, tmp_path, monkeypatch, request):
    dispatchers = track_runtime_dispatchers(monkeypatch)
    case = prepare(project, tmp_path, native_hooks=True)
    service = configured_hook(case, tmp_path)
    install(service)
    drain(dispatchers, request)
    return case, service


def baseline(case, service):
    return {
        'source': files(case['source']),
        'config': case['config_path'].read_bytes(),
        'hooks': service.settings.hooks_file.read_bytes(),
        'settings': service.settings.path.read_bytes(),
    }


def assert_preserved(case, service, before):
    assert baseline(case, service) == before
    assert not case['destination'].exists()
    assert not case['hook_target'].exists()
    # These fixture paths come from setup_case and the explicit request, not the
    # production receipt resolver. Invalid proposals must not publish an intent.
    receipt = Path(case['settings']).parent / case['packet']['receipt_path']
    assert not receipt.exists()
    assert not receipt.with_name(receipt.name + '.pending').exists()


@pytest.mark.parametrize('fault', ['root-null', 'probe-output-list'])
def test_complete_hook_proposal_has_structured_refusal(
        project, tmp_path, monkeypatch, request, fault):
    case, service = installed_case(project, tmp_path, monkeypatch, request)
    before = baseline(case, service)
    # Establish the same full preservation oracle with an already-correct
    # rejection before observing the missing nested-value behavior.
    control = deepcopy(case['packet'])
    control['state_relocation'] = 'invalid-object'
    assert_rejected(invoke(case, control), 'state_relocation')
    assert_preserved(case, service, before)
    packet = deepcopy(case['packet'])
    settings = packet['state_relocation']['hook_destinations'][0]['settings']
    if fault == 'root-null':
        settings['root'] = None
        field = 'root'
    else:
        settings['probe_files']['stdout'] = []
        field = 'stdout'
    process = invoke(case, packet)
    assert_rejected(process, field)
    assert_preserved(case, service, before)


@pytest.mark.parametrize('field', ['expected_source', 'destination', 'settings_path'])
def test_nul_relocation_path_has_structured_refusal(
        project, tmp_path, monkeypatch, request, field):
    case, service = installed_case(project, tmp_path, monkeypatch, request)
    before = baseline(case, service)
    control = deepcopy(case['packet'])
    control['state_relocation'] = 'invalid-object'
    assert_rejected(invoke(case, control), 'state_relocation')
    assert_preserved(case, service, before)
    packet = deepcopy(case['packet'])
    move = packet['state_relocation']
    target = move['hook_destinations'][0] if field == 'settings_path' else move
    target[field] += '\x00'
    process = invoke(case, packet)
    assert_rejected(process, field)
    assert_preserved(case, service, before)


PATH_FIELDS = [
    'root',
    'project_config',
    'python',
    'source_root',
    'hooks_file',
    'definitions',
    'state',
    'lock',
    'database',
    'bindings',
    'launcher',
    'binding_file',
    'observations',
    'response_file',
]
KNOWN_VALUES = [
    *[(key, None) for key in PATH_FIELDS],
    *[(key, '\x00') for key in PATH_FIELDS],
    ('probe_files.stdout', []),
    ('probe_files.stderr', []),
    ('probe_files.receipt', []),
    ('exit_codes.success', []),
    ('exit_codes.incomplete', []),
    ('exit_codes.rejected', []),
    ('cwd_roots', [None]),
    ('cwd_roots', ['/unusable\x00cwd']),
]
KNOWN_IDS = [
    *[key + '-null' for key in PATH_FIELDS],
    *[key + '-nul' for key in PATH_FIELDS],
    'probe-stdout-list',
    'probe-stderr-list',
    'probe-receipt-list',
    'exit-success-list',
    'exit-incomplete-list',
    'exit-rejected-list',
    'cwd-null-element',
    'cwd-nul-element',
]


@pytest.mark.parametrize('field,value', KNOWN_VALUES, ids=KNOWN_IDS)
def test_hook_settings_known_value_refuses_before_operands(project, tmp_path, field, value):
    path = hook_settings(project, tmp_path)
    raw = json.loads(path.read_text())
    # A complete independently arranged valid proposal remains a positive control.
    assert HookSettings.proposed(path, raw).raw == raw
    before = files(project['root'])
    proposed = deepcopy(raw)
    keys = field.split('.')
    target = proposed
    for key in keys[:-1]:
        target = target[key]
    target[keys[-1]] = target[keys[-1]] + value if value == '\x00' else value
    reason = None
    escaped = None
    try:
        HookSettings.proposed(path, proposed)
    except PoiseError as exc:
        reason = str(exc)
    except (TypeError, ValueError) as exc:
        # These are the observed operand failures this regression must disprove.
        # Other exception classes remain unexpected complete-report errors.
        escaped = type(exc).__name__
    assert files(project['root']) == before
    assert reason is not None, (
        'Known unusable setting requires domain refusal: ' + field + '; escaped=' + str(escaped))
    labels = {'cwd_roots': ('cwd',), 'python': ('python', 'interpreter'),
              'source_root': ('source_root', 'entry point')}
    assert any(label in reason for label in labels.get(field, (keys[-1],))), (
        'Domain refusal must identify the unusable known setting')


def test_unknown_hook_options_survive_actual_publication(project, tmp_path, monkeypatch, request):
    case, service = installed_case(project, tmp_path, monkeypatch, request)
    packet = deepcopy(case['packet'])
    raw = packet['state_relocation']['hook_destinations'][0]['settings']
    unknown = {'note': [None, {'values': []}]}
    for target in (raw, raw['probe_files'], raw['exit_codes']):
        target['operator_extension'] = deepcopy(unknown)
    history = immutable(case['tools'].runtime)
    result = update_tools(case['settings']).apply(packet)
    assert result['state_relocation']['status'] == 'completed'
    assert not case['source'].exists()
    published = json.loads(case['hook_target'].read_text())
    for target in (published, published['probe_files'], published['exit_codes']):
        assert target['operator_extension'] == {'note': [None, {'values': []}]}
    assert published == raw
    with sqlite3.connect(case['destination'] / case['project']['cfg']['paths']['database']) as db:
        assert db.execute('SELECT COUNT(*) FROM tasks WHERE id=?', ('T1',)).fetchone() == (1,)
    # Ownership and immutable Task history remain on the destination database.
    from .material_relocation_helpers import fresh
    assert immutable(fresh(case).runtime) == history
