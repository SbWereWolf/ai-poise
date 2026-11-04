"""Transparent fixture preparation and public calls; literal oracles live in fixtures."""
from copy import deepcopy
from contextlib import closing
import base64
import hashlib
import json
from pathlib import Path
import re
import sqlite3

import pytest

from batch.helpers import configure, request
from conftest import WorkPoise, bind_task_requirements, git, write_json
from poise.application.work import WorkTools
from poise.modules.accounting.clock import ClockObservation
from poise.modules.foundation.errors import PoiseError
from sprints.helpers import draft, publish, policy

FIXTURES = Path(__file__).parent / 'fixtures'
DATA = json.loads((FIXTURES / 'contract.json').read_text())
PACKETS = json.loads((FIXTURES / 'packets.json').read_text())


class FixtureClock:
    def __init__(self):
        self.tick = 0

    def observe(self):
        self.tick += 1
        return ClockObservation('2026-11-03T12:00:00+00:00', self.tick, 'feedback-fixture')


def client(project, actor='material-author'):
    return WorkTools(WorkPoise(project['config_path'], actor, FixtureClock()))


def configure_case(project, *, root_form='absolute', storage=True, terminal=False):
    configure(project)
    workflow = json.loads((FIXTURES / 'workflow.json').read_text())
    if terminal:
        workflow['stages'] = workflow['stages'][:1]
        workflow['stages'][0]['transitions']['complete'] = None
    project['workflow'] = workflow
    write_json(project['root'] / 'config/workflows/development.json', workflow)
    project['cfg']['sprint'] = policy()
    project['cfg']['automatic_checks'] = []
    # The independent root coordinates use fixture state, not subject resolution.
    state = project['root'] / project['cfg']['paths']['state']
    project['material_root'] = state / 'user-materials'
    if storage:
        project['cfg']['paths']['task_materials'] = (
            str(project['material_root']) if root_form == 'absolute' else 'user-materials')
    else:
        project['cfg']['paths'].pop('task_materials', None)
    write_json(project['config_path'], project['cfg'])
    return project


def contract(project, identifier='T1', member=False):
    value = deepcopy(project['task'])
    value.update(id=identifier, sprint_id='S' if member else None,
                 goal=DATA['goal'], requirements=DATA['requirements'],
                 definition_of_done=DATA['dod'], methods=[], method_inputs=[],
                 checks={s['id']: [] for s in project['workflow']['stages']},
                 evidence_plan={s['id']: {'subject_methods': {}, 'arguments': [],
                                          'review_arguments': []}
                                for s in project['workflow']['stages']})
    value['stage_contracts'] = [
        {'stage_id': s['id'], 'allowed_paths': s['allowed_paths'],
         'entry_requirements': [], 'exit_requirements': [],
         'criteria': {'requirements': [], 'definition_of_ready': [], 'definition_of_done': []}}
        for s in project['workflow']['stages']]
    value['decomposition'] = {'kind': 'ordinary', 'integration': None, 'phases': [
        {'stage': s['id'], 'skills': ['task-domain'], 'areas': []}
        for s in project['workflow']['stages']]}
    value['content_contract'] = {'sections': [
        {'id': 'intake', 'template': DATA['source_context'], 'normalization': 'strip',
         'write_stages': ['work']}], 'routes': [], 'requirements': []}
    return bind_task_requirements(value, project['requirements_registry'])


def boot(c, task=None, decision=None):
    return c.invoke(request('bootstrap', {'task': task, 'decision': decision,
                                         'feedback': None, 'rework_stage': None}))


def arrange(project, state='newborn', *, member=False, identifier='T1', typed=False):
    owner = client(project, 'executor')
    if state in ('newborn', 'available'):
        born = owner.invoke(request('task', {'action': 'create', 'task_id': identifier,
            'sprint_id': None, 'request_id': f'born-{identifier}'}))
        patch = {'goal': DATA['goal']}
        if typed or state == 'available':
            patch = {k: v for k, v in contract(project, identifier).items()
                     if k not in ('id', 'sprint_id')}
        born = owner.invoke(request('task', {'action': 'edit', 'task_id': identifier,
            'expected_revision': born['revision'], 'request_id': f'edit-{identifier}',
            'patch': patch, 'remove': []}))
        if state == 'available':
            owner.invoke(request('task', {'action': 'ready', 'task_id': identifier,
                'expected_revision': born['revision'], 'request_id': f'ready-{identifier}'}))
        return owner, None
    body = contract(project, identifier, member)
    if member:
        created = draft(owner, [body])
        publish(owner, created['revision'])
        ctx = boot(owner, {'id': identifier})
    else:
        ctx = boot(owner, body)
    if state != 'active':
        worktree = Path(ctx['worktree'])
        (worktree / 'src/fixture_result.py').write_text('RESULT = 47\n')
        result = deepcopy(ctx['result_template'])
        result['sections']['report'] = DATA['report']
        result['sections']['intake'] = DATA['saved_intake']
        result['commit_message'] = 'Preserve the fixture work result'
        owner.invoke(request('verify', {'result': result, 'artifacts': []}))
        if state == 'accepted':
            owner.invoke(request('accept', {}))
    assert owner.runtime.task_queries.record(identifier)['status'] == state
    return owner, ctx


