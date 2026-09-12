from __future__ import annotations

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
    )


def change(*operations: dict, request_id: str = "registry-1", revision: int = 0) -> dict:
    return {
        "request_id": request_id,
        "expected_revision": revision,
        "operations": list(operations),
    }


def operation(kind: str, method_id: str, **fields: object) -> dict:
    return {"kind": kind, "method_id": method_id, **fields}


def require_contract() -> None:
    assert hasattr(CheckRegistry, "apply_change"), (
        "CheckRegistry must own guarded, atomic current-registry mutation"
    )


def test_atomic_add_replace_reschedule_and_remove_are_one_current_projection_change():
    require_contract()
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


def test_change_has_exact_revision_and_request_id_replay_guards():
    require_contract()
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
    require_contract()
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

    conflicting = registration("CONFLICT", "move")
    with pytest.raises(DomainError, match="конфликт"):
        before.apply_change(
            change(operation("add", "CONFLICT", registration=conflicting))
        )
    with pytest.raises(DomainError, match="неизвест"):
        before.apply_change(change(operation("remove", "MISSING")))


def test_inspection_exit_requires_nonempty_complete_current_green_executable_coverage():
    require_contract()
    before = initial_registry()
    with pytest.raises(DomainError, match="GREEN executable-test"):
        before.validate_inspection_exit(OBLIGATIONS)

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
        partial.validate_inspection_exit(OBLIGATIONS)

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
    complete.validate_inspection_exit(OBLIGATIONS)


def configure_public_registry_case(project: dict) -> None:
    from conftest import write_json

    project["process"]["stages"][0]["sections"]["test_registry"] = (
        "Submit one guarded current-registry change."
    )
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
    project["task"]["checks"]["implementation"].append("CLEANUP_FULL_GREEN")
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


def test_sqlite_restart_preserves_current_projection_and_addressable_immutable_history(project):
    require_contract()
    from conftest import add_test
    from tests.batch.helpers import bootstrap, request, result, verify

    configure_public_registry_case(project)
    tools = public_tools(project, "registry-owner")
    boot = bootstrap(tools, project)
    add_test(boot["worktree"])
    payload = result(boot)
    payload["method_additions"] = change(
        operation("remove", "CLEANUP_FULL_GREEN")
    )
    assert verify(tools, payload)["status"] == "verified"

    restarted = public_tools(project, "registry-owner")
    query = restarted.invoke(
        request(
            "show",
            {
                "queries": [
                    {"id": "registry", "kind": "verification_registry"},
                ]
            },
        )
    )["results"][0]["value"]
    assert query["revision"] == 1
    assert "CLEANUP_FULL_GREEN" not in {
        item["method"]["id"] for item in query["current"]
    }
    assert "CLEANUP_FULL_GREEN" in {
        item["method"]["id"] for item in query["history"][0]["entries"]
    }
    assert query["history"][0]["revision"] == 0
    assert query["requests"][0]["request_id"] == "registry-1"


def test_public_work_api_allows_only_stages_with_test_registry_section(project):
    require_contract()
    from conftest import add_test
    from poise.common import PoiseError
    from tests.batch.helpers import bootstrap, request, result, verify

    configure_public_registry_case(project)
    tools = public_tools(project, "registry-public")
    boot = bootstrap(tools, project)
    add_test(boot["worktree"])
    assert verify(tools, result(boot))["status"] == "verified"
    inspection = tools.invoke(
        request(
            "bootstrap",
            {
                "task": None,
                "decision": "continue",
                "feedback": None,
                "rework_stage": None,
            },
        )
    )
    forbidden = result(inspection)
    forbidden["method_additions"] = change(
        operation("remove", "CLEANUP_FULL_GREEN")
    )
    with pytest.raises(PoiseError, match="test_registry"):
        verify(tools, forbidden)
