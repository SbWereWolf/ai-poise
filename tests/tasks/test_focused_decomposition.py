"""Focused Task decomposition contract for Task 0108."""

from __future__ import annotations

from copy import deepcopy

import pytest


STAGES = ("planning", "implementation", "documentation")


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

    documents = [
        root / "docs/workflows/batch-work.md",
        root / "docs/configuration/project-setup.md",
        root / "docs/governance/development-rules.md",
        root / ".agents/skills/poise/SKILL.md",
        root / ".agents/skills/poise-development/SKILL.md",
        root / "AGENTS.md",
    ]
    bodies = [path.read_text(encoding="utf-8") for path in documents]
    for token in (
        "task_decomposition",
        "ordinary",
        "integration",
        "component_inputs",
        "allowed_paths",
        "project-specific routing",
    ):
        assert any(token in body for body in bodies), token


def test_focused_decomposition_contract():
    run_contract()


def test_focused_decomposition_documentation_contract():
    run_documentation_contract()
