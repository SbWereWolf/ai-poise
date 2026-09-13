from __future__ import annotations

from copy import deepcopy
import json
import sqlite3

import pytest

from poise.modules.foundation.errors import DomainError
from poise.modules.verification.domain import CheckRegistry


STAGES = (
    "test_implementation",
    "test_inspection",
    "test_remediation",
    "implementation",
)
OBLIGATIONS = ("requirements[0]", "definition_of_done[0]")
OBLIGATION_CATALOG = (
    "requirements[0]",
    "requirements[1]",
    "definition_of_done[0]",
    "definition_of_done[1]",
)


def method(method_id: str, marker: str, *, green_stage: str = "implementation") -> dict:
    return {
        "id": method_id,
        "argv": ["python", "-B", "-c", f"print({marker!r})"],
        "cwd": ".",
        "environment": {},
        "source_under_test": {
            "kind": "repository",
            "bindings": [{"kind": "cwd", "path": "."}],
        },
        "verification_plan": {
            "responsibility": f"Prove {marker}.",
            "change_surface": ["src/**"],
            "red_stages": [],
            "green_stages": [green_stage],
            "red_failure": None,
        },
        "expected_exit_code": 0,
        "stdout_contains": [marker],
        "stderr_contains": [],
    }


def registration(
    method_id: str,
    marker: str,
    *,
    stage: str = "implementation",
    kind: str = "executable_test",
    covers: tuple[str, ...] = OBLIGATIONS,
) -> dict:
    return {
        "method": method(method_id, marker, green_stage=stage),
        "stages": [stage],
        "evidence_kind": kind,
        "covers": list(covers),
    }


def initial_registry() -> CheckRegistry:
    return CheckRegistry.from_items(
        [
            {"method": method("REPLACE", "old"), "stages": ["implementation"]},
            {"method": method("MOVE", "move"), "stages": ["implementation"]},
            {"method": method("DROP", "drop"), "stages": ["implementation"]},
        ],
        STAGES,
        require_source=True,
        require_plan=True,
    ).with_executable_obligations(
        OBLIGATIONS,
        ("test_inspection",),
        OBLIGATION_CATALOG,
    )


def change(
    *operations: dict,
    request_id: str = "registry-1",
    revision: int = 0,
    executable_obligations: tuple[str, ...] = OBLIGATIONS,
) -> dict:
    return {
        "request_id": request_id,
        "expected_revision": revision,
        "operations": list(operations),
        "executable_obligations": list(executable_obligations),
    }


def operation(kind: str, method_id: str, **fields: object) -> dict:
    return {"kind": kind, "method_id": method_id, **fields}


def test_atomic_add_replace_reschedule_and_remove_are_one_current_projection_change():
    before = initial_registry()
    applied = before.apply_change(
        change(
            operation("add", "ADDED", registration=registration("ADDED", "added")),
            operation(
                "replace",
                "REPLACE",
                registration=registration("REPLACE", "replacement"),
            ),
            operation("reschedule", "MOVE", stages=["test_remediation"]),
            operation("remove", "DROP"),
        )
    )

    assert before.revision == 0
    assert before.method_ids == ("REPLACE", "MOVE", "DROP")
    assert applied.registry.revision == 1
    assert applied.replayed is False
    assert applied.registry.method_ids == ("REPLACE", "MOVE", "ADDED")
    current = {item.method_id: item for item in applied.registry.entries}
    assert current["REPLACE"].to_dict()["method"]["stdout_contains"] == ["replacement"]
    assert current["MOVE"].stages == ("test_remediation",)
    assert current["MOVE"].to_dict()["method"]["verification_plan"]["green_stages"] == [
        "test_remediation"
    ]


def test_change_has_exact_revision_and_request_id_replay_guards():
    with pytest.raises(DomainError, match="executable_obligations"):
        initial_registry().apply_change(
            {
                "request_id": "missing-classification",
                "expected_revision": 0,
                "operations": [operation("remove", "DROP")],
            }
        )

    first_request = change(
        operation("replace", "REPLACE", registration=registration("REPLACE", "replacement"))
    )
    first = initial_registry().apply_change(first_request)
    replay = first.registry.apply_change(first_request)

    assert replay.replayed is True
    assert replay.registry == first.registry
    with pytest.raises(DomainError, match="request_id"):
        first.registry.apply_change(
            change(
                operation("replace", "REPLACE", registration=registration("REPLACE", "different")),
                request_id="registry-1",
                revision=0,
            )
        )
    with pytest.raises(DomainError, match="revision"):
        first.registry.apply_change(
            change(operation("remove", "MOVE"), request_id="registry-2", revision=0)
        )


