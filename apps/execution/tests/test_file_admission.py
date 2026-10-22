"""EXE-05: actual Gate evidence admission consumes the selected Stage policy."""
from pathlib import Path
import pytest
from file_scope_checks import (assert_same, database, decision, gate, gate_packet,
                               invoke, original_rows, ready, saved_gate)


def test_code_root_regular_file_is_accepted_without_poise_receipt_and_saved(tmp_path):
    config, mapping = ready(tmp_path)
    canonical = original_rows(database(config))['executions']
    expected = [decision('code', 0, 'src/app.py', True)]
    response = gate(config, gate_packet(mapping), expected, accepted=True)
    saved = saved_gate(config)
    assert saved['accepted_result']['id'] == response['result_id']
    assert_same(saved['decision']['data']['assessment'], response['assessment'])
    assert_same(saved['accepted_result']['data']['assessment'], response['assessment'])
    assert_same(saved['decision']['data']['provenance']['stage_id'], 'draft')
    assert_same(saved['decision']['data']['provenance']['process_ref']['revision'], mapping['@PROCESS'])
    assert_same(original_rows(database(config))['executions'], canonical)
    assert_same(saved_gate(config), saved)


@pytest.mark.parametrize('relative,reason', [('src/private/note.txt', 'denied'),
                                            ('note.txt', 'outside_allow')])
def test_existing_file_denial_is_real_gate_failure_with_all_other_gates_passing(tmp_path,
                                                                               relative, reason):
    # note.txt has no permission in this explicit policy, while its real positive neighbor does.
    def exclude_note(policy):
        policy['stages'][0]['roots'][0]['read']['allow'] = ['src/**']
    config, mapping = ready(tmp_path, policy_change=exclude_note)
    file = Path(mapping['@CODE0']) / relative
    assert file.is_file()
    response = gate(config, gate_packet(mapping, files=[str(file)]),
                    [decision('code', 0, relative, False, reason)], accepted=False)
    assert response['assessment']['pre']['passed'] is True
    assert response['assessment']['post']['passed'] is True
    assert response['assessment']['file_errors'] == []
    saved = saved_gate(config)
    assert saved['accepted_result'] is None
    assert_same(saved['decision']['data']['assessment'], response['assessment'])
    # A fresh independent context avoids retaining the deliberately forbidden prior file.
    good_config, good_mapping = ready(tmp_path / 'positive')
    gate(good_config, gate_packet(good_mapping), [decision('code', 0, 'src/app.py', True)], accepted=True)


@pytest.mark.parametrize('case,reason', [('missing', 'missing_file'), ('directory', 'not_regular_file')])
def test_unusable_file_is_not_admitted_even_when_path_rule_allows_it(tmp_path, case, reason):
    config, mapping = ready(tmp_path)
    relative = 'src/absent.txt' if case == 'missing' else 'src/directory'
    target = Path(mapping['@CODE0']) / relative
    if case == 'directory':
        target.mkdir()
    response = gate(config, gate_packet(mapping, files=[str(target)]),
                    [decision('code', 0, relative, False, reason)], accepted=False)
    assert saved_gate(config)['accepted_result'] is None
    # The real ordinary neighboring file proves the fixture and permissions work.
    positive_config, positive_mapping = ready(tmp_path / 'positive')
    gate(positive_config, gate_packet(positive_mapping),
         [decision('code', 0, 'src/app.py', True)], accepted=True)


def test_retained_catalogue_is_readmitted_by_current_stage_even_with_no_new_files(tmp_path):
    config, mapping = ready(tmp_path, counted=True)
    file = str(Path(mapping['@ARTIFACT'], 'outputs/note.txt'))
    positive = [decision('artifact', 0, 'outputs/note.txt', True)]
    denied = [decision('artifact', 0, 'outputs/note.txt', False, 'denied')]
    first_packet = gate_packet(mapping, files=[file])
    first = gate(config, first_packet, positive, accepted=True, counted=True)
    original = saved_gate(config)
    second = gate(config, gate_packet(mapping, stage='integration', files=[],
                                     revision=first['revision'], candidate='candidate-2'),
                  denied, accepted=False, counted=True)
    assert second['assessment']['artifact_checks'][0]['actual'] == 1
    assert second['assessment']['artifact_checks'][0]['passed'] is True
    assert second['assessment']['pre']['passed'] is second['assessment']['post']['passed'] is True
    assert saved_gate(config, 'candidate-2')['accepted_result'] is None
    assert_same(saved_gate(config, 'candidate-2')['decision']['data']['assessment'], second['assessment'])
    neighbor = gate(config, gate_packet(mapping, files=[], revision=second['revision'],
                                       candidate='candidate-3'), positive, accepted=True, counted=True)
    Path(file).unlink()
    code, replay = invoke(config, first_packet)
    assert code == 0
    assert_same(replay, dict(first, replayed=True))
    assert_same(saved_gate(config), original)
    # A fresh request observes the missing current file instead of reusing old admission.
    fresh = gate_packet(mapping, files=[file], revision=neighbor['revision'], candidate='candidate-4')
    code, current = invoke(config, fresh)
    assert code == 2 and current['status'] == 'evaluated' and current['accepted'] is False
    assert current['assessment']['file_admission']['passed'] is False
    assert saved_gate(config, 'candidate-4')['accepted_result'] is None


