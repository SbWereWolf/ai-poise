"""C012: catalog-backed, phase-aware, deterministic decomposition diagnostics."""
from copy import deepcopy
import io
import json
from pathlib import Path

import pytest

from poise.modules.foundation.errors import DomainError
from poise.modules.tasks.decomposition import FocusedDecomposition

STAGES = ("planning", "implementation", "documentation")


def catalog():
    rows = [
        ("workflow", "meta", None, None), ("testing", "common", None, None),
        ("backend", "working", 1, "backend"),
        ("backend-format", "working", 2, "backend"),
        ("frontend", "working", 1, "frontend"),
    ]
    return {"schema": "poise-skill-catalog-1", "skills": [
        {"id": id, "path": f".agents/skills/{id}/SKILL.md", "purpose": f"Apply {id}.",
         "class": cls, "level": level, "responsibility": responsibility}
        for id, cls, level, responsibility in rows
    ]}


def policy():
    return {"catalog": catalog(), "skill_ids": ["workflow", "testing", "backend", "backend-format", "frontend"],
            "areas": [{"path": "app/**", "responsibility": "backend"},
                      {"path": "web/**", "responsibility": "frontend"},
                      {"path": "docs/**", "responsibility": "documentation"}]}


def inline_policy():
    return {"skills": [
        {"id": "backend", "class": "narrow", "responsibility": "backend"},
        {"id": "frontend", "class": "narrow", "responsibility": "frontend"},
        {"id": "testing", "class": "general", "responsibility": None},
        {"id": "workflow", "class": "meta", "responsibility": None}], "areas": policy()["areas"]}


def ordinary():
    return {"kind": "ordinary", "phases": [
        {"stage": "planning", "skills": ["workflow"], "areas": []},
        {"stage": "implementation", "skills": ["backend", "testing"], "areas": ["app/**"]},
        {"stage": "documentation", "skills": ["workflow"], "areas": ["docs/**"]},
    ], "integration": None}


def integration():
    d = ordinary()
    d["kind"] = "integration"
    d["phases"][1]["skills"].append("frontend")
    d["phases"][1]["areas"].append("web/**")
    d["integration"] = {"component_inputs": ["backend result", "frontend result"],
                        "combined_result": "Frontend consumes the backend contract.",
                        "integration_checks": ["Cross-component behavior"],
                        "allowed_paths": ["app/**", "web/**", "docs/**"]}
    return d


def diagnostics(d, p=None):
    assert hasattr(FocusedDecomposition, "inspect"), "C012 must expose complete diagnostics"
    return list(FocusedDecomposition.inspect(d, STAGES, p or policy()))


def test_catalog_is_consumed_without_redeclaring_skill_classes():
    routed = FocusedDecomposition.validate_policy(policy())
    assert routed["skills"]["backend"] == ("narrow", "backend")
    assert routed["skills"]["testing"] == ("general", None)
    FocusedDecomposition.parse(ordinary(), STAGES).validate(policy())
    assert diagnostics(ordinary()) == []


@pytest.mark.parametrize("edit", ["override", "unknown", "empty", "missing"])
def test_catalog_selection_is_explicit_and_cannot_be_overridden(edit):
    p = policy()
    if edit == "override": p["skills"] = inline_policy()["skills"]
    elif edit == "unknown": p["skill_ids"].append("missing")
    elif edit == "empty": p["skill_ids"] = []
    else: del p["skill_ids"]
    with pytest.raises(DomainError):
        FocusedDecomposition.validate_policy(p)


def test_excluded_skill_does_not_make_its_responsibility_non_narrow():
    p = policy()
    p["skill_ids"] = ["workflow", "testing", "backend"]
    d = ordinary()
    d["phases"][1]["areas"] = ["web/**"]
    errors = diagnostics(d, p)
    assert any(e["code"] == "phase.responsibility" and "frontend" in e["message"] for e in errors)


def test_skill_from_another_phase_cannot_cover_current_phase_area():
    d = ordinary()
    d["phases"][0]["skills"].append("backend")
    d["phases"][1]["skills"] = ["testing"]
    with pytest.raises(DomainError, match="implementation"):
        FocusedDecomposition.parse(d, STAGES).validate(inline_policy())
    errors = diagnostics(d)
    assert [e["code"] for e in errors] == ["phase.responsibility"]
    assert errors[0]["location"] == "decomposition.phases[implementation]"


def test_ordinary_task_cannot_distribute_peer_responsibilities_across_phases():
    d = ordinary()
    d["phases"][0].update(skills=["frontend"], areas=["web/**"])
    errors = diagnostics(d)
    assert {e["code"] for e in errors} == {"ordinary.skills", "ordinary.areas"}
    assert all(e["location"] == "decomposition" for e in errors)