def subject(c, packet):
    try:
        return c.invoke(deepcopy(packet))
    except PoiseError as error:
        if str(error) == 'Unknown work operation':
            pytest.fail('TASK-FEEDBACK-MISSING: valid addressed material operation is unavailable')
        raise


def append(c, *, task='T1', request_id='source-1', text=None, attachments=(), items=None):
    packet = deepcopy(PACKETS['append'])
    packet['input'].update(task_id=task, request_id=request_id)
    packet['input']['items'] = (deepcopy(items) if items is not None else [
        {'text': DATA['initial_text'] if text is None else text,
         'attachments': list(attachments)}])
    return subject(c, packet)


def read(c, task='T1'):
    packet = deepcopy(PACKETS['read']); packet['input']['task_id'] = task
    return subject(c, packet)


def read_bytes(c, attachment, *, task='T1'):
    packet = deepcopy(PACKETS['bytes'])
    packet['input'].update(task_id=task, attachment_id=attachment['id'])
    value = subject(c, packet)
    assert set(value) - {'interaction'} == set(DATA['bytes_fields'])
    assert value['status'] == 'read_only' and value['task_id'] == task
    assert value['attachment_id'] == attachment['id']
    assert value['encoding'] == 'base64'
    assert (value['name'], value['digest'], value['size']) == (
        attachment['name'], attachment['digest'], attachment['size'])
    return base64.b64decode(value['data'], validate=True)


def source(project, payload=1, *, filename=None, content=None):
    case = DATA['payloads'][payload]
    raw = bytes.fromhex(case['hex']) if content is None else content
    root = project['root'] / 'intake'; root.mkdir(exist_ok=True)
    path = root / (filename or case['name']); path.write_bytes(raw)
    return {'name': case['name'] if filename is None else filename,
            'source': {'kind': 'file', 'path': str(path.resolve()),
                       'digest': hashlib.sha256(raw).hexdigest()}}, raw


def assert_comment(comment, text, actor='material-author'):
    assert set(comment) == set(DATA['comment_fields'])
    assert isinstance(comment['id'], str) and comment['id']
    assert comment['text'] == text and comment['author'] == actor
    assert comment['created_at'] == DATA['timestamp']
    for item in comment['attachments']:
        assert set(item) == set(DATA['attachment_fields'])
        assert isinstance(item['id'], str) and item['id']


def database_path(c):
    return c.runtime.state / c.runtime.paths['database']


def protected(c):
    """Explicit authoritative rows; append audit and optional telemetry are not execution."""
    sql = {
        'tasks': "SELECT id,status,stage_index,iteration,claimed_by,version,current_submission_id,json_extract(metadata,'$.goal'),json_extract(metadata,'$.newborn'),json_extract(metadata,'$.contract'),json_extract(metadata,'$.requirements_snapshot'),json_extract(metadata,'$.requirements_agreement') FROM tasks ORDER BY id",
        'sessions': 'SELECT id,task_id FROM sessions WHERE task_id IS NOT NULL ORDER BY id',
        'execution': 'SELECT task_id,data,version FROM task_execution ORDER BY task_id',
        'workflow': 'SELECT task_id,data FROM task_workflows ORDER BY task_id',
        'submissions': 'SELECT seq,task_id,stage,iteration,digest,data FROM submissions ORDER BY seq',
        'results': 'SELECT task_id,submission_id,data FROM task_results ORDER BY task_id,submission_id',
        'content_contracts': 'SELECT task_id,version,data FROM content_contracts ORDER BY task_id,version',
        'section_layers': 'SELECT submission_id,task_id,section_id,content,content_state FROM section_layers ORDER BY task_id,submission_id,section_id',
        'evidence': 'SELECT id,task_id,stage,iteration,data FROM evidence ORDER BY id',
        'methods': 'SELECT task_id,method_id,version,data FROM task_methods ORDER BY task_id,method_id',
        'events': 'SELECT seq,task_id,version,at,data FROM task_events ORDER BY seq',
    }
    with closing(sqlite3.connect(database_path(c))) as db:
        return {name: db.execute(query).fetchall() for name, query in sql.items()}


def files(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file()} if root.exists() else {}


def assert_reason(error, kind):
    message = str(error)
    assert re.search(DATA['failure_patterns'][kind], message), message
    assert 'Unknown work operation' not in message
    # Refusals contain a remedy, not only an exception class or a bare noun.
    assert re.search(r'use|retry|provide|select|correct|create|configure|read|choose|new|настрой|укаж|повтор|созда|исправ|прочит|выбер|нов', message, re.I), message