def test_invalid_batch_is_atomic_and_rejects_duplicates_conflicts_and_dangling_targets():
    before = initial_registry()
    duplicate = registration("ADDED", "old")
    with pytest.raises(DomainError, match="дубликат"):
        before.apply_change(
            change(
                operation("add", "ADDED", registration=duplicate),
                operation("remove", "DROP"),
            )
        )
    assert before.revision == 0
    assert before.method_ids == ("REPLACE", "MOVE", "DROP")

    conflicting = registration("CONFLICT", "different-expectation")
    conflicting["method"]["argv"] = method("MOVE", "move")["argv"]
    with pytest.raises(DomainError, match="конфликт"):
        before.apply_change(
            change(operation("add", "CONFLICT", registration=conflicting))
        )
    with pytest.raises(DomainError, match="неизвест"):
        before.apply_change(change(operation("remove", "MISSING")))
    with pytest.raises(DomainError, match="неизвестные обязательства"):
        before.apply_change(
            change(
                operation("remove", "DROP"),
                executable_obligations=("requirements[9]",),
            )
        )


def test_inspection_exit_requires_nonempty_complete_current_green_executable_coverage():
    before = initial_registry()
    with pytest.raises(DomainError, match="GREEN executable-test"):
        before.validate_inspection_exit()

    partial = before.apply_change(
        change(
            operation(
                "replace",
                "REPLACE",
                registration=registration(
                    "REPLACE", "replacement", covers=("requirements[0]",)
                ),
            ),
            operation("remove", "MOVE"),
            operation("remove", "DROP"),
        )
    ).registry
    with pytest.raises(DomainError, match=r"definition_of_done\[0\]"):
        partial.validate_inspection_exit()

    complete = partial.apply_change(
        change(
            operation(
                "replace",
                "REPLACE",
                registration=registration("REPLACE", "replacement-complete"),
            ),
            request_id="registry-2",
            revision=1,
        )
    ).registry
    complete.validate_inspection_exit()
    assert complete.obligation_catalog == OBLIGATION_CATALOG
    assert complete.executable_obligations == OBLIGATIONS


def configure_public_registry_case(project: dict) -> None:
    from conftest import write_json

    stages = project["process"]["stages"]
    stage_ids = (
        "test_implementation",
        "test_inspection",
        "test_remediation",
        "implementation",
    )
    for stage, stage_id in zip(stages, stage_ids, strict=True):
        stage["id"] = stage_id
        stage["rework_targets"] = list(stage_ids)
    stages[0].update(
        handler="produce",
        transitions={"complete": "test_inspection"},
        read_only=False,
        allowed_paths=["tests/**"],
    )
    stages[0]["sections"]["test_registry"] = "Submit one guarded registry change."
    stages[1].update(
        handler="inspect",
        transitions={"clear": "implementation", "changes_requested": "test_remediation"},
        read_only=True,
        allowed_paths=[],
    )
    stages[2].update(
        handler="revise",
        transitions={"complete": "test_inspection"},
        read_only=False,
        allowed_paths=["tests/**"],
    )
    stages[2]["sections"]["test_registry"] = "Submit one guarded registry change."
    stages[3].update(
        handler="produce",
        transitions={"complete": None},
        read_only=False,
        allowed_paths=["src/**"],
    )
    project["process"]["route"]["entry"] = "test_implementation"
    project["task"]["requirements"].append(
        "Canonical documentation describes the registry lifecycle."
    )
    project["task"]["definition_of_done"].append(
        "The operator-facing workflow documentation is synchronized."
    )
    project["task"]["executable_obligations"] = list(OBLIGATIONS)

    methods = {item["id"]: item for item in project["task"]["methods"]}
    methods["RED"]["verification_plan"]["red_stages"] = ["test_implementation"]
    methods["GREEN"]["verification_plan"]["green_stages"] = ["implementation"]
    for inputs in project["task"]["method_inputs"]:
        for output in inputs["future_outputs"]:
            if output["producer_stage"] == "tests":
                output["producer_stage"] = "test_implementation"
    cleanup = method("CLEANUP_FULL_GREEN", "obsolete-full-suite")
    project["task"]["methods"].append(cleanup)
    project["task"]["method_inputs"].append(
        {
            "method_id": "CLEANUP_FULL_GREEN",
            "repository_inputs": [],
            "future_outputs": [],
            "reference_profile": {
                "runner": "python",
                "parser": "inline-no-path-arguments",
                "version": 1,
            },
        }
    )
    project["task"]["checks"] = {
        "test_implementation": ["RED"],
        "test_inspection": [],
        "test_remediation": [],
        "implementation": ["GREEN", "CLEANUP_FULL_GREEN"],
    }
    project["task"]["evidence_plan"] = {
        stage["id"]: {
            "subject_methods": {},
            "arguments": [],
            "review_arguments": [],
        }
        for stage in stages
    }
    project["task"]["stage_contracts"] = [
        {
            "stage_id": stage["id"],
            "allowed_paths": list(stage["allowed_paths"]),
            "entry_requirements": [],
            "exit_requirements": [],
        }
        for stage in stages
    ]
    project["cfg"]["automatic_checks"][0]["by_stage"] = {
        "test_implementation": ["RED"],
        "test_inspection": [],
        "test_remediation": [],
        "implementation": ["GREEN"],
    }
    write_json(
        project["root"] / "config/processes/development.json",
        project["process"],
    )