def test_different_levels_do_not_bypass_responsibility_boundaries():
    p = policy()
    p["catalog"]["skills"][-1]["level"] = 2
    d = ordinary()
    d["phases"][1]["skills"].append("frontend")
    assert any(e["code"] == "ordinary.skills" for e in diagnostics(d, p))


def test_complementary_skills_same_owner_and_general_work_remain_valid():
    d = ordinary()
    d["phases"][1]["skills"].append("backend-format")
    assert diagnostics(d) == []
    for phase in d["phases"]:
        phase.update(skills=["workflow", "testing"], areas=["docs/**"])
    assert diagnostics(d) == []


def test_integration_allows_multiple_responsibilities_with_complete_contract():
    assert diagnostics(integration()) == []
    FocusedDecomposition.parse(integration(), STAGES).validate(policy())


def test_integration_scope_covers_general_areas_too():
    d = integration()
    d["integration"]["allowed_paths"].remove("docs/**")
    with pytest.raises(DomainError, match=r"outside allowed_paths.*docs"):
        FocusedDecomposition.parse(d, STAGES).validate(inline_policy())
    assert any(e["code"] == "integration.scope" and "docs/**" in e["message"] for e in diagnostics(d))


def test_integration_requires_all_fields_and_reports_them_together():
    d = integration()
    d["integration"] = {"component_inputs": [], "combined_result": " ", "integration_checks": [], "allowed_paths": []}
    errors = diagnostics(d)
    assert {e["location"] for e in errors if e["code"] == "integration.required"} == {
        "decomposition.integration.component_inputs", "decomposition.integration.combined_result",
        "decomposition.integration.integration_checks", "decomposition.integration.allowed_paths"}


@pytest.mark.parametrize("path", ["../secret/**", "/app/**", "C:/app/**", "app/../secret/**", "app//**", "app\\**", "app/./**"])
def test_area_and_allowed_path_validation_reject_escape_or_noncanonical_patterns(path):
    p = inline_policy()
    p["areas"][0]["path"] = path
    with pytest.raises(DomainError):
        FocusedDecomposition.validate_policy(p)
    d = integration()
    d["integration"]["allowed_paths"] = [path, "web/**", "docs/**"]
    with pytest.raises(DomainError):
        FocusedDecomposition.parse(d, STAGES)


def test_integration_pattern_coverage_is_sound_and_segment_aware():
    p = inline_policy()
    p["areas"][0]["path"] = "app/private/**"
    d = integration()
    d["phases"][1]["areas"][0] = "app/private/**"
    d["integration"]["allowed_paths"][0] = "app/public/**"
    assert any(e["code"] == "integration.scope" for e in diagnostics(d, p))
    d["integration"]["allowed_paths"] = ["**"]
    assert diagnostics(d, p) == []
    d["integration"]["allowed_paths"] = ["app/private/*", "web/**", "docs/**"]
    assert any(e["code"] == "integration.scope" for e in diagnostics(d, p))


def test_all_independent_structural_and_semantic_errors_are_retained():
    d = ordinary()
    d["phases"][0] = {"stage": "planning", "extra": True}
    d["phases"][1].update(skills=["unknown", "frontend"], areas=["unknown/**", "app/**"])
    d["phases"][2]["skills"] = ["workflow", "workflow"]
    errors = diagnostics(d)
    codes = {e["code"] for e in errors}
    assert {"phase.fields", "phase.skills", "skills.unknown", "areas.unknown", "phase.responsibility"} <= codes
    assert any("['areas', 'skills']" in e["message"] and "['extra']" in e["message"] for e in errors)


def test_diagnostics_do_not_depend_on_declaration_or_policy_order():
    d = ordinary()
    d["phases"][1].update(skills=["missing", "frontend"], areas=["no-route/**", "app/**"])
    p = policy()
    first = diagnostics(d, p)
    d["phases"].reverse()
    for phase in d["phases"]:
        phase["skills"].reverse(); phase["areas"].reverse()
    p["catalog"]["skills"].reverse(); p["skill_ids"].reverse(); p["areas"].reverse()
    assert diagnostics(d, p) == first


@pytest.mark.parametrize("value", [None, [], 1, "task", {"kind": [], "phases": [None, {}], "integration": None}])
def test_malformed_declarations_produce_domain_diagnostics_not_tracebacks(value):
    assert diagnostics(value)


def test_stage_identifiers_are_not_inferred_from_string_characters():
    with pytest.raises(DomainError):
        FocusedDecomposition.parse(ordinary(), "planning")


