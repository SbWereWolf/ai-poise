"""Focused Task decomposition contract for Task 0108."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import subprocess
import sys
from types import ModuleType

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
ERP_NARROW_RESPONSIBILITIES = {
    "api-contracts": "backend-contracts",
    "ddd": "domain-design",
    "infrastructure": "infrastructure",
    "laravel": "laravel-backend",
    "layout-and-design": "frontend-design",
    "modern-web-guidance": "frontend-design",
    "moonshine-native": "laravel-admin",
    "php-modernization": "laravel-backend",
    "phpunit": "backend-tests",
    "playwright": "browser-tests",
    "redis-queues": "queues",
    "sql": "database",
    "strict-database-migrations": "database",
    "system-tests": "system-tests",
    "tailwind-4-docs": "frontend-design",
    "vitest": "frontend-tests",
    "vue-best-practices": "vue-frontend",
    "vue-debug-guides": "vue-frontend",
    "vue-pinia-best-practices": "vue-frontend",
    "vue-router-best-practices": "vue-frontend",
}
ERP_META_SKILLS = {
    "exec-task",
    "grilling",
    "preflight",
    "release-gate",
    "remediation",
    "remediation-review",
    "review",
    "safe-commit",
    "self-review",
    "sprint-design",
}
ERP_GENERAL_SKILLS = ERP_MIGRATED_SKILLS - set(ERP_NARROW_RESPONSIBILITIES) - ERP_META_SKILLS
ERP_AREA_ROUTES = {
    "app/Domain/**": "domain-design",
    "app/Jobs/**": "queues",
    "app/MoonShine/**": "laravel-admin",
    "app/**": "laravel-backend",
    "database/**": "database",
    "infrastructure/**": "infrastructure",
    "resources/css/**": "frontend-design",
    "resources/js/**": "vue-frontend",
    "routes/**": "backend-contracts",
    "tests/Browser/**": "browser-tests",
    "tests/Feature/**": "system-tests",
    "tests/Frontend/**": "frontend-tests",
    "tests/Unit/**": "backend-tests",
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

    for phases, expected in (
        (
            _ordinary()["phases"][:-1],
            "decomposition phases mismatch: missing=['documentation']; "
            "duplicate=[]; unknown=[]",
        ),
        (
            _ordinary()["phases"] + [deepcopy(_ordinary()["phases"][0])],
            "decomposition phases mismatch: missing=[]; duplicate=['planning']; "
            "unknown=[]",
        ),
        (
            _ordinary()["phases"]
            + [{"stage": "deployment", "skills": [], "areas": []}],
            "decomposition phases mismatch: missing=[]; duplicate=[]; "
            "unknown=['deployment']",
        ),
    ):
        malformed = _ordinary()
        malformed["phases"] = phases
        _assert_error(policy, malformed, expected)

    missing_skills = _ordinary()
    missing_skills["phases"][0].pop("skills")
    _assert_error(
        policy,
        missing_skills,
        "decomposition phase planning: отсутствуют ['skills']; неизвестные поля []",
    )
    duplicate_skills = _ordinary()
    duplicate_skills["phases"][1]["skills"] = ["task-domain", "task-domain"]
    _assert_error(
        policy,
        duplicate_skills,
        "decomposition phase implementation skills: requires unique strings",
    )

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


def run_red_contract():
    from _pytest.outcomes import Failed

    class AcceptEverything:
        @classmethod
        def parse(cls, declaration, stages):
            return cls()

        def validate(self, policy):
            return None

    controlled = ModuleType("poise.modules.tasks.decomposition")
    controlled.FocusedDecomposition = AcceptEverything
    name = controlled.__name__
    previous = sys.modules.get(name)
    sys.modules[name] = controlled
    try:
        run_contract()
    except Failed as error:
        if "DID NOT RAISE" not in str(error):
            raise
        print("focused-decomposition:red:no-op-validator-accepted-invalid-plan")
        raise SystemExit(1)
    finally:
        if previous is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = previous
    raise AssertionError("controlled no-op validator unexpectedly satisfied the contract")


def _run_behavior_tests():
    selected = [
        str(Path(__file__).resolve()),
        "-k",
        "not focused_decomposition_documentation_contract",
        "-q",
    ]
    return subprocess.run(
        [sys.executable, "-B", "-m", "pytest", *selected],
        text=True,
        capture_output=True,
        check=False,
    )


def run_green_contract():
    completed = _run_behavior_tests()
    if completed.returncode:
        raise AssertionError(completed.stdout + completed.stderr)


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

    inventory = json.loads(
        (root / "config/examples/erp-migrated-skills.json").read_text(
            encoding="utf-8"
        )
    )
    assert set(inventory["skills"]) == ERP_MIGRATED_SKILLS

    erp = json.loads(
        (root / "config/examples/erp-task-decomposition.example.json").read_text(
            encoding="utf-8"
        )
    )
    assert {item["id"] for item in erp["skills"]} == ERP_MIGRATED_SKILLS
    by_id = {item["id"]: item for item in erp["skills"]}
    for skill_id, item in by_id.items():
        if skill_id in ERP_NARROW_RESPONSIBILITIES:
            assert item == {
                "id": skill_id,
                "class": "narrow",
                "responsibility": ERP_NARROW_RESPONSIBILITIES[skill_id],
            }
        else:
            assert item == {
                "id": skill_id,
                "class": "meta" if skill_id in ERP_META_SKILLS else "general",
                "responsibility": None,
            }
    assert set(by_id) == (
        set(ERP_NARROW_RESPONSIBILITIES) | ERP_META_SKILLS | ERP_GENERAL_SKILLS
    )
    actual_routes = {
        item["path"]: item["responsibility"] for item in erp["areas"]
    }
    assert actual_routes == ERP_AREA_ROUTES, (
        f"ERP area routes mismatch: expected={ERP_AREA_ROUTES!r}; "
        f"actual={actual_routes!r}"
    )
    routed = set(ERP_AREA_ROUTES.values())
    missing_routes = sorted(set(ERP_NARROW_RESPONSIBILITIES.values()) - routed)
    assert not missing_routes, (
        f"ERP narrow responsibilities lack area routes: {missing_routes}"
    )

    documents = [
        root / "docs/workflows/batch-work.md",
        root / "docs/configuration/project-setup.md",
        root / "docs/governance/development-rules.md",
        root / ".agents/skills/poise/SKILL.md",
        root / ".agents/skills/poise-development/SKILL.md",
        root / "AGENTS.md",
    ]
    required_by_file = {
        "docs/workflows/batch-work.md": (
            "Каждая фаза процесса явно объявляет skills и areas",
            "Классы skills — meta, general и narrow",
            "обычная Task не объединяет разные narrow responsibilities",
            "integration Task обязана объявить component_inputs, combined_result, integration_checks и allowed_paths",
            "Порядок деклараций не влияет на решение или диагностику",
        ),
        "docs/configuration/project-setup.md": (
            "task_decomposition хранит project-specific routing",
            "неизвестный skill или неразмеченная area отклоняют план",
            "ERP inventory и его классификация принадлежат конфигурации ERP, а не AI poise",
        ),
        "docs/governance/development-rules.md": (
            "ordinary и integration классифицируются до проверки фокуса",
            "валидатор проверяет декларацию, но не доказывает её фактическую полноту",
            "meta и general не требуют разделения, narrow следует responsibility boundary",
        ),
        ".agents/skills/poise/SKILL.md": (
            "Read task_decomposition from the selected project's project-specific routing.",
            "Declare every process phase before an ordinary or integration Task is made ready.",
            "Meta and general skills do not split a Task; peer narrow responsibilities do.",
        ),
        ".agents/skills/poise-development/SKILL.md": (
            "An integration Task names component_inputs, combined_result, integration_checks, and allowed_paths.",
            "Validation does not prove factual completeness of declared skills or boundaries.",
            "Keep target-project skill inventory and area routing out of AI poise source.",
        ),
        "AGENTS.md": (
            "Read skill classes and responsibility boundaries from project-specific routing.",
            "Decomposition validation does not prove factual completeness.",
            "Ordinary Tasks cannot combine peer narrow responsibilities; integration Tasks require explicit integration fields.",
        ),
    }
    for path in documents:
        body = path.read_text(encoding="utf-8")
        relative = path.relative_to(root).as_posix()
        for token in required_by_file[relative]:
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


def _valid_decomposition(stages):
    return {
        "kind": "ordinary",
        "phases": [
            {"stage": stage, "skills": ["task-domain"], "areas": []}
            for stage in stages
        ],
        "integration": None,
    }


def _valid_integration_decomposition(stages):
    return {
        "kind": "integration",
        "phases": [
            {
                "stage": stage,
                "skills": ["task-domain", "sprint-domain"],
                "areas": [],
            }
            for stage in stages
        ],
        "integration": {
            "component_inputs": ["Task component", "Sprint component"],
            "combined_result": "Published Sprint tasks",
            "integration_checks": ["Sprint publication preflight"],
            "allowed_paths": ["src/poise/modules/**"],
        },
    }


def _lifecycle_state(client, project):
    tables = (
        "tasks",
        "task_execution",
        "task_events",
        "sessions",
        "session_sprints",
        "journal",
        "sprints",
        "sprint_layers",
        "sprint_members",
        "sprint_dependencies",
        "sprint_requests",
    )
    with client.runtime.store.transaction() as database:
        rows = {
            table: sorted(
                tuple(row) for row in database.execute(f"SELECT * FROM {table}")
            )
            for table in tables
        }
    rows["worktree_paths"] = sorted(
        path.name
        for path in (
            client.runtime.state / client.runtime.paths["worktrees"]
        ).glob("*")
    )
    return rows


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
    before = _lifecycle_state(client, project)
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
    assert _lifecycle_state(client, project) == before

    valid = deepcopy(project["task"])
    valid["id"] = "FOCUSED-VALID"
    valid["decomposition"] = _valid_decomposition(
        [stage["id"] for stage in project["process"]["stages"]]
    )
    started = client.invoke(
        request(
            "bootstrap",
            {
                "task": valid,
                "decision": None,
                "feedback": None,
                "rework_stage": None,
            },
        )
    )
    assert started["status"] == "active"


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
    before = _lifecycle_state(client, project)
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
    assert _lifecycle_state(client, project) == before

    valid_born = client.invoke(
        request(
            "task",
            {
                "action": "create",
                "request_id": "focused-valid-newborn-create",
                "task_id": "FOCUSED-VALID-NEWBORN",
                "sprint_id": None,
            },
        )
    )
    valid_contract = deepcopy(project["task"])
    valid_contract.pop("id")
    valid_contract.pop("sprint_id")
    valid_contract["decomposition"] = _valid_decomposition(
        [stage["id"] for stage in project["process"]["stages"]]
    )
    valid_edited = client.invoke(
        request(
            "task",
            {
                "action": "edit",
                "request_id": "focused-valid-newborn-edit",
                "task_id": valid_born["task"],
                "expected_revision": valid_born["revision"],
                "patch": valid_contract,
            },
        )
    )
    ready = client.invoke(
        request(
            "task",
            {
                "action": "ready",
                "request_id": "focused-valid-newborn-ready",
                "task_id": valid_born["task"],
                "expected_revision": valid_edited["revision"],
            },
        )
    )
    assert ready["status"] == "available"


def test_sprint_publish_rejects_unfocused_plan(project):
    from conftest import WorkPoise
    from poise.application.work import WorkTools
    from poise.modules.foundation.errors import PoiseError
    from sprints.helpers import draft, publish, setup, task

    configured = setup(project)
    client = WorkTools(WorkPoise(configured["config_path"], "focused-sprint"))
    child = task(configured)
    valid = deepcopy(child)
    valid["decomposition"] = _valid_integration_decomposition(["work"])
    child["decomposition"] = _peer_decomposition(["work"])
    sprint = draft(client, [child])
    before = _lifecycle_state(client, project)
    with pytest.raises(PoiseError, match="ordinary task combines narrow responsibilities"):
        publish(client, sprint["revision"])
    assert _lifecycle_state(client, project) == before

    revised = draft(
        client,
        [valid],
        revision=sprint["revision"],
        request_id="focused-sprint-fix",
    )
    published = publish(client, revised["revision"], request_id="focused-sprint-publish")
    assert published["status"] == "planned"


def test_focused_decomposition_contract():
    run_contract()


def test_focused_decomposition_documentation_contract():
    run_documentation_contract()
