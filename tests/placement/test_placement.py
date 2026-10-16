"""F03 public placement acceptance: literal oracles and actual filesystem effects.

Every negative arrangement starts with a valid CLI call. On the old source only
that observed missing command is RED; no import or setup exception is translated.
"""
from copy import deepcopy
import fcntl
import json
import os
from pathlib import Path
import stat
import subprocess

import pytest

from .helpers import Scenario, canonical, diagnostic, digest, item, literal
from tests.conftest import write_json


def scene(project, tmp_path):
    return Scenario(project, tmp_path)


def pending(s, key, *, purpose='source', recipient='product', lifetime='versioned', name='next.py'):
    return s.request(items=[item(key, purpose=purpose, recipient=recipient, lifetime=lifetime, name=name)])


def test_taskless_prepare_and_consumer_contract(project, tmp_path):
    s = scene(project, tmp_path)
    before = s.initial()
    assert not (s.checkout / 'src/report.py').exists()
    value = s.assert_result(s.invoke(s.request(operation='check')))
    s.assert_effect(value, before, None, 'none', 'not_applicable')
    assert s.state.read_bytes() == before
    unreserved = s.assert_result(s.invoke(s.request(operation='check', items=[item('other', name='other.py', path='src/other.py')])),
                               rows=[s.row(key='other', path='src/other.py', absolute_path=str(s.checkout / 'src/other.py'), reserved=False)])
    assert unreserved['state']['effect'] == 'none'
    assert s.state.read_bytes() == before
    s.state.unlink()
    value = s.assert_result(s.invoke(s.request(operation='check')), status='needs_inputs',
                            rows=[s.row(reserved=False)],
                            diagnostics=[diagnostic('state_missing', None, 'state', 'prepare')])
    assert value['state']['effect'] == 'none' and not s.state.exists()


def test_two_project_policies_and_storage_isolation(project, tmp_path):
    s = scene(project, tmp_path)
    source = s.initial()
    # Reuse the existing public project-setup fixture owner for an independent consumer.
    from tests.projects.helpers import setup_case
    from poise.composition import project_tools
    from shutil import copytree
    beta_root = tmp_path / 'beta'
    beta_root.mkdir()
    checkout = beta_root / 'checkout'
    copytree(project['app'], checkout)
    cfg = deepcopy(project['cfg'])
    cfg['project'] = 'beta'
    cfg['git']['repository'] = str(checkout)
    write_json(beta_root / 'config/workflows/development.json', project['workflow'])
    base = {**project, 'root': beta_root, 'app': checkout, 'cfg': cfg}
    setup, _, packet = setup_case(base)
    packet['edits'][0]['value'] = 'beta'
    created = project_tools(setup).apply(packet)
    base['config_path'] = Path(created['config_path'])
    base['root'] = base['config_path'].parent
    beta = Scenario(base, beta_root, beta=True)
    beta.initial()
    assert s.state != beta.state
    assert s.state.read_bytes() == source
    assert beta.read() == beta.fixture('state-beta-source.template.json')


def test_product_versioned_and_local_resource_categories(project, tmp_path):
    s = scene(project, tmp_path)
    s.initial()
    cases = s.fixture('resource-cases.template.json')[:5]
    rows = sorted([v['row'] for v in cases], key=lambda v: v['key'])
    s.assert_result(s.invoke(s.request(items=[v['item'] for v in reversed(cases)])), rows=rows)
    records = s.read()['reservations']
    assert [v['key'] for v in records] == ['backup', 'config', 'data', 'deps', 'index', 'source-report']
    for row in rows:
        assert row['versioning'] == 'ignored'
        assert row['owner'] == {'kind': 'product', 'id': s.project}
        assert not Path(row['absolute_path']).exists()
        record = next(v for v in records if v['key'] == row['key'])
        assert {k: v for k, v in record.items() if k not in ['purpose', 'recipient', 'lifetime', 'name']} == {
            k: v for k, v in row.items() if k not in ['status', 'reserved']}


def test_harness_handoff_and_declared_tool_outputs(project, tmp_path):
    s = scene(project, tmp_path)
    s.initial()
    cases = s.fixture('resource-cases.template.json')[5:]
    expected = sorted([v['row'] for v in cases], key=lambda v: v['key'])
    s.assert_result(s.invoke(s.request(items=[v['item'] for v in cases])), rows=expected)
    before = s.state.read_bytes()
    for case in cases:
        output = Path(case['row']['absolute_path'])
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b'controlled tool output\n')
    request = s.request(operation='check', items=[{**v['item'], 'path': v['row']['path']} for v in cases])
    value = s.assert_result(s.invoke(request), rows=expected)
    s.assert_effect(value, before, None, 'none', 'not_applicable')
    assert [Path(v['row']['absolute_path']).read_bytes() for v in cases] == [b'controlled tool output\n'] * 2
    assert s.state.read_bytes() == before