def public_tools(project: dict, session: str):
    from tests.batch.helpers import configure
    from poise.application.work import WorkTools
    from conftest import WorkPoise

    configure(project)
    return WorkTools(WorkPoise(project["config_path"], session))


def remove_creation_classification_from_stored_fixture(project: dict, task_id: str) -> None:
    """Represent a pre-contract stored Task; subsequent work must remain public."""
    database = (
        project["root"]
        / project["cfg"]["paths"]["state"]
        / project["cfg"]["paths"]["database"]
    )
    with sqlite3.connect(database) as connection:
        row = connection.execute(
            "SELECT metadata FROM tasks WHERE id=?",
            (task_id,),
        ).fetchone()
        metadata = json.loads(row[0])
        metadata["contract"].pop("executable_obligations")
        connection.execute(
            "UPDATE tasks SET metadata=? WHERE id=?",
            (json.dumps(metadata, ensure_ascii=False, sort_keys=True), task_id),
        )


def focused_product_registration(project: dict, covers: tuple[str, ...]) -> dict:
    focused = deepcopy(next(item for item in project["task"]["methods"] if item["id"] == "GREEN"))
    focused["argv"] = [
        focused["argv"][0],
        "-B",
        "-m",
        "unittest",
        "discover",
        "-s",
        "tests",
        "-p",
        "test_double.py",
        "-v",
    ]
    focused["verification_plan"]["responsibility"] = (
        "Execute the focused double(n) regression test for the declared requirement and DoD."
    )
    return {
        "method": focused,
        "stages": ["implementation"],
        "evidence_kind": "executable_test",
        "covers": list(covers),
    }


def test_sqlite_restart_preserves_current_projection_and_addressable_immutable_history(project):
    from conftest import add_test
    from tests.batch.helpers import bootstrap, request, result, verify

    configure_public_registry_case(project)
    tools = public_tools(project, "registry-owner")
    boot = bootstrap(tools, project)
    add_test(boot["worktree"])
    payload = result(boot)
    payload["method_additions"] = change(
        operation(
            "replace",
            "GREEN",
            registration=focused_product_registration(project, OBLIGATIONS),
        )
    )
    first = verify(tools, payload)
    assert first["status"] == "verified"
    red_receipt = next(item for item in first["checks"] if item["method"] == "RED")["id"]
    remove_creation_classification_from_stored_fixture(project, project["task"]["id"])

    inspection = tools.invoke(
        request(
            "bootstrap",
            {"task": None, "decision": "continue", "feedback": None, "rework_stage": None},
        )
    )
    inspected = result(inspection)
    inspected["stage_work"] = {
        "coverage": "Force the configured remediation route after complete coverage passes.",
        "findings": [
            {
                "id": "fixture-remediation",
                "subject": "current registry",
                "description": "Remove obsolete checks through the authorized remediation stage.",
                "evidence": "RED has an execution receipt and CLEANUP_FULL_GREEN is obsolete.",
            }
        ],
        "resolution_decisions": [],
    }
    assert verify(tools, inspected)["stage_outcome"] == "changes_requested"

    remediation = tools.invoke(
        request(
            "bootstrap",
            {"task": None, "decision": "continue", "feedback": None, "rework_stage": None},
        )
    )
    corrected = result(remediation)
    corrected["method_additions"] = change(
        operation("remove", "RED"),
        operation("remove", "CLEANUP_FULL_GREEN"),
        request_id="registry-2",
        revision=1,
    )
    corrected["stage_work"] = {
        "resolutions": [
            {
                "id": "fixture-resolution",
                "finding_id": "fixture-remediation",
                "description": "Removed the two obsolete current methods atomically.",
                "evidence": "The public verify response reports registry revision 2.",
            }
        ]
    }
    assert verify(tools, corrected)["status"] == "verified"

    restarted = public_tools(project, "registry-owner")
    values = restarted.invoke(
        request(
            "show",
            {
                "queries": [
                    {"id": "registry", "kind": "verification_registry"},
                    {"id": "evidence", "kind": "evidence"},
                ]
            },
        )
    )["results"]
    query = values[0]["value"]
    evidence = values[1]["value"]
    assert query["revision"] == 2
    assert "CLEANUP_FULL_GREEN" not in {
        item["method"]["id"] for item in query["current"]
    }
    assert "RED" not in {item["method"]["id"] for item in query["current"]}
    assert any(
        {"RED", "CLEANUP_FULL_GREEN"}
        <= {item["method"]["id"] for item in snapshot["entries"]}
        for snapshot in query["history"]
    )
    assert query["history"][0]["revision"] == 0
    assert [item["request_id"] for item in query["requests"]] == [
        "registry-1",
        "registry-2",
    ]
    preserved = next(item for item in evidence["observations"] if item["id"] == red_receipt)
    assert preserved["method"] == "RED" and preserved["passed"] is True

    reinspection = restarted.invoke(
        request(
            "bootstrap",
            {"task": None, "decision": "continue", "feedback": None, "rework_stage": None},
        )
    )
    reinspected = result(reinspection)
    reinspected["stage_work"] = {
        "coverage": "Verified the correction and complete current GREEN coverage.",
        "findings": [],
        "resolution_decisions": [
            {
                "resolution_id": "fixture-resolution",
                "decision": "accepted",
                "reason": "Current projection and original receipt are both addressable.",
            }
        ],
    }
    assert verify(restarted, reinspected)["status"] == "verified"
    continued = restarted.invoke(
        request(
            "bootstrap",
            {"task": None, "decision": "continue", "feedback": None, "rework_stage": None},
        )
    )
    assert continued["stage"] == "implementation"