def own_other_task(c):
    born = c.invoke(request('task', {'action': 'create', 'task_id': 'OTHER',
        'sprint_id': None, 'request_id': 'create-other'}))
    c.invoke(request('task', {'action': 'edit', 'task_id': 'OTHER',
        'expected_revision': born['revision'], 'request_id': 'edit-other',
        'patch': {'goal': DATA['other_text']}, 'remove': []}))


def prepare_delivery(project, owner, ctx):
    from terminal_materials.helpers import agree, declaration, file_output
    root = Path(ctx['task_root'])
    service = root / 'nested/service.txt'; service.parent.mkdir(parents=True, exist_ok=True)
    service.write_bytes(b'temporary task service\n')
    source_path = root / 'artifacts/customer.txt'
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_bytes(b'customer result\n')
    destination = project['root'] / 'customer/result.txt'
    agree(owner, declaration([file_output(source_path, destination)]))
    return root, destination


def finish_task(project, owner, state):
    from terminal_materials.helpers import finish
    commit = owner.runtime.task_queries.record('T1')['last_report']['commit']
    return finish(project, owner, commit, state)


def saved_task_expectation(c, state, *, typed=False):
    """Independent SQL receipt bytes plus literal saved text, never the new producer."""
    with closing(sqlite3.connect(database_path(c))) as db:
        receipts = [json.loads(row[0]) for row in db.execute(
            'SELECT data FROM task_results WHERE task_id=? ORDER BY submission_id', ('T1',))]
        sections = dict(db.execute(
            'SELECT section_id,content FROM section_layers WHERE task_id=? '
            'ORDER BY submission_id,section_id', ('T1',)))
    full_contract = state != 'newborn' or typed
    texts = [DATA['source_context']] if full_contract else []
    if state in ('verified', 'accepted', 'completed', 'cancelled'):
        assert sections == {'intake': DATA['saved_intake'], 'report': DATA['report']}
        assert len(receipts) == 1
        assert receipts[0]['task'] == 'T1' and receipts[0]['stage'] == 'work'
        assert receipts[0]['status'] == 'verified' and receipts[0]['checks'] == []
        assert len(receipts[0]['commit']) == 40 and len(receipts[0]['verified_tree']) == 40
        texts += [DATA['saved_intake'], DATA['report']]
    else:
        assert receipts == [] and sections == {}
    return {'id': 'T1', 'status': state, 'goal': DATA['goal'],
            'requirements': DATA['requirements'] if full_contract else [],
            'definition_of_done': DATA['dod'] if full_contract else [],
            'stage': None if state == 'newborn' else 'work',
            'results': receipts, 'required_content_texts': texts}


def assert_saved_task(actual, expected):
    assert set(actual) == set(DATA['task_fields'])
    for field in ('id', 'status', 'goal', 'requirements', 'definition_of_done', 'stage', 'results'):
        assert actual[field] == expected[field], field
    # Content retains the existing owner's representation. Require exact scalar
    # values, not a report substring; the populated intake differs from template.
    def strings(value):
        if isinstance(value, str):
            return [value]
        if isinstance(value, dict):
            return [s for item in value.values() for s in strings(item)]
        if isinstance(value, list):
            return [s for item in value for s in strings(item)]
        return []
    content = strings(actual['content'])
    for text in expected['required_content_texts']:
        assert text in content, f'Saved content missing: {text}'


def arrange_two_worktrees(project, owner, context, author):
    other = boot(author, contract(project, 'OTHER'))
    contexts = {'executor': context, 'material-author': other}
    for actor, ctx in contexts.items():
        root = Path(ctx['worktree']); values = DATA['worktree_wip'][actor]
        (root / 'src/double.py').write_bytes(values['tracked'].encode())
        (root / 'src/staged.py').write_bytes(values['staged'].encode())
        git(root, 'add', 'src/staged.py')
        (root / 'notes.txt').write_bytes(values['untracked'].encode())
    assert Path(context['worktree']) != Path(other['worktree'])
    return contexts


def worktree_snapshots(contexts):
    result = {}
    for actor, ctx in contexts.items():
        root = Path(ctx['worktree'])
        index = Path(git(root, 'rev-parse', '--path-format=absolute', '--git-path', 'index'))
        values = {name: (root / name).read_bytes()
                  for name in ('src/double.py', 'src/staged.py', 'notes.txt')}
        literal = DATA['worktree_wip'][actor]
        assert values == dict(zip(('src/double.py', 'src/staged.py', 'notes.txt'),
                                 (literal[key].encode() for key in ('tracked', 'staged', 'untracked'))))
        result[actor] = {'head': git(root, 'rev-parse', 'HEAD'),
                        'index': index.read_bytes(), 'files': values}
    return result
