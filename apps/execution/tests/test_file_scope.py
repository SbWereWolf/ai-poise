"""EXE-03/06: explicit per-Stage multi-root read/write decisions."""
from copy import deepcopy
from pathlib import Path
import pytest
from file_scope_checks import (access, arranged, assert_same, bind, checked, database,
                               decision, declare, fixture, invoke, original_rows,
                               path, query, ready, refusal, result_query)
from process_checks import published


@pytest.mark.parametrize('kind,position,relative,mode,allowed,reason', fixture('access_rows'))
def test_root_qualified_read_write_allow_and_deny(tmp_path, kind, position, relative,
                                                 mode, allowed, reason):
    config, mapping = ready(tmp_path)
    value = access(mapping, mode=mode, paths=[path(kind, position, relative)])
    checked(config, value, [decision(kind, position, relative, allowed, reason)])


@pytest.mark.parametrize('stage,role,relative,allowed,reason', fixture('main_rows'))
def test_main_checkout_write_uses_stage_not_name_or_global_role(tmp_path, stage, role,
                                                               relative, allowed, reason):
    config, mapping = ready(tmp_path)
    value = access(mapping, stage=stage, mode='write', paths=[path('main', 0, relative)])
    checked(config, value, [decision('main', 0, relative, allowed, reason)], role=role)


def test_custom_saved_role_is_used_for_read_and_read_only_write(tmp_path):
    config, mapping = ready(tmp_path)
    value = access(mapping, stage='audit')
    first = checked(config, value, [decision('code', 0, 'src/app.py', True)], role='extra-auditor')
    write = access(mapping, stage='audit', mode='write', revision=first['revision'], request='audit-write')
    checked(config, write, [decision('code', 0, 'src/app.py', False, 'read_only')], role='extra-auditor')


def test_batch_preserves_input_order_and_independent_root_decisions(tmp_path):
    config, mapping = ready(tmp_path)
    paths = [path('code', 1), path(), path('main', 0)]
    expected = [decision('code', 1, 'src/app.py', False, 'outside_allow'),
                decision('code', 0, 'src/app.py', True), decision('main', 0, 'src/app.py', True)]
    checked(config, access(mapping, paths=paths), expected)


def test_access_result_is_own_exact_durable_public_record_and_does_not_advance(tmp_path):
    config, mapping = ready(tmp_path)
    canonical = original_rows(database(config))['executions']
    value = access(mapping)
    response = checked(config, value, [decision('code', 0, 'src/app.py', True)])
    result = query(config, result_query())
    expected = bind(fixture('saved_result'), dict(mapping, **{'@RESULT': response['result_id']}))
    assert_same(result['results'], [expected])
    assert_same(query(config, result_query()), result)
    assert_same(original_rows(database(config))['executions'], canonical)
    Path(mapping['@CODE0'], 'src/app.py').unlink()
    code, replay = invoke(config, value)
    assert code == 0
    assert_same(replay, dict(response, replayed=True))
    assert_same(query(config, result_query()), result)


def test_frozen_stage_role_survives_process_source_edit(tmp_path):
    config, mapping = ready(tmp_path)
    packet = fixture('edit_role')
    packet = bind(packet, mapping)
    published(invoke(config, packet), revision=4, previous=mapping['@PROCESS'])
    checked(config, access(mapping, stage='audit', revision=4),
            [decision('code', 0, 'src/app.py', True)], role='extra-auditor')


def test_selected_root_rejects_real_neighbor_escape_with_permissive_allow_and_controls(tmp_path):
    def permissive(policy):
        policy['stages'][0]['roots'][0]['read'] = {'allow': ['**'], 'deny': []}
    config, mapping = ready(tmp_path, policy_change=permissive)
    assert Path(mapping['@CODE1'], 'note.txt').is_file()
    first = checked(config, access(mapping, paths=[path(relative='note.txt')]),
                    [decision('code', 0, 'note.txt', True)])
    escaped = access(mapping, paths=[path(relative='../code1/note.txt')],
                     revision=first['revision'], request='escape')
    refusal(config, escaped, 'relative', 'root')
    own_other = access(mapping, paths=[path('code', 1, 'note.txt')],
                       revision=first['revision'], request='other-root')
    other = checked(config, own_other, [decision('code', 1, 'note.txt', False, 'outside_allow')])
    checked(config, access(mapping, paths=[path('code', 1, 'docs/guide.txt')],
                           revision=other['revision'], request='other-valid'),
            [decision('code', 1, 'docs/guide.txt', True)])


def test_absolute_path_is_not_a_selected_root_relative_path(tmp_path):
    config, mapping = ready(tmp_path)
    refusal(config, access(mapping, paths=[path(relative=str(Path(mapping['@CODE0'], 'src/app.py')))]),
            'relative')
    checked(config, access(mapping), [decision('code', 0, 'src/app.py', True)])


