"""Shipped configuration work resolves a real, focused instruction owner."""
import json
from pathlib import Path

import pytest

from poise.infrastructure.development_routing import load_development_routing
from poise.infrastructure.skill_catalog import load_skill_catalog
from poise.modules.tasks.decomposition import FocusedDecomposition

ROOT = Path(__file__).resolve().parents[2]
SOURCES = (
    "config/project.example.json",
    "config/project-templates/common.json",
)


def shipped_policy(source):
    document = json.loads((ROOT / source).read_text())
    return document.get("config", document)["task_decomposition"]


@pytest.mark.parametrize("source", SOURCES)
def test_configuration_template_resolves_delivered_narrow_owner(source):
    policy = shipped_policy(source)
    rows = [row for row in policy["skills"]
            if row["responsibility"] == "project-configuration"]
    assert len(rows) == 1
    assert rows[0]["id"] == "project-configuration"
    assert rows[0]["class"] == "narrow"
    assert {row["responsibility"] for row in policy["areas"]
            if row["path"] == "config/**"} == {"project-configuration"}
    catalog = load_skill_catalog(ROOT / ".agents/skill-catalog.json", ROOT)
    entries = [entry for entry in catalog.skills if entry.id == "project-configuration"]
    assert len(entries) == 1
    assert entries[0].skill_class == "working"
    assert entries[0].level == 1
    assert entries[0].responsibility == "project-configuration"
    assert catalog.decomposition_skills(["project-configuration"]) == rows


@pytest.mark.parametrize("path,selected", [
    ("config/project.example.json", True),
    ("config/project-templates/common.json", True),
    ("docs/configuration/project-setup.md", False),
    ("src/poise/modules/tasks/domain.py", False),
])
def test_real_router_selects_configuration_instruction_in_its_scope(path, selected):
    router = load_development_routing(
        catalog=ROOT / ".agents/skill-catalog.json",
        selection=ROOT / "config/development/skill-selection.json",
        policy=ROOT / "config/development/routing.json",
        packages=ROOT / "config/testing/test-packages.json",
    )
    result = router.route({
        "checkout": str(ROOT), "stage": "implementation", "handler": "produce",
        "scope_paths": [path], "changed_paths": [], "facts": [],
        "required_methods": [], "registered_methods": [],
        "result_contract": {"sections": ["report"]},
    })
    assert result["status"] == "ready"
    assert ("project-configuration" in result["skills"]) is selected
    assert {"poise", "poise-development", "repo-tooling"} <= set(result["skills"])


@pytest.mark.parametrize("source", SOURCES)
@pytest.mark.parametrize("skills,valid", [
    (["project-configuration"], True),
    ([], False),
    (["poise-development"], False),
])
def test_configuration_phase_requires_its_narrow_owner(source, skills, valid):
    decomposition = {
        "kind": "ordinary", "integration": None,
        "phases": [{"stage": "implementation", "skills": skills, "areas": ["config/**"]}],
    }
    diagnostics = list(FocusedDecomposition.inspect(
        decomposition, ("implementation",), shipped_policy(source),
    ))
    assert (diagnostics == []) is valid
    if not valid:
        assert any(item["code"] == "phase.responsibility" for item in diagnostics)
