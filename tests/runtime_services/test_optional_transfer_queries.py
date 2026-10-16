"""Local Task reads do not compose an optional file-transfer operation."""
from copy import deepcopy
import hashlib
import json

import pytest

from batch.helpers import bootstrap, configure, request, result
from conftest import WorkPoise, add_test, write_json
from poise.application.work import WorkTools
from poise.common import PoiseError


def _tools(project, actor, *, transfer):
    configure(project)
    if not transfer:
        project['cfg']['runtime_services'].pop('transfer')
        write_json(project['config_path'], project['cfg'])
    return WorkTools(WorkPoise(project['config_path'], actor))


def test_local_bootstrap_without_transfer_service(project):
    tools = _tools(project, 'local', transfer=False)
    context = bootstrap(tools, project)
    assert context['stage'] == project['workflow']['stages'][0]['id']
    assert tools.runtime.current_task()['id'] == 'T1'
    assert tools.runtime.current_task()['claimed_by'] == 'local'


def test_existing_local_task_reopens_without_transfer_service(project):
    original = _tools(project, 'owner', transfer=True)
    context = bootstrap(original, project)
    tools = _tools(project, 'owner', transfer=False)
    reopened = tools.invoke(request('bootstrap', {
        'task': {'id': 'T1'}, 'decision': None, 'feedback': None, 'rework_stage': None,
    }))
    assert reopened['stage'] == context['stage']
    assert reopened['iteration'] == context['iteration']
    assert tools.runtime.current_task()['id'] == 'T1'
    assert tools.runtime.current_task()['claimed_by'] == 'owner'


@pytest.mark.parametrize('phase', ['preparing', 'prepared'])
def test_pending_transfer_rows_remain_visible_without_service(project, phase):
    original = _tools(project, 'owner', transfer=True)
    bootstrap(original, project)
    data = {'phase': phase, 'task_ids': ['T1'], 'sprint_ids': [], 'fingerprint': 'a' * 64}
    if phase == 'prepared':
        data['manifest'] = {'path': str(project['root'] / 'prepared.json'), 'digest': 'b' * 64}
    with original.runtime.store.transaction() as db:
        db.execute('INSERT INTO transfer_requests(actor,request_id,digest,data) VALUES(?,?,?,?)',
                   ('exporter', 'saved-export', 'c' * 64, json.dumps(data)))
    tools = _tools(project, 'owner', transfer=False)
    effects = tools.runtime.delivery_effects
    assert effects.preparing_consumer('T1') is (phase == 'preparing')
    assert effects.consumer_blocker(tools.runtime.current_task()) == 'transfer_pending'


def test_imported_delivery_projection_reads_without_service(project):
    tools = _tools(project, 'reader', transfer=False)
    # This is the persisted read projection, not a claimed file-import execution.
    delivery = {'binding': {'locations': {}}, 'manifest': {'workspaces': {'DRAFT': None}, 'files': []}}
    saved = {'task_ids': ['DRAFT'], 'package_digest': 'd' * 64, 'delivery': delivery}
    with tools.runtime.store.transaction() as db:
        db.execute('INSERT INTO transfer_imports(package_digest,data) VALUES(?,?)',
                   ('d' * 64, json.dumps(saved)))
    with tools.runtime.store.unit_of_work() as uow:
        observed = uow.transfers.delivery_for_task('DRAFT')
    assert observed == {**delivery, 'package_digest': 'd' * 64,
                        'verification_opened': False, 'verification_origin': None}


def _saved_local_handoff(project):
    project['cfg']['runtime_services']['handoff'] = {
        'directory': 'handoffs', 'preserved_directory': 'preserved',
        'file_mode': 384, 'bundle': 'handoff.tar.gz', 'receipt': 'receipt.json',
    }
    sender = _tools(project, 'sender', transfer=True)
    context = bootstrap(sender, project)
    add_test(context['worktree'])
    handed = sender.invoke(request('handoff', {
        'request_id': 'local-handoff', 'reason': 'Continue local Task work',
        'result': result(context, 'Preserve local work'),
        'commit_message': 'Save local handoff', 'artifact_paths': [],
    }))
    return sender, context, handed


def test_local_handoff_resume_without_transfer_service(project):
    sender, context, handed = _saved_local_handoff(project)
    receiver = _tools(project, 'receiver', transfer=False)
    resumed = receiver.invoke(request('bootstrap', {
        'task': {'id': 'T1'}, 'decision': None, 'feedback': None, 'rework_stage': None,
    }))
    assert receiver.runtime.current_task()['id'] == 'T1'
    assert resumed['stage'] == context['stage']
    assert resumed['iteration'] == context['iteration']
    assert resumed['worktree'] == context['worktree']
    assert handed['status'] == 'handed_off'
    assert receiver.runtime.current_task()['claimed_by'] == 'receiver'


def _delivery_state(runtime):
    with runtime.store.transaction() as db:
        return {table: [tuple(row) for row in db.execute(f'SELECT * FROM {table} ORDER BY rowid')]
                for table in ('tasks', 'journal', 'transfer_imports', 'handoffs')}


@pytest.mark.parametrize('state', ['unverified_present', 'unverified_missing', 'verification_opened'])
def test_handoff_consumer_reads_persisted_import_without_service(project, state):
    sender, context, handed = _saved_local_handoff(project)
    material = project['root'] / 'delivered-task-material.md'
    payload = b'Required delivered Task material.\n'
    if state == 'unverified_present':
        material.write_bytes(payload)
    # Persist only the delivery projection consumed by validate_resume. This does
    # not claim that an export/import flow was executed by the test.
    imported = {'task_ids': ['T1'], 'package_digest': 'd' * 64,
        'delivery': {'binding': {'locations': {'required': str(material)}},
                     'manifest': {'workspaces': {'T1': None}, 'omitted_artifacts': [],
                                  'files': [{'task_id': 'T1', 'placement': 'required',
                                             'digest': hashlib.sha256(payload).hexdigest()}]}}}
    with sender.runtime.store.transaction() as db:
        db.execute('INSERT INTO transfer_imports(package_digest,data) VALUES(?,?)',
                   ('d' * 64, json.dumps(imported)))
    if state == 'verification_opened':
        with sender.runtime.store.unit_of_work() as uow:
            uow.transfers.open_verification('T1', 'source-verifier', 'd' * 64)
    receiver = _tools(project, 'reader', transfer=False)
    runtime = receiver.runtime
    data = runtime.task_queries.record('T1')
    expected = runtime.handoff_tools.commands.latest('T1')
    before = _delivery_state(runtime)
    if state == 'unverified_missing':
        with pytest.raises(PoiseError, match='^Delivered current Task material missing or changed$'):
            runtime.handoff_tools.validate_resume(data)
    else:
        assert runtime.handoff_tools.validate_resume(data) == expected
    assert _delivery_state(runtime) == before
    assert material.exists() is (state == 'unverified_present')
    assert handed['status'] == 'handed_off'


@pytest.mark.parametrize('configured', [False, True])
def test_explicit_transfer_still_requires_its_service(project, configured):
    tools = _tools(project, 'explicit', transfer=configured)
    payload = {'action': 'export', 'request_id': 'explicit-export',
               'task_ids': ['MISSING'], 'sprint_id': None, 'handoff': None}
    with pytest.raises(PoiseError) as raised:
        tools.invoke(request('transfer', deepcopy(payload)))
    expected = 'Unknown task in transfer selection' if configured else 'Файловый перенос задач не настроен'
    assert str(raised.value) == expected