def test_known_locations_reuse_authority(project, tmp_path):
    s = scene(project, tmp_path)
    before = s.initial()
    value = s.assert_result(s.invoke())
    s.assert_effect(value, before, before, 'unchanged', 'confirmed')
    assert s.read()['authorizations'] == []
    assert s.read()['baseline']['entries'] == ['.git', 'AGENTS.md', 'docs', 'src']
    # Declared external roots carry their existing scope authority, including new child paths.
    case = s.fixture('resource-cases.template.json')[0]
    s.assert_result(s.invoke(s.request(items=[case['item']])), rows=[case['row']])
    assert s.read()['authorizations'] == []


def test_unknown_root_before_creation_and_explicit_agreement(project, tmp_path):
    s = scene(project, tmp_path)
    before = s.initial()
    # Each scope case starts with an independently literal ungranted state.
    # A previously stored correct grant cannot mask a wrongly scoped grant.
    for case in s.fixture('scoped-approval-cases.template.json'):
        s.seed(case['before'])
        value = s.assert_result(s.invoke(case['request']), status=case['status'],
                                rows=[case['row']], diagnostics=case['diagnostics'])
        expected = canonical(case['expected_state'])
        assert s.state.read_bytes() == expected, case['id']
        if case['status'] != 'ready':
            assert expected == before and s.read()['authorizations'] == [], case['id']
        assert not Path(case['row']['absolute_path']).exists()
    s.seed(s.fixture('state-source.template.json'))
    request = s.request(items=[item(path='new-area/report.py')])
    row = s.row(status='needs_agreement', path='new-area/report.py',
                absolute_path=str(s.checkout / 'new-area/report.py'), reserved=False)
    reasons = [diagnostic('approval_required', 'source-report', 'items:source-report:path',
                          'agree:product:new-area')]
    # Use a fresh declaration key; the original reservation may not silently move.
    request['items'][0]['key'] = 'approved-report'
    row['key'] = 'approved-report'
    reasons[0].update(key='approved-report', field='items:approved-report:path')
    s.assert_result(s.invoke(request), status='needs_agreement', rows=[row], diagnostics=reasons)
    assert s.state.read_bytes() == before and not (s.checkout / 'new-area').exists()
    (s.checkout / 'new-area').mkdir()
    s.assert_result(s.invoke(request), status='needs_agreement', rows=[row], diagnostics=reasons)
    approvals = s.fixture('authorizations.json')
    request['authorizations'] = approvals
    s.assert_result(s.invoke(request), rows=[{**row, 'status': 'allowed', 'reserved': True,
                                            'authorization_refs': ['configured-product', 'human:new-area-for-product-report']}])
    assert s.read()['baseline']['entries'] == ['.git', 'AGENTS.md', 'docs', 'src']
    assert s.read()['authorizations'] == approvals
    assert not (s.checkout / 'new-area/report.py').exists()
    root_file = s.invoke(s.request(items=[item('root-file', path='report.py')]))
    assert root_file[0] == 1 and root_file[1]['diagnostics'] == [diagnostic(
        'approval_required', 'root-file', 'items:root-file:path', 'agree:product:report.py')]
    # A sibling beyond the granted prefix and a boolean are not approvals.
    sibling = s.request(items=[item('sibling', path='other-area/report.py')], authorizations=approvals)
    value = s.invoke(sibling)
    assert value[0] == 1 and value[1]['diagnostics'] == [diagnostic(
        'approval_required', 'sibling', 'items:sibling:path', 'agree:product:other-area')]
    request['authorizations'] = True
    s.assert_rejected(s.invoke(request), 'invalid_request', 'request', 'provide:request')


def owner_item(kind):
    return item(kind, purpose=kind + '-material', recipient='reviewer' if kind == 'task' else 'team',
                lifetime=kind, name=kind + '.json')


def owner_row(s, kind):
    return s.row(key=kind, status='needs_inputs', category=kind + '-material', rule=kind,
                 root=kind, versioning='ignored', owner=None, path=None, absolute_path=None,
                 reserved=False, authorization_refs=[])


