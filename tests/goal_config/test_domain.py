import copy
import pytest
from tests.goal_config.helpers import template, additions
from poise.modules.goal_config.domain import GoalTypeDefinition, BatchValidationError


def test_template_create_is_full_and_independent():
    source=template()
    result=GoalTypeDefinition.build("notes",source,additions())
    assert result.data["goal_type"]=="notes"
    assert result.data["content_contract"]["sections"][0]["id"]=="rollback"
    assert source==template()
    changed=result.data; changed["stages"][0]["instruction"]="tamper"
    assert result.data["stages"][0]["instruction"]!="tamper"


def test_forward_references_are_validated_on_final_candidate():
    a=GoalTypeDefinition.build("notes",template(),additions())
    b=GoalTypeDefinition.build("notes",template(),list(reversed(additions())))
    assert a.revision==b.revision


def test_patch_preserves_known_values_without_filling_missing_ones():
    source=template(); original=copy.deepcopy(source)
    out=GoalTypeDefinition.build("writing",source,[{"op":"patch_stage","id":"draft","set":{"instruction":"new"}}])
    assert out.data["stages"][0]["allowed_paths"]==original["stages"][0]["allowed_paths"]
    del source["stages"][0]["normalization"]
    with pytest.raises(BatchValidationError): GoalTypeDefinition.build("writing",source,[])


@pytest.mark.parametrize("updates", [
 [{"op":"patch_stage","id":"draft","set":{"instruction":"one"}},{"op":"patch_stage","id":"draft","set":{"instruction":"two"}}],
 [{"op":"remove_stage","id":"draft"},{"op":"put_stage","value":template()["stages"][0]}],
 [{"op":"put_section","value":additions()[1]["value"]},{"op":"remove_section","id":"rollback"}],
])
def test_overlapping_intents_have_no_last_writer_wins(updates):
    with pytest.raises(BatchValidationError) as e: GoalTypeDefinition.build("writing",template(),updates)
    assert any(i["code"]=="conflicting_change" for i in e.value.issues)


def test_disjoint_stage_patches_can_be_batched():
    x=GoalTypeDefinition.build("writing",template(),[
      {"op":"patch_stage","id":"draft","set":{"instruction":"new"}},
      {"op":"patch_stage","id":"draft","set":{"allowed_paths":["docs/**"]}}])
    assert x.data["stages"][0]["instruction"]=="new"
    assert x.data["stages"][0]["allowed_paths"]==["docs/**"]


def test_multiple_bad_inputs_are_reported_together():
    with pytest.raises(BatchValidationError) as e:
        GoalTypeDefinition.build("writing",template(),[
           {"op":"patch_stage","id":"missing-one","set":{"instruction":"x"}},
           {"op":"remove_stage","id":"missing-two"}])
    assert len(e.value.issues)==2


@pytest.mark.parametrize("mutate", [
 lambda x:x["stages"][0]["transitions"].update(complete="missing"),
 lambda x:x["stages"][1].update(read_only=False),
 lambda x:x["route"].update(max_transitions=0),
 lambda x:x["stages"][0].update(handler="unregistered"),
 lambda x:x["stages"][0]["required_sections"].append("unknown"),
 lambda x:x.update(extends="development"),
 lambda x:x["stages"][0].update(artifact_requirements=[{"scope":"task","pattern":"*.json","minimum":2,"maximum":1}]),
 lambda x:x["content_contract"]["requirements"].append({"id":"bad","kind":"section","stages":["draft"],"phase":"pre","section":"none","states":["populated"]}),
])
def test_invalid_final_process_is_rejected(mutate):
    raw=template(); mutate(raw)
    with pytest.raises(BatchValidationError): GoalTypeDefinition.build("writing",raw,[])


def test_insert_and_remove_nodes_in_one_batch():
    extra=copy.deepcopy(template()["stages"][0]); extra.update(id="prepare",transitions={"complete":"draft"},rework_targets=["prepare"])
    value=GoalTypeDefinition.build("writing",template(),[
        {"op":"patch_route","set":{"entry":"prepare"}}, {"op":"put_stage","value":extra}])
    assert value.data["route"]["entry"]=="prepare"
    out=GoalTypeDefinition.build("writing",value.data,[{"op":"remove_stage","id":"prepare"}, {"op":"patch_route","set":{"entry":"draft"}}])
    assert {x["id"] for x in out.data["stages"]}=={x["id"] for x in template()["stages"]}


def test_required_content_can_be_removed_only_with_its_references():
    created=GoalTypeDefinition.build("writing",template(),additions())
    with pytest.raises(BatchValidationError):
        GoalTypeDefinition.build("writing",created.data,[{"op":"remove_section","id":"rollback"}])
    out=GoalTypeDefinition.build("writing",created.data,[
        {"op":"remove_section","id":"rollback"},{"op":"remove_requirement","id":"rollback-required"}])
    assert out.data["content_contract"]["sections"]==[]


def test_trace_route_and_requirement_are_one_candidate():
    out=GoalTypeDefinition.build("writing",template(),[
     {"op":"put_requirement","value":{"id":"proof","kind":"trace","stages":["draft","audit"],"phase":"post","route":"r","point":"p","field_equals":{}}},
     {"op":"put_trace_route","value":{"id":"r","requirements":["R1"],"points":[{"id":"p","kind":"method","fields":{},"write_stages":["draft"]}]}}
    ])
    assert out.data["content_contract"]["routes"][0]["id"]=="r"


def test_goal_config_rejects_trace_required_only_after_its_write_stage():
    changes = [
      {"op":"put_requirement","value":{"id":"late-proof","kind":"trace","stages":["audit"],"phase":"post","route":"r","point":"p","field_equals":{}}},
      {"op":"put_trace_route","value":{"id":"r","requirements":["R1"],"points":[{"id":"p","kind":"method","fields":{},"write_stages":["draft"]}]}}
    ]
    with pytest.raises(BatchValidationError) as error:
        GoalTypeDefinition.build("writing",template(),changes)
    message = str(error.value)
    for expected in ("late-proof", "r", "p", "write_stages", "draft", "audit"):
        assert expected in message


def test_unknown_operation_not_raw_json_patch():
    with pytest.raises(BatchValidationError):
        GoalTypeDefinition.build("writing",template(),[{"op":"write_file","path":"/tmp/x","value":"oops"}])
