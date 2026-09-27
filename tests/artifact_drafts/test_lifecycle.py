"""Editable acceptance drafts keep reviewed digests without versioned filenames."""
from __future__ import annotations

from copy import deepcopy
from io import StringIO
from pathlib import Path
import contextlib
import hashlib
import json
import sys

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY_ROOT))
sys.path.insert(0, str(REPOSITORY_ROOT / "tests"))

from conftest import WorkPoise, add_test
from poise.application.work import WorkTools
from poise.common import PoiseError
from tests.batch.helpers import (
    bootstrap,
    configure,
    request,
    result,
    text_artifact,
    verify,
)


def task_record(runtime) -> dict:
    return runtime.task_queries.record("T1")


def artifact_records(runtime) -> list[dict]:
    return runtime.store.artifact_records("T1")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def draft_events(runtime) -> list[tuple[str, dict]]:
    with runtime.store.transaction() as db:
        rows = db.execute(
            "SELECT event,data FROM journal WHERE task_id=? "
            "AND event LIKE 'artifact_draft.%' ORDER BY seq",
            ("T1",),
        )
    return [(row["event"], json.loads(row["data"])) for row in rows]


def declare(tools, runtime, path: str, request_id: str = "declare-analysis-v6") -> dict:
    return tools.invoke(request("artifact_drafts", {
        "action": "declare",
        "request_id": request_id,
        "task_id": "T1",
        "expected_version": task_record(runtime)["_version"],
        "drafts": [{"scope": "task", "path": path}],
        "reason": "The acceptance document remains editable through review and rework.",
    }))


def finalize(tools, runtime, artifact_id: str) -> dict:
    return tools.invoke(request("artifact_drafts", {
        "action": "finalize",
        "request_id": "finalize-analysis-v6",
        "task_id": "T1",
        "expected_version": task_record(runtime)["_version"],
        "artifact_ids": [artifact_id],
        "reason": "The accepted draft is now final immutable evidence.",
    }))


def recover(tools, runtime, artifact_id: str, **changes) -> dict:
    payload = {
        "action": "recover",
        "request_id": "recover-erp-analysis-v6",
        "task_id": "T1",
        "expected_version": task_record(runtime)["_version"],
        "artifact_ids": [artifact_id],
        "reason": "The acceptance file was registered as immutable before review rework ended.",
        "authorization": "The user explicitly authorized same-path correction of ERP-AI-ANALYSIS v6.",
    }
    payload.update(changes)
    return tools.invoke(request("artifact_drafts", payload))


def rework(tools) -> dict:
    return tools.invoke(request("bootstrap", {
        "task": None,
        "decision": "rework",
        "feedback": "Correct the reviewed acceptance draft in place.",
        "rework_stage": "tests",
    }))


def test_declared_draft_is_reviewed_twice_at_one_path_then_finalized(project):
    configure(project)
    runtime = WorkPoise(project["config_path"], "draft-owner")
    tools = WorkTools(runtime)
    context = bootstrap(tools, project)
    add_test(context["worktree"])

    opened = declare(tools, runtime, "ERP-AI-ANALYSIS-v6.md")
    assert opened["status"] == "artifact_drafts_declared"
    assert opened["drafts"] == [{
        "artifact_id": opened["drafts"][0]["artifact_id"],
        "scope": "task",
        "path": "ERP-AI-ANALYSIS-v6.md",
        "state": "open",
    }]

    first = verify(
        tools,
        result(context, "Review the initial v6 acceptance draft."),
        [text_artifact("task", "ERP-AI-ANALYSIS-v6.md", "initial reviewed text\n")],
    )
    artifact_id = first["artifacts"][0]["id"]
    artifact_path = Path(first["artifacts"][0]["path"])
    first_digest = sha256(artifact_path)

    second_context = rework(tools)
    artifact_path.write_text("corrected reviewed text\n", encoding="utf-8")
    second_result = result(second_context, "Review the corrected v6 draft at the same path.")
    second_result["artifact_paths"] = [str(artifact_path)]
    second = verify(tools, second_result)
    second_digest = sha256(artifact_path)

    assert second["artifacts"] == [{"id": artifact_id, "path": str(artifact_path)}]
    assert second_digest != first_digest
    assert [event for event, _ in draft_events(runtime)] == [
        "artifact_draft.declared",
        "artifact_draft.reviewed",
        "artifact_draft.reviewed",
    ]
    reviewed = [data for event, data in draft_events(runtime)
                if event == "artifact_draft.reviewed"]
    assert [(item["previous_digest"], item["digest"]) for item in reviewed] == [
        (None, first_digest),
        (first_digest, second_digest),
    ]

    closed = finalize(tools, runtime, artifact_id)
    assert closed["status"] == "artifact_drafts_finalized"
    assert closed["artifact_ids"] == [artifact_id]
    assert not artifact_path.with_name("ERP-AI-ANALYSIS-v7.md").exists()

    immutable_context = rework(tools)
    artifact_path.write_text("unauthorized post-acceptance edit\n", encoding="utf-8")
    immutable_result = result(immutable_context, "A finalized artifact cannot be revised.")
    immutable_result["artifact_paths"] = [str(artifact_path)]
    with pytest.raises(PoiseError, match="изменён после регистрации"):
        verify(tools, immutable_result)
    assert artifact_records(runtime)[0]["digest"] == second_digest