def cli(tmp_path, request):
    from poise.interfaces.skills import execute
    raw = catalog()
    for skill in raw["skills"]:
        path = tmp_path / skill["path"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("Body not loaded")
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps(raw))
    before = {str(p): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    output = io.StringIO()
    result = execute(path, tmp_path, io.BytesIO(json.dumps(request).encode()), output)
    after = {str(p): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    assert before == after
    return result, json.loads(output.getvalue())


def test_direct_policy_export_is_a_valid_project_policy_snapshot(tmp_path):
    p = policy()
    code, result = cli(tmp_path, {"operation": "policy", "skill_ids": p["skill_ids"], "areas": p["areas"]})
    assert code == 0, result
    assert "catalog" in result["policy"] and "skills" not in result["policy"]
    assert FocusedDecomposition.validate_policy(result["policy"])["skills"]["frontend"] == ("narrow", "frontend")


def test_readonly_batch_reports_every_task_and_independent_error(tmp_path):
    invalid = ordinary()
    invalid["phases"][1].update(skills=["missing", "frontend"], areas=["no-route/**", "app/**"])
    p = policy()
    tasks = [
        {"id": "GOOD", "stages": list(STAGES), "decomposition": ordinary()},
        {"id": "BROKEN", "stages": list(STAGES), "decomposition": invalid},
        {"id": "INTEGRATION", "stages": list(STAGES), "decomposition": integration()},
    ]
    code, result = cli(tmp_path, {"operation": "validate", "skill_ids": p["skill_ids"], "areas": p["areas"], "tasks": tasks})
    assert code == 1, result
    assert result["status"] == "invalid"
    rows = {r["task"]: r for r in result["results"]}
    assert rows["GOOD"]["valid"] is True
    assert rows["INTEGRATION"]["valid"] is True
    assert {"skills.unknown", "areas.unknown", "phase.responsibility"} <= {e["code"] for e in rows["BROKEN"]["diagnostics"]}
    tasks[1]["decomposition"] = ordinary()
    code, result = cli(tmp_path, {"operation": "validate", "skill_ids": p["skill_ids"], "areas": p["areas"], "tasks": tasks})
    assert code == 0 and result["status"] == "valid"


def test_public_task_creation_uses_full_diagnostics(project):
    from poise.modules.tasks.definition import validate_creation
    bad = deepcopy(project["task"])
    process = project["process"]
    stages = [s["id"] for s in process["stages"]]
    bad["decomposition"] = {"kind": "ordinary", "phases": [
        {"stage": s, "skills": ["missing"], "areas": ["no-route/**"]} for s in stages
    ], "integration": None}
    with pytest.raises(DomainError) as caught:
        validate_creation(bad, process, [], inline_policy())
    assert "unknown decomposition skills" in str(caught.value)
    assert "unrouted decomposition areas" in str(caught.value)


def test_selected_project_config_accepts_catalog_snapshot(project):
    from poise.common import load_config
    config_path = project["config_path"]
    raw = json.loads(config_path.read_text())
    raw["task_decomposition"] = policy()
    config_path.write_text(json.dumps(raw))
    _, loaded, _ = load_config(config_path)
    assert loaded["task_decomposition"] == policy()


def test_exact_multiphase_diagnostics_are_stable_and_keep_all_defects():
    d = ordinary()
    d["phases"][0].update(skills=["frontend"], areas=["web/**"])
    assert diagnostics(d) == [
        {"code": "ordinary.areas", "location": "decomposition",
         "message": "ordinary task combines unrelated areas: areas=['app/**:backend', 'web/**:frontend']"},
        {"code": "ordinary.skills", "location": "decomposition",
         "message": "ordinary task combines narrow responsibilities: skills=['backend:backend', 'frontend:frontend']"},
    ]
    with pytest.raises(DomainError) as caught:
        FocusedDecomposition.require_valid(d, STAGES, policy())
    assert list(caught.value.diagnostics) == diagnostics(d)
    assert str(caught.value) == "\n".join(e["message"] for e in diagnostics(d))


def test_batch_retains_valid_results_after_malformed_or_duplicate_items():
    from poise.application.skill_catalog import validate_tasks
    task = {"id": "OK", "stages": list(STAGES), "decomposition": ordinary()}
    rows = validate_tasks([None, task, {"id": "BAD"}, deepcopy(task)], policy())
    assert len(rows) == 4
    assert {r["task"] for r in rows} == {"$[0]", "BAD", "OK"}
    assert all(not r["valid"] for r in rows)
    assert all("task.duplicate" in {e["code"] for e in r["diagnostics"]} for r in rows if r["task"] == "OK")
    rows = validate_tasks([None, {"id": "BAD"}, task], policy())
    assert next(r for r in rows if r["task"] == "OK")["valid"] is True


@pytest.mark.parametrize("operation,extra", [
    ("unknown", {}),
    ("read", {"skill_ids": ["absent"]}),
    ("read", {"skill_ids": [], "extra": True}),
    ("validate", {"skill_ids": ["backend"], "areas": policy()["areas"], "tasks": []}),
])
def test_cli_invalid_requests_are_rejected_not_tracebacks(tmp_path, operation, extra):
    code, result = cli(tmp_path, {"operation": operation, **extra})
    assert code == 2 and result["status"] == "rejected"
    assert result["reason"]