def test_optional_owner_disabled_missing_and_valid_task_sprint(project, tmp_path):
    s = scene(project, tmp_path)
    s.initial()
    items = [owner_item('task'), owner_item('sprint')]
    rows = [owner_row(s, k) for k in ['sprint', 'task']]
    s.assert_result(s.invoke(s.request(items=items)), status='needs_inputs', rows=rows,
                    diagnostics=[diagnostic('owner_disabled', k, 'items:' + k + ':owner',
                                            'enable:owner_provider') for k in ['sprint', 'task']])
    s.policy_data['owner_provider'] = 'poise'
    s.save_policy()
    s.assert_result(s.invoke(s.request(items=items)), status='needs_inputs', rows=rows,
                    diagnostics=[diagnostic('owner_unavailable', k, 'items:' + k + ':owner',
                                            'provide:' + k + '_owner') for k in ['sprint', 'task']])
    # Establish a real isolated caller/session and published Sprint through their public owners.
    caller = s.work()
    from tests.sprints.helpers import setup, task, draft, publish, bootstrap
    from tests.conftest import WorkPoise
    from poise.application.work import WorkTools
    setup(project)
    runtime = WorkPoise(s.config, caller['session'])
    tools = WorkTools(runtime)
    planned = draft(tools, [task(project, 'A'), task(project, 'B')],
                    [{'predecessor': 'A', 'successor': 'B', 'kind': 'result'}])
    publish(tools, planned['revision'])
    context = bootstrap(tools, 'A')
    before_task = deepcopy(runtime.task_queries.record('A'))
    before_sprint = deepcopy(runtime.sprint_tools.overview(None))
    state_home = project['root'] / project['cfg']['paths']['state']
    sprint_home = state_home / project['cfg']['paths']['sprints'] / 'S'
    expected = []
    for kind, identifier, home in [('sprint', 'S', sprint_home), ('task', 'A', sprint_home / 'task/A')]:
        expected.append(s.row(key=kind, category=kind + '-material', rule=kind, root=kind,
                              versioning='ignored', owner={'kind': kind, 'id': identifier},
                              path='artifacts/' + kind + '.json',
                              absolute_path=str(home / 'artifacts' / (kind + '.json')),
                              authorization_refs=['current-' + kind + '-owner']))
    s.assert_result(s.invoke(s.request(items=items)), rows=expected)
    assert runtime.task_queries.record('A') == before_task
    assert runtime.sprint_tools.overview(None) == before_sprint
    assert all(not Path(v['absolute_path']).exists() for v in expected)
    assert context['task'] == 'A'


def test_forged_or_foreign_owner_context_is_diagnosed(project, tmp_path):
    s = scene(project, tmp_path)
    before = s.initial()
    request = s.request(items=[owner_item('task')])
    request['task_root'] = str(tmp_path / 'forged')
    s.assert_rejected(s.invoke(request), 'invalid_request', 'request', 'provide:request')
    request.pop('task_root')
    request['items'][0]['owner'] = {'kind': 'task', 'id': 'foreign'}
    s.assert_rejected(s.invoke(request), 'invalid_request', 'request', 'provide:request')
    s.policy_data['owner_provider'] = 'poise'
    s.save_policy()
    caller = s.work()
    from tests.conftest import WorkPoise
    runtime = WorkPoise(s.config, caller['session'])
    runtime.bootstrap(deepcopy(project['task']))
    task_before = deepcopy(runtime.task_queries.record('T1'))
    # A different valid binding has no right to a merely supplied existing Task path.
    other = tmp_path / 'foreign-caller.json'
    write_json(other, {'schema': 'poise-caller-binding-1', 'caller_id': 'other-placement-caller'})
    request = s.request(items=[owner_item('task')])
    code, value = s.invoke(request, env={**s.env, 'POISE_CALLER_BINDING': str(other)})
    assert code == 1
    assert value['items'] == [owner_row(s, 'task')]
    assert value['diagnostics'] == [diagnostic('owner_unavailable', 'task', 'items:task:owner', 'provide:task_owner')]
    assert s.state.read_bytes() == before
    assert runtime.task_queries.record('T1') == task_before