def test_regular_file_body_change_is_a_fresh_usable_observation(tmp_path):
    config, mapping = ready(tmp_path)
    first = gate(config, gate_packet(mapping), [decision('code', 0, 'src/app.py', True)], accepted=True)
    Path(mapping['@CODE0'], 'src/app.py').write_bytes(b'different recoverable content\x00')
    second_packet = gate_packet(mapping, files=[], revision=first['revision'], candidate='candidate-2')
    gate(config, second_packet, [decision('code', 0, 'src/app.py', True)], accepted=True)


def test_real_legacy_artifact_outside_all_working_roots_is_not_admitted(tmp_path):
    from file_scope_checks import bind, fixture

    def working_inside(policy):
        root = next(value for value in policy['roots'] if value['kind'] == 'artifact')
        root['path'] = str(Path(root['path']) / 'working')
        inside = Path(root['path']) / 'outputs/note.txt'
        inside.parent.mkdir(parents=True)
        inside.write_text('inside the declared working root')

    config, mapping = ready(tmp_path, definition=fixture('outside_count_definition'),
                            policy_change=working_inside)
    outside = Path(mapping['@ARTIFACT']) / 'outputs/note.txt'
    assert outside.is_file()
    # Gate's artifact scope is the parent; the explicit working root is its child.
    assert not outside.is_relative_to(Path(mapping['@ARTIFACT']) / 'working')
    code, rejected_evidence = invoke(config, gate_packet(mapping, files=[str(outside)]))
    assert code == 2
    expected = bind(fixture('outside_gate_response'), dict(mapping, **{'@OUTSIDE': str(outside)}))
    assert_same(rejected_evidence, expected)
    assert rejected_evidence['assessment']['artifact_checks'][0]['actual'] == 1
    assert rejected_evidence['assessment']['artifact_checks'][0]['passed'] is True
    assert rejected_evidence['assessment']['pre']['passed'] is True
    assert rejected_evidence['assessment']['post']['passed'] is True
    saved = saved_gate(config)
    assert saved['accepted_result'] is None
    assert_same(saved['decision']['data']['assessment'], expected['assessment'])
    good_config, good_mapping = ready(tmp_path / 'positive',
                                      definition=fixture('outside_count_definition'),
                                      policy_change=working_inside)
    inside = Path(good_mapping['@ARTIFACT']) / 'working/outputs/note.txt'
    assert inside.is_file()
    code, admitted = invoke(good_config, gate_packet(good_mapping, files=[str(inside)]))
    assert code == 0 and isinstance(admitted['result_id'], str) and admitted['result_id']
    expected_inside = bind(fixture('inside_gate_response'),
                           dict(good_mapping, **{'@RESULT': admitted['result_id']}))
    assert_same(admitted, expected_inside)
    assert saved_gate(good_config)['accepted_result']['id'] == admitted['result_id']


def test_working_root_directory_saves_negative_evidence_and_regular_neighbor_succeeds(tmp_path):
    from file_scope_checks import fixture

    config, mapping = ready(tmp_path)
    canonical = original_rows(database(config))['executions']
    root = Path(mapping['@CODE0'])
    assert root.is_absolute() and root.is_dir()
    code, negative = invoke(config, gate_packet(mapping, files=[str(root)]))
    assert code == 2
    assert negative['status'] == 'evaluated', negative
    expected = fixture('outside_gate_response')
    expected['assessment'] = fixture('assessment')
    expected['assessment']['passed'] = False
    expected['assessment']['file_admission'] = {
        'passed': False, 'files': [decision('code', 0, '.', False, 'not_regular_file')]}
    assert_same(negative, expected)
    saved = saved_gate(config)
    assert saved['accepted_result'] is None
    assert_same(saved['decision']['data']['assessment'], expected['assessment'])
    neighbor = gate(config, gate_packet(mapping, revision=negative['revision'],
                                        candidate='root-directory-neighbor'),
                    [decision('code', 0, 'src/app.py', True)], accepted=True)
    assert saved_gate(config, 'root-directory-neighbor')['accepted_result']['id'] == neighbor['result_id']
    assert_same(original_rows(database(config))['executions'], canonical)
