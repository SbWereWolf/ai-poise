"""Focused contract for caller-supplied development check schedules."""
from __future__ import annotations

import json
from pathlib import Path
import runpy
import sys

import pytest

from poise.common import PoiseError
from poise.modules.catalogue.domain import TaskBlueprint
from poise.modules.goal_config.domain import GoalTypeDefinition
from poise.modules.verification.domain import CheckRegistry


ROOT = Path(__file__).resolve().parents[2]
TEMPLATE_PATH = ROOT / "config/catalogue/task-templates/development.json"
PROCESS_PATH = ROOT / "config/catalogue/process-templates/development.json"


def _documents() -> tuple[dict, dict]:
    process = json.loads(PROCESS_PATH.read_text())
    template = json.loads(TEMPLATE_PATH.read_text())
    return process, template


def _load_walkthrough() -> dict:
    examples = str(ROOT / "examples")
    sys.path.insert(0, examples)
    try:
        return runpy.run_path(str(ROOT / "examples/catalogue_walkthrough.py"))
    finally:
        sys.path.remove(examples)


def _empty_schedule(process: dict) -> dict[str, list[str]]:
    return {stage["id"]: [] for stage in process["stages"]}


def _baseline_method() -> dict:
    return {
        "id": "CUSTOM_BASELINE",
        "argv": [sys.executable, "-B", "-c", "print('CUSTOM_BASELINE')"],
        "cwd": ".",
        "environment": {},
        "source_under_test": {
            "kind": "external",
            "reason": "The fixture proves schedule rendering and does not inspect product source.",
        },
        "verification_plan": {
            "responsibility": "Exercise an explicitly supplied nonempty initial schedule.",
            "change_surface": [],
            "red_stages": [],
            "green_stages": ["baseline"],
            "red_failure": None,
        },
        "expected_exit_code": 0,
        "stdout_contains": ["CUSTOM_BASELINE"],
        "stderr_contains": [],
    }


def _parameters(process: dict, checks: dict, methods: list[dict] | None = None) -> dict:
    selected_methods = [] if methods is None else methods
    stages = [stage["id"] for stage in process["stages"]]
    return {
        "identity": "DEVELOPMENT-CHECKS",
        "membership": None,
        "goal": "Prove caller-supplied initial checks.",
        "requirements": ["The initial schedule is supplied by the caller."],
        "dod": ["The rendered Task preserves the exact schedule."],
        "executable_obligations": ["requirements[0]"],
        "methods": selected_methods,
        "method_inputs": [
            {
                "method_id": method["id"],
                "repository_inputs": [],
                "future_outputs": [],
                "reference_profile": {
                    "runner": "python",
                    "parser": "inline-no-path-arguments",
                    "version": 1,
                },
            }
            for method in selected_methods
        ],
        "checks": checks,
        "artifact_requirements": [],
        "contract": {"sections": [], "routes": [], "requirements": []},
        "evidence": {
            stage: {"subject_methods": {}, "arguments": [], "review_arguments": []}
            for stage in stages
        },
    }


def _instantiate(checks: dict, methods: list[dict] | None = None) -> dict:
    process, template = _documents()
    parsed_process = GoalTypeDefinition.parse(process).data
    return TaskBlueprint.parse(template).instantiate(
        _parameters(process, checks, methods),
        parsed_process,
        [],
    )


def test_development_template_requires_caller_supplied_checks_without_fixed_method_ids():
    _, template = _documents()
    assert template["parameters"]["checks"] == "object"
    assert template["task"]["checks"] == {"$input": "checks"}
    rendered = json.dumps(template["task"]["checks"], sort_keys=True)
    for method_id in ("BASELINE", "TEST_RED", "TEST_GREEN", "DOC_CHECK"):
        assert method_id not in rendered


def test_development_template_accepts_complete_empty_initial_schedule():
    process, _ = _documents()
    checks = _empty_schedule(process)
    created = _instantiate(checks)
    assert created["checks"] == checks
    assert CheckRegistry.from_task(
        created["methods"],
        created["checks"],
        tuple(checks),
    ).entries == ()


def test_development_template_accepts_explicit_nonempty_initial_schedule():
    process, _ = _documents()
    method = _baseline_method()
    checks = _empty_schedule(process)
    checks["baseline"] = [method["id"]]
    created = _instantiate(checks, [method])
    assert created["checks"]["baseline"] == ["CUSTOM_BASELINE"]
    assert created["checks"]["solution_planning"] == []