def test_rule_priority_deduplication_and_order(project, tmp_path):
    s = scene(project, tmp_path)
    s.initial()
    rule = deepcopy(s.policy_data['rules'][0])
    rule.update(id='preferred-source', priority=20, category='preferred', directory='src/preferred')
    s.policy_data['rules'].insert(0, rule)
    s.save_policy()
    items = [item('z', name='z.py'), item('a', name='a.py')]
    rows = [s.row(key=k, category='preferred', rule='preferred-source', path='src/preferred/' + k + '.py',
                  absolute_path=str(s.checkout / 'src/preferred' / (k + '.py'))) for k in ['a', 'z']]
    s.assert_result(s.invoke(s.request(items=items)), rows=rows)
    state = s.state.read_bytes()
    s.policy_data['rules'].reverse()
    s.save_policy()
    s.assert_result(s.invoke(s.request(items=list(reversed(items)))), rows=rows)
    assert s.state.read_bytes() == state
    # Identical scoped authority records dedupe; overlapping paths do not duplicate refs.
    approval = s.fixture('authorizations.json')[0]
    request = s.request(items=[item('new', path='new-area/report.py')], authorizations=[approval, approval])
    result = s.invoke(request)
    assert result[0] == 0
    assert result[1]['items'][0]['authorization_refs'] == ['configured-product', 'human:new-area-for-product-report']
    assert s.read()['authorizations'] == [approval]


def test_malformed_missing_and_ambiguous_input_diagnostics(project, tmp_path):
    s = scene(project, tmp_path)
    before = s.initial()
    # Prove the entry point first, then exercise rejection before state creation.
    s.state.unlink()
    for case in s.fixture('invalid-request-boundaries.json'):
        s.assert_rejected(s.invoke(raw=canonical(case['request'])),
                          case['code'], case['field'], case['action'])
        assert not s.state.exists(), case['id']
    cfg = json.loads(s.config.read_bytes())
    bounded = deepcopy(cfg)
    boundary = s.fixture('batch-boundaries.template.json')
    bounded['batch']['max_items'] = boundary['max_items']
    write_json(s.config, bounded)
    s.assert_rejected(s.invoke(boundary['overflow_request']), 'invalid_request', 'request', 'provide:request')
    assert not s.state.exists()
    value = s.assert_result(s.invoke(boundary['boundary_request']), rows=boundary['expected_rows'])
    expected = canonical(boundary['expected_state'])
    assert s.state.read_bytes() == expected
    s.assert_effect(value, None, expected, 'committed', 'confirmed')
    assert len(s.read()['reservations']) == 2
    write_json(s.config, cfg)
    s.seed(s.fixture('state-source.template.json'))
    malformed = [None, {}, {'operation': 'prepare'}, {**s.request(), 'extra': 1},
                 s.request(items=[]), s.request(items=[item(), item()])]
    for field in ['key', 'purpose', 'recipient', 'lifetime', 'name', 'path']:
        request = s.request()
        request['items'][0].pop(field)
        malformed.append(request)
    malformed.append(s.request(operation='unrecognized'))
    for request in malformed:
        raw = canonical(request)
        s.assert_rejected(s.invoke(raw=raw), 'invalid_request', 'request', 'provide:request')
    for bad in ['../report.py', '/report.py', 'src/../report.py', 'src\\report.py', 'src/*.py', 'src/report.py/']:
        s.assert_rejected(s.invoke(s.request(items=[item(path=bad)])), 'invalid_paths', 'items', 'provide:paths')
    for bad in ['', '.', '..', 'dir/report.py', '*.py', 'bad\x00.py']:
        s.assert_rejected(s.invoke(s.request(items=[item(name=bad)])), 'invalid_paths', 'items', 'provide:paths')
    check = s.request(operation='check')
    check['items'][0]['path'] = None
    s.assert_rejected(s.invoke(check), 'invalid_paths', 'items', 'provide:paths')
    s.assert_rejected(s.invoke(raw=b'{"operation":"prepare","operation":"check"}'),
                      'invalid_request', 'request', 'provide:request')
    duplicate = deepcopy(s.policy_data['rules'][0])
    duplicate['id'] = 'also-source'
    s.policy_data['rules'].append(duplicate)
    s.save_policy()
    result = s.invoke()
    assert result[0] == 1 and result[1]['diagnostics'] == [diagnostic(
        'ambiguous_rules', 'source-report', 'items:source-report', 'configure:policy')]
    s.policy_data['rules'].pop()
    s.save_policy()
    result = s.invoke(s.request(items=[item(purpose='unconfigured')]))
    assert result[0] == 1
    assert result[1]['items'] == [s.row(status='needs_inputs', category=None, rule=None, root=None,
                                      versioning=None, owner=None, path=None, absolute_path=None,
                                      reserved=False, authorization_refs=[])]
    assert result[1]['diagnostics'] == [diagnostic('unmapped_intent', 'source-report', 'items:source-report', 'configure:policy')]
    assert s.state.read_bytes() == before


