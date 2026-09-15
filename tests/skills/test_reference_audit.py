"""Structural reference checks do not infer semantic skill usefulness."""
import json
from pathlib import Path
import pytest
from poise.infrastructure.documentation_checks import audit_skills


def fixture(tmp_path):
    skill=tmp_path/'.agents/skills/one';skill.mkdir(parents=True)
    (skill/'references').mkdir()
    (tmp_path/'.agents/skill-catalog.json').write_text(json.dumps({'schema':'poise-skill-catalog-1','skills':[
        {'id':'one','path':'.agents/skills/one/SKILL.md','purpose':'Test fixture',
         'class':'common','level':None,'responsibility':None}]}))
    return skill


def test_nested_reachability_and_context_not_automatic_loading(tmp_path):
    skill=fixture(tmp_path)
    (skill/'SKILL.md').write_text('When editing forms, read [index](references/index.md).\n')
    (skill/'references/index.md').write_text('For keyboard behavior, use [focus](focus.md).\n')
    (skill/'references/focus.md').write_text('# Focus\n')
    result=audit_skills(tmp_path)
    assert result['errors']==[] and result['unreachable']==[]
    assert result['semantic_validation']=='not_performed'
    assert any('When editing forms' in e['context'] for e in result['edges'])
    assert '.agents/skills/one/references/focus.md' in result['reachable']


def test_missing_and_orphan_reference_reported_without_deletion(tmp_path):
    skill=fixture(tmp_path)
    (skill/'SKILL.md').write_text('Read [missing](references/missing.md).\n')
    orphan=skill/'references/orphan.md';orphan.write_text('# Keep for review\n')
    result=audit_skills(tmp_path)
    assert [e['kind'] for e in result['errors']]==['missing_file']
    assert result['unreachable']==['.agents/skills/one/references/orphan.md']
    assert orphan.read_text()=='# Keep for review\n'


def test_declared_ndjson_index_provides_structural_reachability(tmp_path):
    skill=fixture(tmp_path)
    (skill/'SKILL.md').write_text('Search `catalogue.ndjson`, then read only the chosen path.\n')
    (skill/'catalogue.ndjson').write_text(json.dumps({'id':'one','path':'references/guide.md'})+'\n')
    (skill/'references/guide.md').write_text('# Guide\n')
    result=audit_skills(tmp_path)
    assert result['errors']==[] and result['unreachable']==[]
    assert any(e['kind']=='index_entry' for e in result['edges'])


def test_unlisted_skill_is_not_silently_selected(tmp_path):
    skill=fixture(tmp_path);(skill/'SKILL.md').write_text('# One\n')
    with pytest.raises(ValueError, match='Unknown selected'):
        audit_skills(tmp_path, ['invented'])


def test_loading_language_heuristic_is_review_only(tmp_path):
    skill=fixture(tmp_path);(skill/'SKILL.md').write_text('Always read all references.\n')
    result=audit_skills(tmp_path)
    assert result['errors']==[] and result['semantic_validation']=='not_performed'
    assert result['review_notes']


def test_checked_reference_cannot_escape_via_symlink(tmp_path):
    skill=fixture(tmp_path);(skill/'SKILL.md').write_text('[secret](references/out.md)\n')
    outside=tmp_path.parent/(tmp_path.name+'-secret.md');outside.write_text('private')
    (skill/'references/out.md').symlink_to(outside)
    result=audit_skills(tmp_path)
    assert any(e['kind']=='outside_checkout' for e in result['errors'])


def test_shipped_catalog_references_are_structurally_reachable():
    result=audit_skills(Path(__file__).resolve().parents[2])
    assert result['skills']==35
    assert result['errors']==[], result['errors']
    assert result['unreachable']==[], result['unreachable']


def test_nested_list_links_are_not_mistaken_for_indented_code(tmp_path):
    skill=fixture(tmp_path)
    (skill/'SKILL.md').write_text(
        '- Optional feature:\n'
        '    - Only for animation:\n'
        '      [transition](references/transition.md)\n'
        '\nNot a list:\n\n'
        '    [code sample](does-not-exist.md)\n'
        '```md\n[another example](missing.md)\n```\n')
    (skill/'references/transition.md').write_text('# Transition\n')
    result=audit_skills(tmp_path)
    assert result['errors']==[]
    assert result['unreachable']==[]
    assert len(result['edges'])==1


def test_explicit_selection_does_not_validate_unrelated_skill(tmp_path):
    skill=fixture(tmp_path);(skill/'SKILL.md').write_text('# One\n')
    other=skill.parent/'other';other.mkdir()
    (other/'SKILL.md').write_text('[broken](references/missing.md)\n')
    index=tmp_path/'.agents/skill-catalog.json'
    data=json.loads(index.read_text())
    data['skills'].append({**data['skills'][0], 'id':'other',
                          'path':'.agents/skills/other/SKILL.md'})
    index.write_text(json.dumps(data))
    selected=audit_skills(tmp_path,['one'])
    assert selected['errors']==[] and selected['unreachable']==[]
    assert selected['selection_source']=='explicit_ids'
    assert audit_skills(tmp_path)['errors'][0]['kind']=='missing_file'