@pytest.mark.parametrize('change', ['root', 'stage', 'generation'])
def test_ordinary_unknown_binding_is_rejected_without_effect_and_valid_neighbor_works(tmp_path, change):
    config, mapping = ready(tmp_path)
    value = access(mapping)
    if change == 'root':
        value['input']['paths'][0]['root']['position'] = 9
    elif change == 'stage':
        value['input']['stage_id'] = 'absent'
    else:
        value['input']['generation'] = 2
    refusal(config, value, change)
    checked(config, access(mapping), [decision('code', 0, 'src/app.py', True)])


def test_files_check_requires_explicit_policy_not_flat_scope_fallback(tmp_path):
    config, mapping = ready(tmp_path, policy=False)
    refusal(config, access(mapping), 'policy')
    # A second ordinary configured installation establishes the positive route.
    good_config, good_mapping = ready(tmp_path / 'explicit')
    checked(good_config, access(good_mapping), [decision('code', 0, 'src/app.py', True)])


@pytest.mark.parametrize('case', ['duplicate_root', 'unknown_stage', 'wrong_contract'])
def test_policy_declaration_binding_controls_have_no_partial_effect(tmp_path, case):
    config, mapping, value = arranged(tmp_path)
    good = deepcopy(value)
    if case == 'duplicate_root':
        value['input']['file_policy']['roots'].append(deepcopy(value['input']['file_policy']['roots'][0]))
    elif case == 'unknown_stage':
        value['input']['file_policy']['stages'][0]['stage_id'] = 'absent'
    else:
        value['input']['contract_revision'] = 'different'
    refusal(config, value, 'root', 'stage', 'contract')
    declare(config, good)


def test_saved_access_queries_isolate_context_and_generation(tmp_path):
    config, mapping = ready(tmp_path)
    checked(config, access(mapping), [decision('code', 0, 'src/app.py', True)])
    for update in [{'context_id': 'absent'}, {'generation': 2}, {'task_id': '0043'},
                   {'request_id': 'absent'}]:
        assert_same(query(config, result_query(**update))['results'],
                    [{'id': 'saved', 'kind': 'file_result', 'result': None}])


def test_business_denied_access_has_exact_saved_negative_provenance_replay_and_neighbor(tmp_path):
    config, mapping = ready(tmp_path)
    canonical = original_rows(database(config))['executions']
    value = access(mapping, mode='write', paths=[path('main', 0)])
    negative = checked(config, value, [decision('main', 0, 'src/app.py', False, 'outside_allow')])
    expected = bind(fixture('negative_saved_result'), dict(mapping, **{'@RESULT': negative['result_id']}))
    assert_same(query(config, result_query())['results'], [expected])
    assert_same(query(config, result_query())['results'], [expected])
    # The same selected root/path permits reading: denial is the write decision.
    neighbor = access(mapping, paths=[path('main', 0)], revision=negative['revision'],
                      request='negative-neighbor')
    checked(config, neighbor, [decision('main', 0, 'src/app.py', True)])
    code, replay = invoke(config, value)
    assert code == 2
    assert_same(replay, dict(negative, replayed=True))
    assert_same(query(config, result_query())['results'], [expected])
    assert_same(original_rows(database(config))['executions'], canonical)


def test_two_populated_policy_contexts_preserve_distinct_exact_results_after_interleaving(tmp_path):
    config, mapping, declaration = arranged(tmp_path)
    declare(config, declaration)
    canonical = original_rows(database(config))['executions']
    first_packet = access(mapping)
    first = checked(config, first_packet, [decision('code', 0, 'src/app.py', True)])
    other = bind(fixture('declare'), mapping)
    other['input'].update(context_id='scope-2', request_id='declare-scope-2',
                          expected_revision=first['revision'])
    other['input']['file_policy']['stages'][0]['roots'][0]['read']['deny'] = ['src/**']
    code, declared_other = invoke(config, other)
    assert code == 0
    assert_same(declared_other, fixture('second_context_declared'))
    second_packet = access(mapping, context='scope-2', request='access-2',
                           revision=declared_other['revision'])
    second = checked(config, second_packet, [decision('code', 0, 'src/app.py', False, 'denied')])
    assert first['result_id'] != second['result_id']
    expected_first = bind(fixture('saved_result'), dict(mapping, **{'@RESULT': first['result_id']}))
    expected_second = bind(fixture('second_context_saved_result'),
                           dict(mapping, **{'@RESULT': second['result_id']}))
    assert_same(query(config, result_query())['results'], [expected_first])
    assert_same(query(config, result_query(context_id='scope-2', request_id='access-2'))['results'],
                [expected_second])
    checked(config, access(mapping, mode='write', revision=second['revision'], request='access-3'),
            [decision('code', 0, 'src/app.py', True)])
    for packet, saved, expected_code in [(first_packet, first, 0), (second_packet, second, 2)]:
        code, replay = invoke(config, packet)
        assert code == expected_code
        assert_same(replay, dict(saved, replayed=True))
    assert_same(query(config, result_query())['results'], [expected_first])
    assert_same(query(config, result_query(context_id='scope-2', request_id='access-2'))['results'],
                [expected_second])
    assert_same(original_rows(database(config))['executions'], canonical)