def test_atomic_batch_repeat_and_conflicting_declarations(project, tmp_path, monkeypatch):
    s = scene(project, tmp_path)
    before = s.initial()
    request = s.request(items=[item('good', name='good.py'), item('bad', name='bad.py', path='new/bad.py')])
    code, value = s.invoke(request)
    assert code == 1 and value['status'] == 'needs_agreement'
    assert all(not v['reserved'] for v in value['items'])
    assert s.state.read_bytes() == before
    s.state.unlink()
    result = s.invoke(request)
    assert result[0] == 1 and result[1]['status'] == 'needs_agreement'
    empty = canonical(s.fixture('state-empty.template.json'))
    assert s.state.read_bytes() == empty  # The initial baseline is an explicit reported effect.
    assert result[1]['state']['after_sha256'] == digest(empty)
    assert result[1]['state']['effect'] == 'committed'
    s.seed(s.fixture('state-source.template.json'))
    # Key reuse cannot change original intent or location.
    result = s.invoke(s.request(items=[item(path='src/moved/report.py')]))
    assert result[0] == 1 and result[1]['diagnostics'] == [diagnostic(
        'reservation_conflict', 'source-report', 'items:source-report', 'choose:new_key')]
    # Identical semantic declarations may share a destination; different intent cannot.
    s.assert_result(s.invoke(s.request(items=[item('alias')])), rows=[s.row(key='alias')])
    conflicting = s.request(items=[item('different', purpose='documentation', path='src/report.py')])
    result = s.invoke(conflicting)
    assert result[0] == 1 and result[1]['diagnostics'] == [diagnostic(
        'destination_conflict', 'different', 'items:different:path', 'choose:placement')]
    # Explicit independent fixtures model current extra Y, with no historical discriminator.
    retry = s.fixture('retry.template.json')['valid-extra']
    s.seed(retry['before'])
    prior = s.state.read_bytes()
    value = s.assert_result(s.invoke(retry['request']))
    after = canonical(retry['expected_after'])
    assert s.state.read_bytes() == after
    s.assert_effect(value, prior, after, 'committed', 'confirmed')
    assert retry['preserved_record'] in s.read()['reservations']
    def no_replace(*args, **kwargs):
        pytest.fail('Identical X+Y current image must not be replaced')
    with monkeypatch.context() as guards:
        guards.setattr(os, 'replace', no_replace)
        repeated = s.assert_result(s.transport(guards, retry['request']))
    s.assert_effect(repeated, after, after, 'unchanged', 'confirmed')
    assert s.read() == retry['already_present_expected_after']


def test_current_policy_and_new_root_recheck(project, tmp_path, monkeypatch):
    s = scene(project, tmp_path)
    before = s.initial()
    for case in s.fixture('component-glob-cases.template.json'):
        s.policy_data['rules'][0]['paths'] = [case['pattern']]
        s.save_policy()
        s.seed(s.fixture('state-source.template.json'))
        request = deepcopy(case['request'])
        request['operation'] = 'check'
        value = s.assert_result(s.invoke(request), status=case['status'], rows=[case['row']],
                                diagnostics=case['diagnostics'])
        s.assert_effect(value, before, None, 'none', 'not_applicable')
        assert s.state.read_bytes() == before, case['id']
        # The same literal path must have the same admission under prepare.
        allowed = case['status'] == 'ready'
        value = s.assert_result(s.invoke(case['request']), status=case['status'],
                                rows=[{**case['row'], 'reserved': allowed}],
                                diagnostics=case['diagnostics'])
        expected = canonical(case['expected_prepare_state'])
        assert s.state.read_bytes() == expected, case['id']
        if not allowed:
            assert expected == before
    s.seed(s.fixture('state-source.template.json'))
    s.policy_data['rules'][0]['paths'] = ['src/allowed/**']
    s.save_policy()
    value = s.assert_result(s.invoke(s.request(operation='check')), status='needs_inputs',
                            rows=[s.row(status='denied', reserved=False)],
                            diagnostics=[diagnostic('path_not_allowed', 'source-report', 'items:source-report:path', 'choose:placement')])
    s.assert_effect(value, before, None, 'none', 'not_applicable')
    refresh = s.fixture('retry.template.json')['metadata-refresh']
    s.policy_data = refresh['policy']
    s.save_policy()
    replacements = []
    original = os.replace
    def observe(source, target, *args, **kwargs):
        if Path(target) == s.state:
            replacements.append((source, target))
        return original(source, target, *args, **kwargs)
    monkeypatch.setattr(os, 'replace', observe)
    value = s.assert_result(s.transport(monkeypatch, refresh['request']),
                            rows=[s.row(category='audited-product-versioned', rule='source-refresh')])
    after = canonical(refresh['expected_after'])
    assert s.state.read_bytes() == after and len(replacements) == 1
    s.assert_effect(value, before, after, 'committed', 'confirmed')
    # Late existence never changes the original root baseline.
    (s.checkout / 'late').mkdir()
    result = s.invoke(s.request(items=[item('late', path='late/report.py')]))
    assert result[0] == 1 and result[1]['diagnostics'] == [diagnostic(
        'approval_required', 'late', 'items:late:path', 'agree:product:late')]