def test_development_template_does_not_default_missing_checks():
    process, template = _documents()
    parameters = _parameters(process, _empty_schedule(process))
    parameters.pop("checks")
    with pytest.raises(PoiseError):
        TaskBlueprint.parse(template).instantiate(
            parameters,
            GoalTypeDefinition.parse(process).data,
            [],
        )


@pytest.mark.parametrize("mutation", ["missing_stage", "extra_stage", "unknown_method"])
def test_development_template_reuses_exact_task_schedule_validation(mutation):
    process, _ = _documents()
    checks = _empty_schedule(process)
    if mutation == "missing_stage":
        checks.pop("documentation")
    elif mutation == "extra_stage":
        checks["not_a_process_stage"] = []
    else:
        checks["baseline"] = ["NOT_A_REGISTERED_METHOD"]
    with pytest.raises(PoiseError):
        _instantiate(checks)


def test_catalogue_identity_reference_and_example_use_explicit_checks():
    from poise.application.catalogue import CatalogueCommands
    from poise.infrastructure.catalogue import FileCatalogue
    from poise.modules.goal_config.domain import fingerprint

    template = json.loads(TEMPLATE_PATH.read_text())
    settings = json.loads((ROOT / "config/catalogue/settings.json").read_text())
    reference = json.loads((ROOT / "config/catalogue/reference.json").read_text())
    entry = settings["task_templates"]["development-v1"]
    assert entry["digest"] == fingerprint(template)
    development = next(
        item for item in reference["goal_types"] if item["id"] == "development"
    )
    assert development["method_schedule"] == {}
    assert reference["methods"]["development"] == {}
    walkthrough = _load_walkthrough()
    catalogue = FileCatalogue(ROOT / "config/catalogue/settings.json")
    process = GoalTypeDefinition.parse(json.loads(PROCESS_PATH.read_text())).data
    task = walkthrough["prepare_task"](
        catalogue,
        CatalogueCommands(catalogue, None, catalogue.raw["max_items"]),
        {"development": process},
        "development",
        "REAL-CONSUMER",
        ROOT,
    )
    assert task["checks"] == walkthrough["_initial_checks"](
        process,
        task["methods"],
    )
    assert task["checks"]["baseline"] == ["BASELINE"]
    assert task["checks"]["test_implementation"] == ["TEST_RED"]
    assert task["checks"]["implementation"] == ["TEST_GREEN"]


def test_canonical_guidance_distinguishes_initial_and_planned_checks():
    catalogue = (ROOT / "docs/configuration/process-catalogue.md").read_text()
    batch = (ROOT / "docs/workflows/batch-work.md").read_text()
    root_rules = (ROOT / "AGENTS.md").read_text()
    development_skill = (ROOT / ".agents/skills/poise-development/SKILL.md").read_text()
    assert "пустое начальное расписание" in catalogue
    assert "verification_planning" in catalogue
    assert "initial checks" in root_rules
    assert "initial checks" in development_skill
    assert "Пустое начальное расписание" in batch


def _run_red() -> int:
    _, template = _documents()
    fixed = template["task"].get("checks")
    old_ids = {"BASELINE", "TEST_RED", "TEST_GREEN", "DOC_CHECK"}
    if "checks" not in template["parameters"] and any(
        method_id in json.dumps(fixed) for method_id in old_ids
    ):
        print("EXPECTED_OLD_FIXED_SCHEDULE")
        return 1
    raise AssertionError("The exact old fixed-schedule defect was not reproduced")


def _run_green() -> int:
    test_development_template_requires_caller_supplied_checks_without_fixed_method_ids()
    test_development_template_accepts_complete_empty_initial_schedule()
    test_development_template_accepts_explicit_nonempty_initial_schedule()
    test_development_template_does_not_default_missing_checks()
    for mutation in ("missing_stage", "extra_stage", "unknown_method"):
        test_development_template_reuses_exact_task_schedule_validation(mutation)
    test_catalogue_identity_reference_and_example_use_explicit_checks()
    print("INITIAL_CHECKS_GREEN")
    return 0


def _run_documentation() -> int:
    test_canonical_guidance_distinguishes_initial_and_planned_checks()
    print("INITIAL_CHECKS_DOCUMENTATION_GREEN")
    return 0


if __name__ == "__main__":
    modes = {"red": _run_red, "green": _run_green, "documentation": _run_documentation}
    if len(sys.argv) != 2 or sys.argv[1] not in modes:
        raise SystemExit("usage: test_development_initial_checks.py red|green|documentation")
    raise SystemExit(modes[sys.argv[1]]())
