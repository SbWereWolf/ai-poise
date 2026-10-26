"""Selected-result contracts through actual WorkTools/CLI, SQLite and exact bytes.

Fixture participants prove gates, not independent engineering review or savings.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from conftest import WorkPoise, add_test
from poise.application.work import WorkTools
from poise.common import PoiseError
from poise.infrastructure.artifact_factory import FileArtifactFactory
from tests.batch.helpers import request
from tests.batch.result_selection_support import (
    assert_refused, assert_saved, case_factory, fixture, selected,
)


def test_selected_sections_and_files_match_explicit_verify(case_factory):
    old = case_factory()
    chosen = case_factory()
    # The old control MUST succeed before reaching the unsupported new form.
    assert_saved(old, old.tools.invoke(old.old()))
    assert_saved(chosen, chosen.tools.invoke(chosen.selected()))
    assert chosen.saved()['sections'] == fixture('old-payload.json')['sections']
    assert chosen.target.read_bytes() == old.target.read_bytes()


def test_old_path_matches_independent_oracle(case_factory):
    case = case_factory()
    assert_saved(case, case.tools.invoke(case.old()))


def test_mixed_metadata_retains_explicit_decisions(case_factory):
    case = case_factory()
    packet = case.selected()
    metadata = fixture('mixed-metadata.json')
    packet['input']['selection']['metadata'] = metadata
    result = case.tools.invoke(packet)
    assert result['status'] == 'verified'
    saved = case.saved()
    for key in ('commit_message', 'content_additions', 'trace'):
        assert saved[key] == metadata[key]
    assert saved['sections'] == fixture('old-payload.json')['sections']
    assert case.target.read_bytes() == bytes.fromhex('00ff70726f6f66800a')
    content = case.tools.invoke(request('show', {'queries': [{'id': 'content', 'kind': 'content'}]}))['results'][0]['value']
    assert content['trace']['review']['choice']['decision'] == 'keep'
    assert content['trace']['review']['choice']['reason'] == 'Явное решение агента.'


def test_mixed_evidence_work_retains_the_agents_argument(case_factory):
    from tests.evidence.file_proof_support import Scenario
    scenario = Scenario(case_factory(raw=True))
    scenario.write('A')
    argument = scenario.argument('A')
    packet = selected(scenario.context)
    packet['input']['selection']['files'] = []
    packet['input']['selection']['metadata']['evidence_work'] = {
        'phase': 'prepare', 'arguments': [argument], 'decisions': []}
    result = scenario.tools.invoke(packet)
    assert result['status'] == 'verified'
    actual = scenario.show()['arguments']
    assert len(actual) == 1
    for name in ('id', 'kind', 'claim', 'path', 'covers', 'verdict'):
        assert actual[0][name] == argument[name]
    assert scenario.show()['decisions'] == []


def test_mixed_stage_work_retains_explicit_criteria_proposal(case_factory):
    from tests.runtime_services.stage_criteria_helpers import boot, proposal, show, work
    tools, context = boot(case_factory(raw=True))
    offered = proposal(tools, requirement='Keep the selected proof bytes unchanged.')
    packet = selected(context)
    packet['input']['selection']['files'] = []
    packet['input']['selection']['metadata']['stage_work'] = {
        'criteria_work': work(tools, 'selected-criteria-1', proposals=[offered])}
    assert tools.invoke(packet)['status'] == 'verified'
    current = show(tools)
    assert len(current['proposals']) == 1
    assert current['proposals'][0]['criteria'] == offered['criteria']
    assert current['proposals'][0]['decision'] is None
    assert current['execution']['bound_revision'] is None


def test_mixed_registry_work_retains_explicit_change(case_factory):
    from tests.verification.test_current_registry_mutation import configure_public_registry_case, public_tools
    project = case_factory(raw=True)
    configure_public_registry_case(project)
    tools = public_tools(project, 'selected-registry-author')
    context = tools.invoke(request('bootstrap', {
        'task': project['task'], 'decision': None, 'feedback': None, 'rework_stage': None}))
    add_test(context['worktree'])
    packet = selected(context)
    packet['input']['selection']['files'] = []
    change = fixture('registry-change.json')
    packet['input']['selection']['metadata']['method_additions'] = change
    assert tools.invoke(packet)['status'] == 'verified'
    registry = tools.invoke(request('show', {'queries': [
        {'id': 'registry', 'kind': 'verification_registry'}]}))['results'][0]['value']
    assert registry['revision'] == 1
    assert 'CLEANUP_FULL_GREEN' not in [entry['method']['id'] for entry in registry['current']]
    assert registry['executable_obligations'] == ['requirements[0]', 'definition_of_done[0]']


@pytest.mark.parametrize('root', ['task', 'worktree', 'runtime'])
def test_selected_current_roots_preserve_binary_bytes(case_factory, root):
    case = case_factory()
    packet = case.selected()
    owner = {'task': case.root, 'worktree': Path(case.context['worktree']),
             'runtime': Path(case.context['runtime_root'])}[root]
    relative = 'src/selected-proof.bin' if root == 'worktree' else 'inputs/input.bin'
    source = owner / relative
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(bytes.fromhex('00ff70726f6f66800a'))
    packet['input']['selection']['files'][0]['source'] = {'root': root, 'path': relative}
    result = case.tools.invoke(packet)
    assert_saved(case, result)
    if root == 'runtime':
        assert not source.exists()
        assert case.tools.invoke(packet)['replayed'] is True


@pytest.mark.parametrize('spec', fixture('source-cases.json'))
def test_selected_roots_reject_foreign_and_missing_sources(case_factory, spec):
    case = case_factory()
    packet = case.selected()
    packet['input']['selection']['files'][0]['source'] = {
        'root': spec['root'], 'path': spec['path']}
    reason = assert_refused(case, packet, spec['code'])
    assert spec['path'] in reason


def test_existing_generic_external_file_source_is_preserved(case_factory, tmp_path):
    case = case_factory()
    external = tmp_path / 'outside-accepted.bin'
    external.write_bytes(bytes.fromhex('00ff70726f6f66800a'))
    old = case.old()
    old['input']['artifacts'][0]['source']['path'] = str(external)
    assert_saved(case, case.tools.invoke(old))


@pytest.mark.parametrize('fault', ['bytes', 'missing'])
def test_selection_detects_source_change_before_publication(case_factory, monkeypatch, fault):
    case = case_factory()
    later = case.root / 'inputs/later.bin'
    later.write_bytes(b'later original')
    packet = case.selected()
    second = deepcopy(packet['input']['selection']['files'][0])
    second['path'] = 'proofs/later.bin'
    second['source']['path'] = 'inputs/later.bin'
    packet['input']['selection']['files'].append(second)
    original = FileArtifactFactory.materialize
    reached = []

    def change_after_snapshot(factory, prepared):
        if any(item.source is not None and item.source.path == str(later) for item in prepared):
            reached.append(True)
            if fault == 'bytes':
                later.write_bytes(b'changed after snapshot')
            else:
                later.unlink()
        return original(factory, prepared)

    monkeypatch.setattr(FileArtifactFactory, 'materialize', change_after_snapshot)
    reason = assert_refused(case, packet, 'selection_source_changed')
    assert reached == [True]
    assert str(later) in reason
    assert not (case.root / 'artifacts/proofs/later.bin').exists()


@pytest.mark.parametrize('spec', fixture('negative-cases.json'))
def test_selection_requires_current_state_and_required_sections(case_factory, spec):
    case = case_factory()
    packet = case.selected()
    value = packet['input']['selection']
    for key in spec['path'][:-1]:
        value = value[key]
    value[spec['path'][-1]] = deepcopy(spec['value'])
    assert_refused(case, packet, spec['code'])


def test_exact_selection_replay_preserves_saved_result(case_factory, monkeypatch):
    case = case_factory()
    packet = case.selected()
    first = case.tools.invoke(packet)
    assert_saved(case, first)
    before = case.snapshot()
    case.source.unlink()
    monkeypatch.setattr(case.tools.runtime, '_execute_checks',
                        lambda *a, **kw: pytest.fail('completed replay reran checks'))
    monkeypatch.setattr(case.tools.runtime.work_resources, 'factory',
                        lambda: pytest.fail('completed replay reopened or republished source'))
    repeated = case.tools.invoke(deepcopy(packet))
    assert repeated['replayed'] is True
    assert repeated['checks'] == first['checks']
    assert repeated['verified_tree'] == first['verified_tree']
    assert case.snapshot() == before
    assert case.target.read_bytes() == bytes.fromhex('00ff70726f6f66800a')


def test_selection_exact_replay_after_ordinary_own_release(case_factory):
    case = case_factory()
    packet = case.selected()
    first = case.tools.invoke(packet)
    assert case.release()['after']['task_id'] is None
    with pytest.raises(PoiseError, match='selection_ownership_required'):
        case.tools.invoke(packet)
    case.reopen()
    again = case.tools.invoke(packet)
    assert again['replayed'] is True and again['commit'] == first['commit']


@pytest.mark.parametrize('mutation', ['content', 'version', 'metadata', 'destination', 'source'])
def test_selection_conflicting_request_preserves_original(case_factory, mutation):
    case = case_factory()
    original = case.selected()
    first = case.tools.invoke(original)
    before = case.snapshot()
    changed = deepcopy(original)
    choice = changed['input']['selection']
    if mutation == 'content':
        choice['sections']['report'] = 'A different substantive claim.'
    elif mutation == 'version':
        choice['expected']['version'] += 1
    elif mutation == 'metadata':
        choice['metadata']['commit_message'] = 'A different explicit intention'
    elif mutation == 'destination':
        choice['files'][0]['path'] = 'proofs/different.bin'
    else:
        choice['files'][0]['source']['path'] = 'inputs/different.bin'
    with pytest.raises(PoiseError, match='selection_request_conflict'):
        case.tools.invoke(changed)
    assert case.snapshot() == before
    assert case.tools.invoke(original)['commit'] == first['commit']
    assert case.target.read_bytes() == bytes.fromhex('00ff70726f6f66800a')


def test_selection_retry_resolves_saved_attempt_without_rerunning_checks(case_factory, monkeypatch):
    case = case_factory(checked=True)
    case.allow.write_text('allow')
    packet = case.selected()
    original = case.tools.runtime.runner.verified

    def fail_delivery(*args, **kwargs):
        raise PoiseError('injected selection delivery interruption')

    monkeypatch.setattr(case.tools.runtime.runner, 'verified', fail_delivery)
    with pytest.raises(PoiseError, match='injected selection delivery interruption'):
        case.tools.invoke(packet)
    assert case.runs.read_text().count('run') == 1
    assert case.tools.runtime.current_task()['status'] == 'active'
    monkeypatch.setattr(case.tools.runtime.runner, 'verified', original)
    monkeypatch.setattr(case.tools.runtime, '_execute_checks',
                        lambda *a, **kw: pytest.fail('exact incomplete retry reran terminal check'))
    case.source.unlink()
    result = case.tools.invoke(packet)
    assert_saved(case, result)
    assert case.runs.read_text().count('run') == 1


def test_selection_unknown_attempt_requires_owning_recovery(case_factory, monkeypatch):
    case = case_factory(checked=True)
    packet = case.selected()
    called = []

    def unknown(*args, **kwargs):
        called.append(True)
        raise PoiseError('external outcome remains unknown')

    monkeypatch.setattr(case.tools.runtime, '_execute_checks', unknown)
    with pytest.raises(PoiseError, match='external outcome remains unknown'):
        case.tools.invoke(packet)
    before = case.snapshot()
    with pytest.raises(PoiseError, match='selection_recovery_pending'):
        case.tools.invoke(packet)
    assert called == [True]
    assert case.snapshot() == before


def test_selection_respects_failing_checks(case_factory):
    case = case_factory(checked=True)
    result = case.tools.invoke(case.selected())
    assert result['status'] == 'checks_failed'
    assert case.tools.runtime.current_task()['status'] == 'active'
    assert len(result['checks']) == 1
    assert result['checks'][0]['actual_exit_code'] == 1
    assert result['checks'][0]['passed'] is False
    assert case.runs.read_text().count('run') == 1
    assert result['continuation']['kind'] == 'continue_work'


def test_selection_respects_independent_review_and_foreign_claim(case_factory):
    case = case_factory()
    packet = case.selected()
    assert case.tools.invoke(packet)['status'] == 'verified'
    progression = request('advance', {'request_id': 'selection-review', 'task_id': 'T1'})
    handoff = case.tools.invoke(progression)
    assert handoff['status'] == 'role_handoff_required'
    assert handoff['next_stage'] == 'audit'
    case.release()
    case.reopen('selection-reviewer')
    before = case.snapshot()
    with pytest.raises(PoiseError, match='selection_ownership_required|selection_stale'):
        case.tools.invoke(packet)
    assert case.snapshot() == before
    entered = case.tools.invoke(progression)
    assert entered['stage'] == 'audit'
    context = case.tools.invoke(request('bootstrap', {
        'task': None, 'decision': None, 'feedback': None, 'rework_stage': None}))
    review = selected(context)
    review['input']['selection']['files'] = []
    review['input']['selection']['metadata']['stage_work'] = fixture('oracles.json')['inspection_work']
    result = case.tools.invoke(review)
    assert result['status'] == 'verified' and result['stage_outcome'] == 'clear'
    assert case.tools.runtime.current_task()['status'] == 'verified'


def test_invalid_registered_artifact_blocks_new_selection_publication(case_factory):
    case = case_factory()
    created = case.tools.invoke(request('artifacts', {'items': [
        {'scope': 'task', 'path': 'existing.txt', 'source': {'kind': 'text', 'text': 'trusted'}}]}))
    damaged = Path(created['artifact_paths'][0])
    damaged.write_text('changed after registration')
    before = case.snapshot()
    with pytest.raises(PoiseError, match='изменён|changed|digest'):
        case.tools.invoke(case.selected())
    assert case.snapshot() == before and not case.target.exists()


@pytest.mark.parametrize('conflict', [False, True])
def test_selection_duplicate_destinations_use_existing_artifact_semantics(case_factory, conflict):
    case = case_factory()
    packet = case.selected()
    other = deepcopy(packet['input']['selection']['files'][0])
    if conflict:
        later = case.root / 'inputs/other.bin'
        later.write_bytes(b'different literal bytes')
        other['source']['path'] = 'inputs/other.bin'
    packet['input']['selection']['files'].append(other)
    before = case.snapshot()
    if conflict:
        with pytest.raises(PoiseError, match='Conflicting|conflict|different content'):
            case.tools.invoke(packet)
        assert case.snapshot() == before and not case.target.exists()
    else:
        assert_saved(case, case.tools.invoke(packet))


def test_cli_selection_keeps_full_output_and_factual_continuation(case_factory):
    from tests.batch.test_cli import call
    case = case_factory()
    case.release()
    started = call(case.project, request('bootstrap', {
        'task': {'id': 'T1'}, 'decision': None, 'feedback': None, 'rework_stage': None}))
    assert started.returncode == 0, started.stdout + started.stderr
    context = json.loads(Path(json.loads(started.stdout)['response_path']).read_text())
    packet = selected(context)
    delivered = call(case.project, packet)
    assert delivered.returncode == 0, delivered.stdout + delivered.stderr
    view = json.loads(delivered.stdout)
    assert len(delivered.stdout) <= case.project['cfg']['limits']['output_chars']
    full = json.loads(Path(view['response_path']).read_text())
    assert full['status'] == 'verified'
    assert full['selection'] == {'schema': 'stage-result-selection-1', 'request_id': 'selection-example-1'}
    assert set(full['continuation']) == {'kind', 'packet', 'instruction'}
    assert full['continuation']['kind'] == 'advance'
    next_packet = full['continuation']['packet']
    assert next_packet['operation'] == 'advance'
    assert next_packet['input'] == {'task_id': 'T1', 'request_id': 'selection-example-1-advance'}
    assert next_packet['messages'] == []
    assert full['checks'] == []
    assert any(a['path'] == str(case.target) for a in full['artifacts'])
    assert case.target.read_bytes() == bytes.fromhex('00ff70726f6f66800a')
    observed = call(case.project, request('show', {'queries': [{'id': 'task', 'kind': 'task'}]}))
    state = json.loads(Path(json.loads(observed.stdout)['response_path']).read_text())['results'][0]['value']
    assert state['status'] == 'verified' and state['stage'] == 'implementation'


def test_cli_selection_stale_refusal_has_executable_readonly_recovery(case_factory):
    from tests.batch.test_cli import call
    case = case_factory()
    case.release()
    started = call(case.project, request('bootstrap', {
        'task': {'id': 'T1'}, 'decision': None, 'feedback': None, 'rework_stage': None}))
    context = json.loads(Path(json.loads(started.stdout)['response_path']).read_text())
    packet = selected(context)
    packet['input']['selection']['expected']['version'] += 100
    rejected = call(case.project, packet)
    assert rejected.returncode == 2
    full = json.loads(Path(json.loads(rejected.stdout)['response_path']).read_text())
    assert full['status'] == 'rejected' and full['reason'].startswith('selection_stale')
    assert full['continuation']['kind'] == 'refresh_context'
    recovery = full['continuation']['packet']
    assert recovery['operation'] == 'show'
    assert recovery['input']['queries'][0]['kind'] == 'task'
    assert call(case.project, recovery).returncode == 0
    assert not case.target.exists()


def test_old_mixed_path_matches_independent_metadata_oracle(case_factory):
    case = case_factory()
    packet = case.old()
    packet['input']['result'] = fixture('old-mixed-payload.json')
    assert case.tools.invoke(packet)['status'] == 'verified'
    saved = case.saved()
    expected = fixture('old-mixed-payload.json')
    for key in ('sections', 'commit_message', 'content_additions', 'trace'):
        assert saved[key] == expected[key]


def test_cli_selection_checks_failed_keeps_real_exit_and_receipts(case_factory):
    from tests.batch.test_cli import call
    case = case_factory(checked=True)
    case.release()
    started = call(case.project, request('bootstrap', {
        'task': {'id': 'T1'}, 'decision': None, 'feedback': None, 'rework_stage': None}))
    assert started.returncode == 0
    context = json.loads(Path(json.loads(started.stdout)['response_path']).read_text())
    result = call(case.project, selected(context))
    assert result.returncode == 1
    full = json.loads(Path(json.loads(result.stdout)['response_path']).read_text())
    assert full['status'] == 'checks_failed'
    assert len(full['checks']) == 1
    assert full['checks'][0]['actual_exit_code'] == 1
    assert full['checks'][0]['passed'] is False
    assert full['continuation']['kind'] == 'continue_work'
    assert case.runs.read_text().count('run') == 1


def test_cli_large_selection_retains_every_registered_path(case_factory):
    from tests.batch.test_cli import call
    case = case_factory()
    case.release()
    started = call(case.project, request('bootstrap', {
        'task': {'id': 'T1'}, 'decision': None, 'feedback': None, 'rework_stage': None}))
    assert started.returncode == 0
    context = json.loads(Path(json.loads(started.stdout)['response_path']).read_text())
    packet = selected(context)
    example = packet['input']['selection']['files'][0]
    chosen_names = ['proofs/selected-' + str(index) + '.bin' for index in range(20)]
    packet['input']['selection']['files'] = [
        {**deepcopy(example), 'path': name} for name in chosen_names]
    result = call(case.project, packet)
    assert result.returncode == 0
    assert len(result.stdout) <= case.project['cfg']['limits']['output_chars']
    full = json.loads(Path(json.loads(result.stdout)['response_path']).read_text())
    expected_paths = {str(case.root / 'artifacts' / name) for name in chosen_names}
    actual_paths = {item['path'] for item in full['artifacts']}
    assert expected_paths <= actual_paths
    assert len([item for item in full['artifacts'] if item['path'] in expected_paths]) == 20
    for name in expected_paths:
        assert Path(name).read_bytes() == bytes.fromhex('00ff70726f6f66800a')


def test_selection_rechecks_source_until_registration(case_factory, monkeypatch):
    case = case_factory()
    packet = case.selected()
    before = case.snapshot()
    original = FileArtifactFactory.materialize
    reached_after_publication = []

    def replace_after_publication(factory, prepared):
        paths = original(factory, prepared)
        if str(case.target) in paths:
            reached_after_publication.append(case.target.exists())
            case.source.write_bytes(b'changed after publication before registration')
        return paths

    monkeypatch.setattr(FileArtifactFactory, 'materialize', replace_after_publication)
    with pytest.raises(PoiseError, match='selection_source_changed'):
        case.tools.invoke(packet)
    assert reached_after_publication == [True]
    after = case.snapshot()
    for key in ('results', 'packets', 'artifacts', 'links', 'verified_events'):
        assert after[key] == before[key]
    current = case.tools.runtime.current_task()
    assert current['status'] == 'active' and current['claimed_by'] == case.actor
    # A prepared submission/file is not registration or a verified outcome.
    assert case.target.read_bytes() == bytes.fromhex('00ff70726f6f66800a')
    with case.tools.runtime.store.transaction() as db:
        assert db.execute('SELECT COUNT(id) FROM artifacts WHERE path=?', (str(case.target),)).fetchone()[0] == 0


def test_legacy_text_template_and_file_verify_is_preserved(case_factory):
    case = case_factory()
    packet = case.old()
    packet['input']['artifacts'].extend(fixture('legacy-artifacts.json'))
    expected = [case.target, case.root / 'artifacts/notes/text.txt',
                case.root / 'artifacts/notes/template.md']
    assert_saved(case, case.tools.invoke(packet), expected_paths=expected)
    assert (case.root / 'artifacts/notes/text.txt').read_bytes() == b'literal text\n'
    assert (case.root / 'artifacts/notes/template.md').read_bytes() == b'# Explicit title\n\nExplicit body\n'
    assert case.saved()['artifact_paths'] == [
        str(case.target), str(case.root / 'artifacts/notes/text.txt'),
        str(case.root / 'artifacts/notes/template.md')]


def test_explicit_existing_artifact_choice_and_new_append_match_old_verify(case_factory):
    for mode in ('old', 'selected'):
        case = case_factory()
        case.tools.invoke(request('artifacts', {'items': fixture('existing-artifacts.json')}))
        chosen = case.root / 'artifacts/existing-a.txt'
        unchosen = case.root / 'artifacts/existing-b.txt'
        assert chosen.read_bytes() == b'selected existing evidence A\n'
        assert unchosen.read_bytes() == b'unselected existing evidence B\n'
        # Refresh only dynamic version coordinates after real registration.
        case.context = case.tools.invoke(request('bootstrap', {
            'task': None, 'decision': None, 'feedback': None, 'rework_stage': None}))
        if mode == 'old':
            packet = case.old()
            packet['input']['result']['artifact_paths'] = [str(chosen)]
        else:
            packet = case.selected()
            packet['input']['selection']['metadata']['artifact_paths'] = [str(chosen)]
        result = case.tools.invoke(packet)
        expected = [chosen, case.target]
        assert_saved(case, result, expected_paths=expected)
        actual = case.saved()['artifact_paths']
        assert actual == [str(chosen), str(case.target)]
        assert str(unchosen) not in actual
        # Unselected material remains registered history, not a selected input.
        with case.tools.runtime.store.transaction() as db:
            assert db.execute('SELECT COUNT(id) FROM artifacts WHERE path=?', (str(unchosen),)).fetchone()[0] == 1


def test_distinct_selected_sources_with_equal_bytes_have_one_destination(case_factory):
    case = case_factory()
    twin = case.root / 'inputs/equal-bytes.bin'
    twin.write_bytes(bytes.fromhex('00ff70726f6f66800a'))
    assert twin != case.source
    assert case.source.read_bytes() == twin.read_bytes() == bytes.fromhex('00ff70726f6f66800a')
    # The existing generic file API retains its source-identity contract.
    old = case.old()
    second = deepcopy(old['input']['artifacts'][0])
    second['source']['path'] = str(twin)
    old['input']['artifacts'].append(second)
    before = case.snapshot()
    with pytest.raises(PoiseError, match='Conflicting content'):
        case.tools.invoke(old)
    assert case.snapshot() == before and not case.target.exists()
    packet = case.selected()
    packet['input']['selection']['files'] = fixture('equal-byte-files.json')
    result = case.tools.invoke(packet)
    assert_saved(case, result)
    assert case.saved()['artifact_paths'] == [str(case.target)]
    with case.tools.runtime.store.transaction() as db:
        rows = db.execute('SELECT path,digest FROM artifacts WHERE owner=? AND scope=? AND path=?',
                          ('T1', 'task', str(case.target))).fetchall()
    assert len(rows) == 1
    assert tuple(rows[0]) == (str(case.target), hashlib.sha256(bytes.fromhex('00ff70726f6f66800a')).hexdigest())
    assert case.target.read_bytes() == bytes.fromhex('00ff70726f6f66800a')


@pytest.mark.parametrize('window', [1, 2], ids=['first', 'second'])
@pytest.mark.parametrize('fault', ['bytes', 'missing'])
def test_selection_prepare_change_has_source_recovery(case_factory, monkeypatch, window, fault):
    case = case_factory()
    before = case.snapshot()
    original = FileArtifactFactory.prepare
    calls = []

    def change_before_prepare(factory, items):
        if any(item['source'].get('path') == str(case.source) for item in items):
            calls.append(True)
            if len(calls) == window:
                if fault == 'bytes':
                    case.source.write_bytes(b'changed after selected snapshot')
                else:
                    case.source.unlink()
        return original(factory, items)

    monkeypatch.setattr(FileArtifactFactory, 'prepare', change_before_prepare)
    with pytest.raises(PoiseError) as error:
        case.tools.invoke(case.selected())
    assert len(calls) == window
    assert str(error.value).startswith('selection_source_changed')
    assert str(case.source) in str(error.value)
    continuation = error.value.continuation
    assert continuation['kind'] == 'restore_source' and continuation['packet'] is None
    assert str(case.source) in continuation['instruction']
    assert case.snapshot() == before and not case.target.exists()


@pytest.mark.parametrize('window', [1, 2], ids=['first', 'second'])
def test_selection_prepare_preserves_unrelated_owner_error(case_factory, monkeypatch, window):
    case = case_factory()
    before = case.snapshot()
    original = FileArtifactFactory.prepare
    calls = []

    def conflict_before_prepare(factory, items):
        if any(item['source'].get('path') == str(case.source) for item in items):
            calls.append(True)
            if len(calls) == window:
                case.target.parent.mkdir(parents=True, exist_ok=True)
                case.target.write_bytes(b'existing unrelated destination bytes')
        return original(factory, items)

    monkeypatch.setattr(FileArtifactFactory, 'prepare', conflict_before_prepare)
    with pytest.raises(PoiseError) as error:
        case.tools.invoke(case.selected())
    assert len(calls) == window
    assert str(error.value).startswith('Artifact already exists with different content:')
    assert error.value.continuation['kind'] == 'continue_work'
    assert case.source.read_bytes() == bytes.fromhex('00ff70726f6f66800a')
    assert case.target.read_bytes() == b'existing unrelated destination bytes'
    assert case.snapshot() == before


def _cli_collection_limit(case_factory, collection, *, legacy):
    from tests.batch.test_cli import call
    from tests.batch.helpers import message
    case = case_factory()
    case.release()
    started = call(case.project, request('bootstrap', {
        'task': {'id': 'T1'}, 'decision': None, 'feedback': None, 'rework_stage': None}))
    assert started.returncode == 0, started.stdout + started.stderr
    context = json.loads(Path(json.loads(started.stdout)['response_path']).read_text())
    case.project['cfg']['batch']['max_items'] = 8
    case.project['config_path'].write_text(json.dumps(case.project['cfg']))
    packet = case.old() if legacy else selected(context)
    if collection == 'files':
        items = packet['input']['artifacts'] if legacy else packet['input']['selection']['files']
        items.extend(deepcopy(items[0]) for _ in range(8))
    else:
        packet['messages'] = [message('limit-' + str(index)) for index in range(9)]
    before = case.snapshot()
    rejected = call(case.project, packet)
    assert rejected.returncode == 2 and rejected.stderr == ''
    full = json.loads(Path(json.loads(rejected.stdout)['response_path']).read_text())
    assert full['status'] == 'rejected'
    assert case.snapshot() == before and not case.target.exists()
    return full, packet


@pytest.mark.parametrize('collection', ['files', 'messages'])
def test_cli_selection_collection_limit_has_typed_recovery(case_factory, collection):
    full, packet = _cli_collection_limit(case_factory, collection, legacy=False)
    assert full['reason'].startswith('selection_contract_invalid')
    assert 'max_items' in full['reason'] and '8' in full['reason']
    assert full['continuation']['kind'] == 'correct_input'
    assert full['continuation']['packet'] is None
    assert '8' in full['continuation']['instruction']
    assert full['selection']['schema'] == 'stage-result-selection-1'
    assert full['selection']['request_id'] == packet['input']['selection']['request_id']


@pytest.mark.parametrize('collection', ['files', 'messages'])
def test_cli_legacy_collection_limit_keeps_existing_rejection(case_factory, collection):
    full, _ = _cli_collection_limit(case_factory, collection, legacy=True)
    assert set(full) == {'status', 'reason'}
    expected = ('Packet collection exceeds configured max_items' if collection == 'files'
                else 'messages requires a bounded list')
    assert full['reason'] == expected