def test_placement_does_not_publish_product_or_change_task_lifecycle(project, tmp_path):
    s = scene(project, tmp_path)
    git_before = subprocess.check_output(['git', '-C', str(s.checkout), 'status', '--porcelain'])
    head = subprocess.check_output(['git', '-C', str(s.checkout), 'rev-parse', 'HEAD'])
    s.initial()
    assert subprocess.check_output(['git', '-C', str(s.checkout), 'status', '--porcelain']) == git_before
    assert subprocess.check_output(['git', '-C', str(s.checkout), 'rev-parse', 'HEAD']) == head
    assert not (s.checkout / 'src/report.py').exists()
    caller = s.work()
    from tests.conftest import WorkPoise
    runtime = WorkPoise(s.config, caller['session'])
    assert runtime.current_task() is None and runtime.task_queries.summary() == []
    assert runtime.sprint_tools.overview(None) is None
    value = s.assert_result(s.invoke(s.request(operation='check')))
    assert value['state']['confirmation'] == 'not_applicable'
    assert runtime.current_task() is None and runtime.task_queries.summary() == []


def test_lock_failure_preserves_state(project, tmp_path):
    s = scene(project, tmp_path)
    before = s.initial()
    cfg = json.loads(s.config.read_bytes())
    cfg['limits'].update(lock_seconds=0.03, lock_poll_seconds=0.005)
    write_json(s.config, cfg)
    with s.state.with_name('placement.lock').open('a+b') as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        value = s.assert_result(s.invoke(pending(s, 'pending')), status='needs_inputs',
                                rows=[s.row(key='pending', path='src/next.py',
                                            absolute_path=str(s.checkout / 'src/next.py'), reserved=False)],
                                diagnostics=[diagnostic('state_lock', None, 'state', 'retry:prepare')])
        assert value['state'] == s.fixture('state-observation.template.json')
    assert s.state.read_bytes() == before


def test_policy_and_state_read_failures_preserve_truth(project, tmp_path, monkeypatch):
    s = scene(project, tmp_path)
    before = s.initial()
    policy = s.policy.read_bytes()
    s.policy.unlink()
    s.assert_rejected(s.invoke(), 'unreadable_policy', 'policy', 'restore:policy')
    assert s.state.read_bytes() == before
    s.policy.write_bytes(policy)
    original = Path.read_bytes
    def unreadable(path):
        if path == s.state:
            raise PermissionError('controlled state read failure')
        return original(path)
    with monkeypatch.context() as faults:
        faults.setattr(Path, 'read_bytes', unreadable)
        code, value = s.transport(faults)
        assert code == 1 and value['diagnostics'] == [diagnostic('state_read', None, 'state', 'inspect:state')]
        assert value['state']['observed_sha256'] is None
        assert original(s.state) == before
    s.state.write_bytes(b'{invalid-state\n')
    code, value = s.invoke()
    assert code == 1 and value['diagnostics'] == [diagnostic('invalid_state', None, 'state', 'inspect:state')]
    assert s.state.read_bytes() == b'{invalid-state\n'
    foreign = s.fixture('state-source.template.json')
    foreign['caller_key'] = 'foreign-caller-key'
    s.seed(foreign)
    foreign_bytes = s.state.read_bytes()
    code, value = s.invoke()
    assert code == 1 and value['diagnostics'] == [diagnostic('invalid_state', None, 'state', 'inspect:state')]
    assert s.state.read_bytes() == foreign_bytes
    for case in s.fixture('invalid-state-boundaries.template.json'):
        state = s.fixture('state-source.template.json')
        target = state
        for key in case['path'][:-1]:
            target = target[key]
        key = case['path'][-1]
        if case['operation'] == 'remove':
            target.pop(key)
        else:
            target[key] = case['value']
        s.seed(state)
        corrupted = canonical(state)
        value = s.assert_result(s.invoke(), status='needs_inputs', rows=[s.row(reserved=False)],
                                diagnostics=[diagnostic('invalid_state', None, 'state', 'inspect:state')])
        assert value['state']['after_sha256'] is None and value['state']['effect'] == 'none'
        assert s.state.read_bytes() == corrupted, case['id']


