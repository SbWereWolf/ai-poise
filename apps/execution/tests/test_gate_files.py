"""Real regular-file facts, explicit roots and original unique min/max counts."""
from copy import deepcopy
from pathlib import Path
import pytest
from gate_checks import (assert_same, arranged, declared, evaluated, fixture, negative_rows,
                         next_packet, packet, query, ready, result_query, write_json)


@pytest.mark.parametrize('count,accepted', [(0, False), (1, True), (2, True), (3, False)])
def test_actual_unique_file_minimum_and_maximum_apply_to_all_file_gates(tmp_path, count, accepted):
    config, mapping = ready(tmp_path)
    paths = []
    for index in range(count):
        path = tmp_path / f'state/files/task/outputs/file-{index}.txt'
        path.write_text('actual')
        paths.extend([str(path), str(path)])
    candidate = packet('evaluate', mapping, files=paths)
    response = evaluated(config, candidate, accepted=accepted)
    expected = fixture('assessment')
    expected['passed'] = accepted
    expected['post']['passed'] = accepted
    expected['artifact_checks'][0].update(actual=count, passed=accepted)
    expected['post']['requirements'][-1].update(passed=accepted, detail=f'unique_files={count}; требуется 1..2')
    assert_same(response['assessment'], expected)
    if not accepted:
        negative_rows(config)


def test_scope_and_case_sensitive_pattern_are_independent_of_file_body(tmp_path):
    config, mapping = ready(tmp_path)
    paths = [mapping['@FILE']]
    for relative in ['runtime/outputs/result.txt', 'sprint/outputs/result.txt',
                     'task/outputs/result.TXT', 'task/elsewhere.txt']:
        path = tmp_path / 'state/files' / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('other')
        paths.append(str(path))
    first = evaluated(config, packet('evaluate', mapping, files=paths), accepted=True,
                      assessment=fixture('assessment'))
    Path(mapping['@FILE']).write_bytes(b'changed completely\x00')
    evaluated(config, next_packet(mapping, first, files=[]), accepted=True,
              assessment=fixture('assessment'))


@pytest.mark.parametrize('case', ['missing', 'directory', 'outside', 'ambiguous'])
def test_supplied_unusable_file_facts_save_explicit_negative_observation(tmp_path, case):
    config, mapping = ready(tmp_path)
    if case == 'missing':
        path = tmp_path / 'state/files/task/absent.txt'
    elif case == 'directory':
        path = tmp_path / 'state/files/task/outputs'
    elif case == 'outside':
        path = tmp_path / 'outside.txt'
        path.write_text('outside')
    else:
        data = __import__('json').loads(config.read_text())
        data['gates']['roots']['runtime'] = data['gates']['roots']['task']
        write_json(config, data)
        path = Path(mapping['@FILE'])
    candidate = packet('evaluate', mapping, files=[str(path)])
    result = evaluated(config, candidate, accepted=False)
    assert_same(result['assessment']['prepared'], True)
    errors = result['assessment']['file_errors']
    assert len(errors) == 1 and set(errors[0]) == {'path', 'reason'}
    assert_same(errors[0]['path'], str(path))
    assert type(errors[0]['reason']) is str and errors[0]['reason']
    negative_rows(config)


def test_nested_roots_select_deepest_scope(tmp_path):
    config, mapping = ready(tmp_path)
    import json
    data = json.loads(config.read_text())
    data['gates']['roots']['runtime'] = 'files'
    write_json(config, data)
    evaluated(config, packet('evaluate', mapping), accepted=True, assessment=fixture('assessment'))
    saved = query(config, result_query())['results'][0]
    assert_same(saved['candidate']['data']['files'], fixture('facts'))


def test_known_file_removal_and_restoration_are_observed_only_for_new_candidates(tmp_path):
    config, mapping = ready(tmp_path)
    first = evaluated(config, packet('evaluate', mapping), accepted=True)
    Path(mapping['@FILE']).unlink()
    second = evaluated(config, next_packet(mapping, first, files=[]), accepted=False)
    assert_same(second['assessment']['artifact_checks'][0]['actual'], 0)
    negative_rows(config, 'candidate-2')
    original = query(config, result_query())['results'][0]
    assert_same(original['candidate']['data']['files'], fixture('facts'))
    Path(mapping['@FILE']).write_text('restored by operator')
    evaluated(config, next_packet(mapping, second, 'candidate-3', files=[]), accepted=True,
              assessment=fixture('assessment'))


def test_root_relocation_uses_catalogued_relative_paths_without_integrity_veto(tmp_path):
    config, mapping = ready(tmp_path)
    first = evaluated(config, packet('evaluate', mapping), accepted=True)
    import json
    data = json.loads(config.read_text())
    data['gates']['roots']['task'] = 'new-task-root'
    write_json(config, data)
    Path(mapping['@FILE']).unlink()
    path = tmp_path / 'state/new-task-root/outputs/result.txt'
    path.parent.mkdir(parents=True)
    path.write_text('replacement')
    evaluated(config, next_packet(mapping, first, files=[]), accepted=True,
              assessment=fixture('assessment'))
