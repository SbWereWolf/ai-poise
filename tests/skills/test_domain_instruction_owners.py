"""Declared Task, Sprint and verification owners have real scoped instructions."""
import json
from pathlib import Path

import pytest

from poise.infrastructure.development_routing import load_development_routing
from poise.infrastructure.skill_catalog import load_skill_catalog
from poise.modules.tasks.decomposition import FocusedDecomposition

ROOT = Path(__file__).resolve().parents[2]
SOURCES = (
    "config/project.example.json",
    "config/project-templates/linux-reference.json",
    "config/project-templates/wsl-poise.json",
)
OWNERS = (
    ("task-domain", "tasks"),
    ("sprint-domain", "sprints"),
    ("verification-domain", "verification"),
)
SCOPES = (
    ("src/poise/modules/tasks/domain.py", "task-domain"),
    ("src/poise/modules/sprints/domain.py", "sprint-domain"),
    ("src/poise/modules/verification/domain.py", "verification-domain"),
    ("docs/workflows/task-execution-contract.md", None),
    ("config/project.example.json", None),
)


def policy(source):
    document = json.loads((ROOT / source).read_text())
    return document.get("config", document)["task_decomposition"]


@pytest.mark.parametrize("source", SOURCES)
@pytest.mark.parametrize("owner,responsibility", OWNERS)
def test_template_owner_resolves_real_delivered_instruction(source, owner, responsibility):
    rows = [row for row in policy(source)["skills"] if row["id"] == owner]
    assert len(rows) == 1
    assert rows[0]["class"] == "narrow"
    assert rows[0]["responsibility"] == responsibility
    catalog = load_skill_catalog(ROOT / ".agents/skill-catalog.json", ROOT)
    entries = [entry for entry in catalog.skills if entry.id == owner]
    assert len(entries) == 1
    assert entries[0].path == f".agents/skills/{owner}/SKILL.md"
    assert entries[0].skill_class == "working"
    assert entries[0].level == 1
    assert entries[0].responsibility == responsibility
    assert catalog.decomposition_skills([owner]) == rows


@pytest.mark.parametrize("owner,responsibility", OWNERS)
@pytest.mark.parametrize("path,selected_owner", SCOPES)
def test_real_router_selects_only_the_matching_domain_owner(
    owner, responsibility, path, selected_owner,
):
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
    assert (owner in result["skills"]) is (owner == selected_owner)
    assert {"poise", "poise-development", "repo-tooling"} <= set(result["skills"])


@pytest.mark.parametrize("source", SOURCES)
@pytest.mark.parametrize("owner,responsibility", OWNERS)
def test_single_declared_domain_owner_is_a_valid_ordinary_phase(source, owner, responsibility):
    declaration = {
        "kind": "ordinary", "integration": None,
        "phases": [{"stage": "implementation", "skills": [owner], "areas": ["src/**"]}],
    }
    assert list(FocusedDecomposition.inspect(
        declaration, ("implementation",), policy(source),
    )) == []


@pytest.mark.parametrize("source", SOURCES)
@pytest.mark.parametrize("owners", [
    ["task-domain", "sprint-domain"],
    ["task-domain", "verification-domain"],
    ["sprint-domain", "verification-domain"],
])
def test_peer_domain_owners_do_not_become_one_ordinary_responsibility(source, owners):
    declaration = {
        "kind": "ordinary", "integration": None,
        "phases": [{"stage": "implementation", "skills": owners, "areas": ["src/**"]}],
    }
    errors = list(FocusedDecomposition.inspect(
        declaration, ("implementation",), policy(source),
    ))
    assert any(item["code"] == "ordinary.skills" for item in errors)
