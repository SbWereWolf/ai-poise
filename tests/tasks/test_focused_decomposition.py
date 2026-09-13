"""Focused Task decomposition contract for Task 0108."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import subprocess
import sys

import pytest


STAGES = ("planning", "implementation", "documentation")
ERP_MIGRATED_SKILLS = {
    "api-contracts",
    "ddd",
    "debugging-and-recovery",
    "direct-checks",
    "documentation",
    "exec-task",
    "grilling",
    "infrastructure",
    "jetbrains-ide",
    "laravel",
    "layout-and-design",
    "modern-web-guidance",
    "moonshine-native",
    "performance-and-observability",
    "php-modernization",
    "phpunit",
    "playwright",
    "preflight",
    "redis-queues",
    "release-gate",
    "remediation",
    "remediation-review",
    "repo-tooling",
    "review",
    "safe-commit",
    "security-and-privacy",
    "self-review",
    "sprint-design",
    "sql",
    "strict-database-migrations",
    "system-tests",
    "tailwind-4-docs",
    "tdd",
    "vitest",
    "vue-best-practices",
    "vue-debug-guides",
    "vue-pinia-best-practices",
    "vue-router-best-practices",
}


def _policy():
    return {
        "skills": [
            {"id": "workflow", "class": "meta", "responsibility": None},
            {"id": "python", "class": "general", "responsibility": None},
            {"id": "task-domain", "class": "narrow", "responsibility": "tasks"},
            {"id": "sprint-domain", "class": "narrow", "responsibility": "sprints"},
            {"id": "task-storage", "class": "narrow", "responsibility": "tasks"},
        ],
        "areas": [
            {"path": "src/poise/modules/tasks/**", "responsibility": "tasks"},
            {"path": "src/poise/modules/sprints/**", "responsibility": "sprints"},
            {"path": "docs/**", "responsibility": "documentation"},
        ],
    }


def _ordinary(*, skills=("task-domain",), areas=("src/poise/modules/tasks/**",)):
    return {
        "kind": "ordinary",
        "phases": [
            {"stage": "planning", "skills": [], "areas": []},
            {"stage": "implementation", "skills": list(skills), "areas": list(areas)},
            {"stage": "documentation", "skills": ["workflow"], "areas": ["docs/**"]},
        ],
        "integration": None,
    }


def _integration():
    value = _ordinary(
        skills=("task-domain", "sprint-domain"),
        areas=("src/poise/modules/tasks/**", "src/poise/modules/sprints/**"),
    )
    value["kind"] = "integration"
    value["integration"] = {
        "component_inputs": ["validated Task plan", "validated Sprint plan"],
        "combined_result": "one publication decision",
        "integration_checks": ["cross-owner publication contract"],
        "allowed_paths": [
            "src/poise/modules/sprints/**",
            "src/poise/modules/tasks/**",
        ],
    }
    return value


def _assert_error(policy, declaration, expected):
    from poise.modules.tasks.decomposition import FocusedDecomposition
    from poise.modules.foundation.errors import DomainError

    with pytest.raises(DomainError) as error:
        FocusedDecomposition.parse(declaration, STAGES).validate(policy)
    assert str(error.value) == expected


def run_contract():
    from poise.modules.tasks.decomposition import FocusedDecomposition

    policy = _policy()

    for skills in (
        ("task-domain",),
        ("workflow", "python", "task-domain"),
        ("task-domain", "task-storage"),
    ):
        FocusedDecomposition.parse(_ordinary(skills=skills), STAGES).validate(policy)

    _assert_error(
        policy,
        _ordinary(skills=("sprint-domain", "task-domain")),
        "ordinary task combines narrow responsibilities: "
        "skills=['sprint-domain:sprints', 'task-domain:tasks']",
    )
    _assert_error(
        policy,
        _ordinary(areas=("src/poise/modules/sprints/**", "src/poise/modules/tasks/**")),
        "ordinary task combines unrelated areas: "
        "areas=['src/poise/modules/sprints/**:sprints', "
        "'src/poise/modules/tasks/**:tasks']",
    )

    phased = _ordinary()
    phased["phases"][0]["skills"] = ["sprint-domain"]
    _assert_error(
        policy,
        phased,
        "ordinary task combines narrow responsibilities: "
        "skills=['sprint-domain:sprints', 'task-domain:tasks']",
    )

    for label, phases, expected in (
        (
            "missing",
            _ordinary()["phases"][:-1],
            "decomposition phases mismatch: missing=['documentation']; "
            "duplicate=[]; unknown=[]",
        ),
        (
            "duplicate",
            _ordinary()["phases"] + [deepcopy(_ordinary()["phases"][0])],
            "decomposition phases mismatch: missing=[]; duplicate=['planning']; "
            "unknown=[]",
        ),
        (
            "unknown",
            _ordinary()["phases"]
            + [{"stage": "deployment", "skills": [], "areas": []}],
            "decomposition phases mismatch: missing=[]; duplicate=[]; "
            "unknown=['deployment']",
        ),
    ):
        malformed = _ordinary()
        malformed["phases"] = phases
        _assert_error(policy, malformed, expected)

    valid_integration = _integration()
    FocusedDecomposition.parse(valid_integration, STAGES).validate(policy)
    for field in (
        "component_inputs",
        "combined_result",
        "integration_checks",
        "allowed_paths",
    ):
        incomplete = deepcopy(valid_integration)
        incomplete["integration"][field] = [] if field != "combined_result" else ""
        _assert_error(
            policy,
            incomplete,
            f"integration task requires non-empty fields: ['{field}']",
        )

    outside = deepcopy(valid_integration)
    outside["integration"]["allowed_paths"] = ["src/poise/modules/tasks/**"]
    _assert_error(
        policy,
        outside,
        "integration areas outside allowed_paths: ['src/poise/modules/sprints/**']",
    )


def run_red_contract():
    try:
        run_contract()
    except ModuleNotFoundError as error:
        if error.name != "poise.modules.tasks.decomposition":
            raise
        print("focused-decomposition:red:behavior-unimplemented")
        raise SystemExit(1)
    print("focused-decomposition:red:unexpected-pass")
    raise SystemExit(2)


def run_green_contract():
    selected = [
        str(Path(__file__).resolve()),
        "--deselect",
        f"{Path(__file__).resolve()}::test_focused_decomposition_documentation_contract",
        "-q",
    ]
    completed = subprocess.run(
        [sys.executable, "-B", "-m", "pytest", *selected],
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode:
        raise AssertionError(completed.stdout + completed.stderr)

    unknown_skill = _ordinary(skills=("erp-private-route",))
    _assert_error(
        policy,
        unknown_skill,
        "unknown decomposition skills: ['erp-private-route']",
    )
    unknown_area = _ordinary(areas=("erp/private/**",))
    _assert_error(
        policy,
        unknown_area,
        "unrouted decomposition areas: ['erp/private/**']",
    )

    reversed_policy = {
        "skills": list(reversed(policy["skills"])),
        "areas": list(reversed(policy["areas"])),
    }
    reversed_declaration = deepcopy(phased)
    reversed_declaration["phases"] = list(reversed(reversed_declaration["phases"]))
    _assert_error(
        reversed_policy,
        reversed_declaration,
        "ordinary task combines narrow responsibilities: "
        "skills=['sprint-domain:sprints', 'task-domain:tasks']",
    )


def run_documentation_contract():
    from pathlib import Path
    import json

    root = Path(__file__).resolve().parents[2]
    project = json.loads(
        (root / "config/projects/ai-poise/project.json").read_text(encoding="utf-8")
    )
    policy = project["task_decomposition"]
    assert policy["skills"]
    assert policy["areas"]

    erp = json.loads(
        (root / "config/examples/erp-task-decomposition.example.json").read_text(
            encoding="utf-8"
        )
    )
    assert {item["id"] for item in erp["skills"]} == ERP_MIGRATED_SKILLS

    documents = [
        root / "docs/workflows/batch-work.md",
        root / "docs/configuration/project-setup.md",
        root / "docs/governance/development-rules.md",
        root / ".agents/skills/poise/SKILL.md",
        root / ".agents/skills/poise-development/SKILL.md",
        root / "AGENTS.md",
    ]
    required_by_file = {
        "batch-work.md": (
            "task_decomposition",
            "ordinary",
            "integration",
            "component_inputs",
            "allowed_paths",
        ),
        "project-setup.md": (
            "task_decomposition",
            "project-specific routing",
            "unknown decomposition skills",
        ),
        "development-rules.md": (
            "ordinary",
            "integration",
            "does not prove factual completeness",
        ),
        "SKILL.md": ("task_decomposition", "ordinary", "integration"),
        "AGENTS.md": (
            "project-specific routing",
            "does not prove factual completeness",
        ),
    }
    for path in documents:
        body = path.read_text(encoding="utf-8")
        for token in required_by_file[path.name]:
            assert token in body, (path, token)


def _peer_decomposition(stages):
    return {
        "kind": "ordinary",
        "phases": [
            {
                "stage": stage,
                "skills": ["task-domain", "sprint-domain"] if index == 0 else [],
                "areas": [],
            }
            for index, stage in enumerate(stages)
        ],
        "integration": None,
    }


def test_automatic_task_creation_rejects_unfocused_plan(project):
    from batch.helpers import configure, request
    from conftest import WorkPoise
    from poise.application.work import WorkTools
    from poise.modules.foundation.errors import PoiseError

    configure(project)
    client = WorkTools(WorkPoise(project["config_path"], "focused-auto"))
    contract = deepcopy(project["task"])
    contract["id"] = "FOCUSED-AUTO"
    contract["decomposition"] = _peer_decomposition(
        [stage["id"] for stage in project["process"]["stages"]]
    )
    with pytest.raises(PoiseError, match="ordinary task combines narrow responsibilities"):
        client.invoke(
            request(
                "bootstrap",
                {
                    "task": contract,
                    "decision": None,
                    "feedback": None,
                    "rework_stage": None,
                },
            )
        )


def test_newborn_ready_rejects_unfocused_plan(project):
    from batch.helpers import configure, request
    from conftest import WorkPoise
    from poise.application.work import WorkTools
    from poise.modules.foundation.errors import PoiseError

    configure(project)
    client = WorkTools(WorkPoise(project["config_path"], "focused-newborn"))
    born = client.invoke(
        request(
            "task",
            {
                "action": "create",
                "request_id": "focused-newborn-create",
                "task_id": "FOCUSED-NEWBORN",
                "sprint_id": None,
            },
        )
    )
    contract = deepcopy(project["task"])
    contract.pop("id")
    contract.pop("sprint_id")
    contract["decomposition"] = _peer_decomposition(
        [stage["id"] for stage in project["process"]["stages"]]
    )
    edited = client.invoke(
        request(
            "task",
            {
                "action": "edit",
                "request_id": "focused-newborn-edit",
                "task_id": born["task"],
                "expected_revision": born["revision"],
                "patch": contract,
            },
        )
    )
    with pytest.raises(PoiseError, match="ordinary task combines narrow responsibilities"):
        client.invoke(
            request(
                "task",
                {
                    "action": "ready",
                    "request_id": "focused-newborn-ready",
                    "task_id": born["task"],
                    "expected_revision": edited["revision"],
                },
            )
        )


def test_sprint_publish_rejects_unfocused_plan(project):
    from conftest import WorkPoise
    from poise.application.work import WorkTools
    from poise.modules.foundation.errors import PoiseError
    from sprints.helpers import draft, publish, setup, task

    configured = setup(project)
    client = WorkTools(WorkPoise(configured["config_path"], "focused-sprint"))
    child = task(configured)
    child["decomposition"] = _peer_decomposition(["work"])
    sprint = draft(client, [child])
    with pytest.raises(PoiseError, match="ordinary task combines narrow responsibilities"):
        publish(client, sprint["revision"])


def test_focused_decomposition_contract():
    run_contract()


def test_focused_decomposition_documentation_contract():
    run_documentation_contract()