def test_public_work_api_allows_only_stages_with_test_registry_section(project):
    from conftest import add_test
    from poise.common import PoiseError
    from tests.batch.helpers import bootstrap, request, result, verify

    configure_public_registry_case(project)
    unclassified = {**project, "task": deepcopy(project["task"])}
    unclassified["task"]["id"] = "REGISTRY-UNCLASSIFIED"
    unclassified["task"].pop("executable_obligations")
    with pytest.raises(PoiseError, match="executable_obligations"):
        bootstrap(public_tools(project, "registry-unclassified"), unclassified)

    cases = {
        "zero": [
            operation("remove", "GREEN"),
            operation("remove", "CLEANUP_FULL_GREEN"),
        ],
        "partial": [
            operation(
                "replace",
                "GREEN",
                registration=focused_product_registration(project, ("requirements[0]",)),
            ),
            operation("remove", "CLEANUP_FULL_GREEN"),
        ],
        "complete": [
            operation(
                "replace",
                "GREEN",
                registration=focused_product_registration(project, OBLIGATIONS),
            ),
            operation("remove", "CLEANUP_FULL_GREEN"),
        ],
    }
    for case, additions in cases.items():
        scenario = {**project, "task": deepcopy(project["task"])}
        scenario["task"]["id"] = f"REGISTRY-{case.upper()}"
        tools = public_tools(project, f"registry-{case}")
        boot = bootstrap(tools, scenario)
        add_test(boot["worktree"])
        prepared = result(boot)
        prepared["method_additions"] = change(
            *additions,
            request_id=f"registry-{case}",
        )
        assert verify(tools, prepared)["status"] == "verified"
        inspection = tools.invoke(
            request(
                "bootstrap",
                {"task": None, "decision": "continue", "feedback": None, "rework_stage": None},
            )
        )
        inspected = result(inspection)
        inspected["stage_work"] = {
            "coverage": f"Exercise {case} current executable coverage.",
            "findings": [],
            "resolution_decisions": [],
        }
        if case == "zero":
            with pytest.raises(PoiseError, match="GREEN executable-test"):
                verify(tools, inspected)
        elif case == "partial":
            with pytest.raises(PoiseError, match=r"definition_of_done\[0\]"):
                verify(tools, inspected)
        else:
            forbidden = deepcopy(inspected)
            forbidden["method_additions"] = change(
                operation("remove", "GREEN"),
                request_id="inspection-forbidden",
                revision=1,
            )
            with pytest.raises(PoiseError, match="test_registry"):
                verify(tools, forbidden)
            assert verify(tools, inspected)["status"] == "verified"
