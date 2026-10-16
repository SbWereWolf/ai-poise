"""Literal stage-purpose contracts at the existing process definition owner."""

from copy import deepcopy
import json
from pathlib import Path

import pytest

from poise.modules.foundation.errors import DomainError
from poise.modules.goal_config.domain import GoalTypeDefinition


FIXTURE = Path(__file__).resolve().parents[2] / "fixtures/stage-criteria/workflow.json"


def workflow():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_four_purposes_and_explicit_other_authority_survive_validation():
    source = workflow()
    before = deepcopy(source)
    parsed = GoalTypeDefinition.parse(source).data
    assert [stage["purpose"] for stage in parsed["stages"]] == [
        "design", "observation", "execution", "other",
    ]
    assert parsed["stages"][3]["criteria_authority"] == {
        "propose": ["build"], "decide": ["build"],
    }
    assert source == before


def test_configuration_does_not_discard_known_purpose_or_authority():
    source = workflow()
    source["stages"][0]["vendor_note"] = "Preserve unrelated configuration extensions."
    parsed = GoalTypeDefinition.from_configuration(source).data
    assert parsed["stages"][0]["purpose"] == "design"
    assert parsed["stages"][0]["criteria_authority"] == {
        "propose": ["build"], "decide": [],
    }
    assert "vendor_note" not in parsed["stages"][0]
    assert source["stages"][0]["vendor_note"] == "Preserve unrelated configuration extensions."


def test_editor_changes_purpose_without_changing_role_handler_or_extensions():
    source = workflow()
    source["stages"][0]["vendor_note"] = "Keep this extension."
    edited = GoalTypeDefinition.build_configuration("development", source, [
        {"op": "patch_stage", "id": "sketch", "set": {"purpose": "other"}},
    ]).data
    assert edited["stages"][0]["purpose"] == "other"
    assert edited["stages"][0]["criteria_authority"] == {
        "propose": ["build"], "decide": [],
    }
    assert edited["stages"][0]["role"] == "artisan"
    assert edited["stages"][0]["handler"] == "produce"
    assert edited["stages"][0]["vendor_note"] == "Keep this extension."
    assert source["stages"][0]["purpose"] == "design"


def test_arbitrary_role_names_do_not_determine_purpose_or_grants():
    source = workflow()
    source["stages"][0]["role"] = "quality-strategist"
    source["stages"][1]["role"] = "implementation-specialist"
    parsed = GoalTypeDefinition.parse(source).data
    assert parsed["stages"][0]["purpose"] == "design"
    assert parsed["stages"][1]["purpose"] == "observation"
    assert parsed["stages"][1]["criteria_authority"] == {"propose": [], "decide": ["build"]}


@pytest.mark.parametrize("field", ["purpose", "criteria_authority"])
def test_missing_explicit_stage_classification_is_rejected(field):
    source = workflow()
    del source["stages"][0][field]
    with pytest.raises(DomainError):
        GoalTypeDefinition.from_configuration(source)


@pytest.mark.parametrize("value", [None, "", "automatic", True, ["design"]])
def test_unknown_or_nontext_purpose_is_rejected(value):
    source = workflow()
    source["stages"][0]["purpose"] = value
    with pytest.raises(DomainError):
        GoalTypeDefinition.from_configuration(source)


@pytest.mark.parametrize("value", [
    None,
    {"propose": []},
    {"propose": "build", "decide": []},
    {"propose": ["build", "build"], "decide": []},
    {"propose": ["absent"], "decide": []},
    {"propose": ["assess"], "decide": []},
])
def test_invalid_authority_is_not_silently_replaced_by_empty_grants(value):
    source = workflow()
    source["stages"][0]["criteria_authority"] = value
    with pytest.raises(DomainError):
        GoalTypeDefinition.from_configuration(source)


def test_other_with_explicit_empty_grants_remains_valid_without_implicit_authority():
    source = workflow()
    source["stages"][3]["criteria_authority"] = {"propose": [], "decide": []}
    parsed = GoalTypeDefinition.parse(source).data
    assert parsed["stages"][3]["purpose"] == "other"
    assert parsed["stages"][3]["criteria_authority"] == {"propose": [], "decide": []}


def test_unknown_authority_extension_is_ignored_only_at_configuration_boundary():
    source = workflow()
    source["stages"][0]["criteria_authority"]["vendor_note"] = "This grants no authority."
    parsed = GoalTypeDefinition.from_configuration(source).data
    assert parsed["stages"][0]["criteria_authority"] == {"propose": ["build"], "decide": []}
    with pytest.raises(DomainError):
        GoalTypeDefinition.parse(source)
