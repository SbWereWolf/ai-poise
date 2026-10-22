"""CODE-F03 real CLI trace references and the same graph with a repaired leaf."""
from copy import deepcopy
import json
import pytest

from process_checks import (APP, assert_same, create, current, document, installation,
                            invoke, published, rejected, state, write_json)


CASES = json.loads((APP / 'fixtures/process-trace-references.json').read_text())


@pytest.mark.parametrize('case', CASES, ids=lambda case: '-'.join(
    [case['field'], case['invalid_type'], case['route']]))
def test_trace_reference_types_reject_without_effects_and_valid_leaf_succeeds(tmp_path, case):
    config = installation(tmp_path)
    published(invoke(config, create()), revision=1)
    packet = deepcopy(case['packet'])
    target = (current(config) if case['route'] == 'current'
              else config.parent / 'state/templates/starter.json')
    if case['route'] in ['current', 'template']:
        write_json(target, case['document'])
    before = state(config)
    rejected(invoke(config, packet), case['field'])
    assert_same(state(config), before)
    repaired = deepcopy(case['document'])
    repaired['content_contract']['requirements'][0][case['field']] = case['valid_reference']
    if case['route'] in ['current', 'template']:
        write_json(target, repaired)
    else:
        packet['input']['source']['definition'] = repaired
    before_valid = state(config)
    code, value = invoke(config, packet)
    assert code == 0, value
    if case['route'] == 'current':
        assert_same(value['status'], 'read_only')
        assert_same(value['goal_type'], 'writing')
        assert_same(value['revision'], 1)
        assert_same(state(config), before_valid)
        assert_same(document(config), repaired)
    else:
        published((code, value), revision=2, goal='notes')
        assert_same(document(config, 'notes'), repaired)