def test_premature_erp_v6_registration_requires_released_authorized_recovery(project):
    configure(project)
    owner_runtime = WorkPoise(project["config_path"], "erp-owner")
    owner = WorkTools(owner_runtime)
    context = bootstrap(owner, project)
    add_test(context["worktree"])
    verified = verify(
        owner,
        result(context, "Register the prematurely frozen ERP v6 file."),
        [text_artifact("task", "ERP-AI-ANALYSIS-v6.md", "premature immutable review\n")],
    )
    artifact = artifact_records(owner_runtime)[0]
    original_digest = artifact["digest"]

    with pytest.raises(PoiseError, match="released|claim"):
        recover(owner, owner_runtime, artifact["id"])
    with pytest.raises(PoiseError, match="immutable|registered|recover"):
        declare(owner, owner_runtime, "ERP-AI-ANALYSIS-v6.md", "declare-frozen-v6")

    owner.invoke(request("handoff", {
        "request_id": "release-erp-v6",
        "reason": "Release the Task before explicit artifact-draft recovery.",
        "result": None,
        "commit_message": None,
        "artifact_paths": [],
    }))
    recovery_runtime = WorkPoise(project["config_path"], "erp-recovery")
    recovery_tools = WorkTools(recovery_runtime)
    before = draft_events(recovery_runtime)
    with pytest.raises(PoiseError, match="version"):
        recover(
            recovery_tools,
            recovery_runtime,
            artifact["id"],
            request_id="stale-erp-v6",
            expected_version=task_record(recovery_runtime)["_version"] + 1,
        )
    assert draft_events(recovery_runtime) == before

    recovered = recover(recovery_tools, recovery_runtime, artifact["id"])
    assert recovered["status"] == "artifact_drafts_recovered"
    assert recovered["artifact_ids"] == [artifact["id"]]
    assert recover(recovery_tools, recovery_runtime, artifact["id"]) == recovered
    recovery = [data for event, data in draft_events(recovery_runtime)
                if event == "artifact_draft.recovered"]
    assert len(recovery) == 1
    assert recovery[0]["baseline_digest"] == original_digest
    assert recovery[0]["authorization"].startswith("The user explicitly authorized")
    assert not Path(verified["artifacts"][0]["path"]).with_name(
        "ERP-AI-ANALYSIS-v7.md"
    ).exists()


def test_ordinary_registered_artifact_stays_immutable_and_rejection_is_atomic(project):
    configure(project)
    runtime = WorkPoise(project["config_path"], "immutable-owner")
    tools = WorkTools(runtime)
    context = bootstrap(tools, project)
    add_test(context["worktree"])
    verified = verify(
        tools,
        result(context, "Register genuine immutable evidence."),
        [text_artifact("task", "immutable.txt", "fixed evidence\n")],
    )
    path = Path(verified["artifacts"][0]["path"])
    registered = deepcopy(artifact_records(runtime))
    events = deepcopy(draft_events(runtime))

    next_context = rework(tools)
    path.write_text("conflicting bytes\n", encoding="utf-8")
    changed = result(next_context, "Reject changed immutable evidence.")
    changed["artifact_paths"] = [str(path)]
    with pytest.raises(PoiseError, match="изменён после регистрации"):
        verify(tools, changed)

    assert artifact_records(runtime) == registered
    assert draft_events(runtime) == events


def _run_as_registered_check() -> int:
    stdout = StringIO()
    stderr = StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        exit_code = pytest.main(["-q", __file__])
    captured_out = stdout.getvalue()
    captured_err = stderr.getvalue()
    if exit_code == 0:
        print("ARTIFACT_DRAFT_LIFECYCLE_VERIFIED")
        return 0
    combined = captured_out + captured_err
    if exit_code == 1 and "Unknown work operation" in combined:
        print("ARTIFACT_DRAFT_LIFECYCLE_MISSING", file=sys.stderr)
        return 1
    sys.stdout.write(captured_out)
    sys.stderr.write(captured_err)
    return int(exit_code)


if __name__ == "__main__":
    raise SystemExit(_run_as_registered_check())