def next_expected(s):
    expected = s.fixture('state-source.template.json')
    record = deepcopy(expected['reservations'][0])
    record.update(key='next', name='next.py', path='src/next.py', absolute_path=str(s.checkout / 'src/next.py'))
    expected['reservations'].insert(0, record)
    return canonical(expected)


def test_atomic_replace_failure_observes_before_state(project, tmp_path, monkeypatch):
    s = scene(project, tmp_path)
    before = s.initial()
    after = next_expected(s)
    original = os.replace
    def fail(source, target, *args, **kwargs):
        if Path(target) == s.state:
            raise OSError('controlled before replace failure')
        return original(source, target, *args, **kwargs)
    monkeypatch.setattr(os, 'replace', fail)
    value = s.assert_result(s.transport(monkeypatch, pending(s, 'next')), status='needs_inputs',
                            rows=[s.row(key='next', path='src/next.py', absolute_path=str(s.checkout / 'src/next.py'), reserved=False)],
                            diagnostics=[diagnostic('state_write', None, 'state', 'retry:prepare')])
    s.assert_effect(value, before, after, 'unconfirmed', 'unconfirmed')
    assert s.state.read_bytes() == before


def fail_directory_fsync(s, monkeypatch):
    replaced = []
    failed = []
    original_replace, original_fsync = os.replace, os.fsync
    def replace(source, target, *args, **kwargs):
        result = original_replace(source, target, *args, **kwargs)
        if Path(target) == s.state:
            replaced.append(target)
        return result
    def fsync(fd):
        if replaced and not failed and stat.S_ISDIR(os.fstat(fd).st_mode):
            failed.append(True)
            raise OSError('controlled directory fsync after real replace')
        return original_fsync(fd)
    monkeypatch.setattr(os, 'replace', replace)
    monkeypatch.setattr(os, 'fsync', fsync)
    return replaced


def test_directory_fsync_failure_observes_after_and_retries_without_replace(project, tmp_path, monkeypatch):
    s = scene(project, tmp_path)
    before = s.initial()
    after = next_expected(s)
    request = pending(s, 'next')
    with monkeypatch.context() as faults:
        replaced = fail_directory_fsync(s, faults)
        code, value = s.transport(faults, request)
        assert code == 1 and len(replaced) == 1
        assert value['diagnostics'] == [diagnostic('state_write', None, 'state', 'retry:prepare')]
        assert value['items'][0]['reserved'] is False
        s.assert_effect(value, before, after, 'unconfirmed', 'unconfirmed')
        assert s.state.read_bytes() == after
    observed_modes = []
    original_fsync = os.fsync
    def forbidden_replace(*args, **kwargs):
        pytest.fail('Full intended canonical image equals current bytes: repeat replace is forbidden')
    def observe_fsync(fd):
        observed_modes.append(os.fstat(fd).st_mode)
        return original_fsync(fd)
    with monkeypatch.context() as guards:
        guards.setattr(os, 'replace', forbidden_replace)
        guards.setattr(os, 'fsync', observe_fsync)
        value = s.assert_result(s.transport(guards, request), rows=[s.row(
            key='next', path='src/next.py', absolute_path=str(s.checkout / 'src/next.py'))])
        s.assert_effect(value, after, after, 'unchanged', 'confirmed')
    assert any(stat.S_ISREG(v) for v in observed_modes) and any(stat.S_ISDIR(v) for v in observed_modes)
    assert s.state.read_bytes() == after


