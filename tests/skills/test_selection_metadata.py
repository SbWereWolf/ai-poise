import copy
import json
from pathlib import Path
import pytest
from poise.common import PoiseError
from poise.modules.skills.catalog import SkillCatalog
from poise.modules.skills.selection import SkillSelection

HERE = Path(__file__).resolve().parents[2]


def inputs():
    return (json.loads((HERE/'config/development/skill-selection.json').read_text()),
            SkillCatalog.parse(json.loads((HERE/'.agents/skill-catalog.json').read_text())))


def test_every_shipped_skill_is_selected_or_explicitly_parked():
    raw, catalog = inputs(); model = SkillSelection.parse(raw, catalog)
    doc = model.as_dict()
    selected = {s for rule in doc['rules'] for s in rule['skills']}
    parked = {s['id'] for s in doc['excluded']}
    assert selected.isdisjoint(parked)
    assert selected | parked == {s.id for s in catalog.skills}
    assert {'laravel', 'vue-best-practices', 'vitest', 'phpunit'} <= parked
    assert {'poise', 'poise-development', 'repo-tooling', 'tdd', 'ddd'} <= selected


@pytest.mark.parametrize('defect', ['unknown_skill','duplicate','unknown_fact','escape','missing_coverage'])
def test_invalid_metadata_rejected(defect):
    raw,catalog=inputs()
    if defect=='unknown_skill':raw['rules'][0]['skills'].append('invented')
    elif defect=='duplicate':raw['rules'].append(copy.deepcopy(raw['rules'][0]))
    elif defect=='unknown_fact':raw['rules'][0]['when']['facts_all']=['invented']
    elif defect=='escape':raw['rules'][0]['when']['paths_any']=['../erp/**']
    elif defect=='missing_coverage':raw['excluded'].pop()
    with pytest.raises(PoiseError): SkillSelection.parse(raw,catalog)


def test_canonical_metadata_is_not_mutated_by_caller():
    raw,catalog=inputs(); model=SkillSelection.parse(raw,catalog); expected=model.as_dict()
    raw['rules'].clear(); model.as_dict()['rules'].clear()
    assert model.as_dict()==expected


def test_no_instruction_bodies_or_commands_in_selection_metadata():
    raw,catalog=inputs(); doc=SkillSelection.parse(raw,catalog).as_dict()
    assert all(set(r)=={'id','priority','when','skills'} for r in doc['rules'])
    assert all(set(r['when'])=={'stages','paths_any','facts_all'} for r in doc['rules'])
    assert doc['subject']=='ai-poise'


def test_task_and_sprint_formulation_have_distinct_skill_routes():
    raw, _ = inputs()
    rules = {rule['id']: rule for rule in raw['rules']}
    assert rules['task_design']['skills'] == ['task-design']
    assert rules['sprint_design']['skills'] == ['sprint-design']
    assert rules['sprint_design']['when']['facts_all'] == ['sprint_design']
