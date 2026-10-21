"""T048 actual public process creation and structural-edit contracts."""
from copy import deepcopy
import json
import pytest

from process_checks import (assert_same, create, current, document, edit, fixture,
                            installation, invoke, published, rejected, state, write_json)


@pytest.mark.parametrize('template', [False, True])
def test_full_and_selected_template_materialize_independently(tmp_path, template):
    config = installation(tmp_path)
    original = (tmp_path / 'state/templates/starter.json').read_bytes()
    published(invoke(config, create(template=template)), revision=1)
    assert_same(document(config), fixture('definition'))
    assert (tmp_path / 'state/templates/starter.json').read_bytes() == original
    alternative = fixture('alternative')
    published(invoke(config, create(goal='notes', request='notes-1', revision=1,
                                   definition=alternative)), revision=2, goal='notes')
    assert_same(document(config, 'notes'), alternative)
    assert_same(document(config), fixture('definition'))


@pytest.mark.parametrize('reverse', [False, True])
def test_forward_references_and_final_validation_ignore_declaration_order(tmp_path, reverse):
    config = installation(tmp_path)
    changes = fixture('forward_changes')
    if reverse:
        changes.reverse()
    published(invoke(config, create(changes=changes)), revision=1)
    assert_same(document(config), fixture('forward_expected'))


def test_disjoint_named_changes_and_removal_preserve_exact_final_document(tmp_path):
    config = installation(tmp_path)
    first = published(invoke(config, create()), revision=1)
    second = published(invoke(config, edit(first, fixture('edit_changes'))), revision=2,
                       previous=first['process_revision'])
    assert_same(document(config), fixture('edited'))
    third = published(invoke(config, edit(second, fixture('forward_changes'), request='add')),
                      revision=3, previous=second['process_revision'])
    removes = fixture('remove_changes')
    published(invoke(config, edit(third, removes, request='remove')), revision=4,
              previous=third['process_revision'])
    assert_same(document(config), fixture('edited'))


@pytest.mark.parametrize('case', ['shape', 'entry', 'link', 'content', 'overlap', 'purpose', 'authority'])
def test_structural_failure_is_concrete_and_preserves_published_state(tmp_path, case):
    config = installation(tmp_path)
    first = published(invoke(config, create()), revision=1)
    if case == 'shape':
        changes, tokens = [{'op': 'patch_stage', 'id': 'compose', 'set': {'read_only': 1}}], ['bool', 'read_only']
    elif case == 'entry':
        changes, tokens = [{'op': 'patch_route', 'set': {'entry': 'missing'}}], ['route', 'вход', 'missing']
    elif case == 'link':
        changes, tokens = [{'op': 'patch_stage', 'id': 'compose', 'set': {'transitions': {'complete': 'missing'}}}], ['route', 'этап', 'missing']
    elif case == 'content':
        changes = deepcopy(fixture('forward_changes')[:1])
        tokens = ['content', 'prepare', 'basis', 'section']
    elif case == 'overlap':
        changes = [deepcopy(fixture('edit_changes')[0])] * 2
        tokens = ['changes', 'conflict', 'Пересека']
    elif case == 'purpose':
        changes, tokens = [{'op': 'patch_stage', 'id': 'compose', 'set': {'purpose': 'imaginary'}}], ['purpose', 'route', 'назначен']
    else:
        changes = fixture('invalid_authority_changes')
        tokens = ['authority', 'execution', 'route']
    before = state(config)
    rejected(invoke(config, edit(first, changes, request='invalid')),
             clauses=fixture('structural_diagnostics')[case])
    assert state(config) == before


def test_raw_extensions_survive_named_configuration_edit(tmp_path):
    config = installation(tmp_path)
    raw = fixture('raw_extension')
    write_json(tmp_path / 'state/templates/starter.json', raw)
    first = published(invoke(config, create(template=True)), revision=1)
    assert_same(document(config), raw)
    published(invoke(config, edit(first, fixture('edit_changes'))), revision=2,
              previous=first['process_revision'])
    expected = fixture('edited')
    expected['extension'] = raw['extension']
    expected['stages'][1]['editor_note'] = 'keep this too'
    expected['route']['display'] = 'Entry'
    assert_same(document(config), expected)
    assert json.loads(config.read_text())['operator_extension'] == fixture('config')['operator_extension']


def test_named_trace_route_and_requirement_have_one_final_candidate(tmp_path):
    config = installation(tmp_path)
    first = published(invoke(config, create(changes=fixture('trace_changes'))), revision=1)
    assert_same(document(config), fixture('trace_expected'))
    published(invoke(config, edit(first, fixture('trace_remove'))), revision=2,
              previous=first['process_revision'])
    assert_same(document(config), fixture('definition'))