def test_postfailure_read_uncertainty_has_verifiable_next_action(project, tmp_path, monkeypatch):
    s = scene(project, tmp_path)
    before = s.initial()
    after = next_expected(s)
    original_read = Path.read_bytes
    request = pending(s, 'next')
    with monkeypatch.context() as faults:
        replaced = fail_directory_fsync(s, faults)
        def unreadable_after(path):
            if path == s.state and replaced:
                raise PermissionError('controlled postfailure read uncertainty')
            return original_read(path)
        faults.setattr(Path, 'read_bytes', unreadable_after)
        code, value = s.transport(faults, request)
        assert code == 1
        expected = s.fixture('state-observation.template.json')
        expected.update(before_sha256=digest(before), after_sha256=digest(after),
                        effect='unconfirmed', confirmation='unconfirmed')
        assert value['state'] == expected
        assert value['diagnostics'] == [diagnostic('state_write', None, 'state', 'retry:prepare')]
        assert original_read(s.state) == after
    check = s.request(operation='check', items=request['items'])
    value = s.assert_result(s.invoke(check), rows=[s.row(key='next', path='src/next.py', absolute_path=str(s.checkout / 'src/next.py'))])
    s.assert_effect(value, after, None, 'none', 'not_applicable')
    value = s.assert_result(s.invoke(request), rows=[s.row(key='next', path='src/next.py', absolute_path=str(s.checkout / 'src/next.py'))])
    s.assert_effect(value, after, after, 'unchanged', 'confirmed')


def test_caller_binding_and_checkout_namespace_isolation(project, tmp_path):
    s = scene(project, tmp_path)
    before = s.initial()
    other_binding = tmp_path / 'other-binding.json'
    write_json(other_binding, {'schema': 'poise-caller-binding-1', 'caller_id': 'other-placement-caller'})
    env = {**s.env, 'POISE_CALLER_BINDING': str(other_binding)}
    code, value = s.invoke(env=env)
    assert code == 0
    assert value['state']['path'] != str(s.state)
    other_state = Path(value['state']['path'])
    assert other_state.read_bytes() != before  # caller_key is a semantic persisted boundary.
    assert s.state.read_bytes() == before
    linked = tmp_path / 'linked-checkout'
    subprocess.run(['git', '-C', str(s.checkout), 'worktree', 'add', '--detach', str(linked)],
                   check=True, capture_output=True)
    code, value = s.invoke(checkout=linked)
    assert code == 0 and value['checkout'] == str(linked)
    assert value['state']['path'] not in [str(s.state), str(other_state)]
    assert s.state.read_bytes() == before
    unrelated = tmp_path / 'unrelated'
    unrelated.mkdir()
    subprocess.run(['git', '-C', str(unrelated), 'init', '-b', 'main'], check=True, capture_output=True)
    s.assert_rejected(s.invoke(checkout=unrelated), 'project_binding', 'checkout', 'select:checkout')
    no_identity = {k: v for k, v in s.env.items() if k != 'POISE_CALLER_BINDING'}
    s.assert_rejected(s.invoke(env=no_identity), 'invalid_caller', 'caller', 'provide:caller')


def test_configuration_extensions_and_explicit_required_fields(project, tmp_path):
    s = scene(project, tmp_path)
    before = s.initial()
    original = deepcopy(s.policy_data)
    s.state.unlink()
    for case in s.fixture('invalid-policy-boundaries.template.json'):
        s.policy_data = deepcopy(original)
        target = s.policy_data
        for key in case['path'][:-1]:
            target = target[key]
        target[case['path'][-1]] = case['value']
        s.save_policy()
        s.assert_rejected(s.invoke(), 'invalid_policy', 'policy', 'correct:policy')
        assert not s.state.exists(), case['id']
    s.policy_data = original
    s.save_policy()
    s.seed(s.fixture('state-source.template.json'))
    s.policy_data['future-extension'] = {'opaque': 'preserve this raw value'}
    s.policy_data['roots'][0]['future-root-option'] = 'retained'
    s.save_policy()
    raw_policy = s.policy.read_bytes()
    s.assert_result(s.invoke())
    assert s.policy.read_bytes() == raw_policy and s.state.read_bytes() == before
    valid = deepcopy(s.policy_data)
    for field in ['schema', 'version', 'roots', 'rules', 'owner_provider', 'state']:
        s.policy_data = deepcopy(valid)
        s.policy_data.pop(field)
        s.save_policy()
        s.assert_rejected(s.invoke(), 'invalid_policy', 'policy', 'correct:policy')
        assert s.state.read_bytes() == before
    s.policy_data = deepcopy(valid)
    s.policy_data['rules'][0]['priority'] = True
    s.save_policy()
    s.assert_rejected(s.invoke(), 'invalid_policy', 'policy', 'correct:policy')
    s.policy_data = deepcopy(valid)
    s.save_policy()
    approval = s.fixture('authorizations.json')[0]
    conflicting = deepcopy(approval)
    conflicting['purposes'] = ['documentation']
    s.assert_rejected(s.invoke(s.request(authorizations=[approval, conflicting])),
                      'invalid_request', 'request', 'provide:request')
    assert s.state.read_bytes() == before
