"""Editable acceptance drafts keep reviewed digests without versioned filenames."""
from __future__ import annotations

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


class ArtifactDraftOperationMissing(RuntimeError):
    pass


def task_record(runtime) -> dict:
    return runtime.task_queries.record("T1")


def artifact_records(runtime) -> list[dict]:
    return runtime.store.artifact_records("T1")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def draft_events(runtime) -> list[tuple[str, dict]]:
    with runtime.store.transaction() as db:
        rows = list(db.execute(
            "SELECT event,data FROM journal WHERE task_id=? "
            "AND event LIKE 'artifact_draft.%' ORDER BY seq",
            ("T1",),
        ))
    return [(row["event"], json.loads(row["data"])) for row in rows]


def protected_state(runtime, task_root: Path) -> dict:
    queries = {
        "tasks": "SELECT id,status,stage_index,iteration,claimed_by,version,current_submission_id,metadata FROM tasks ORDER BY id",
        "execution": "SELECT task_id,data,version FROM task_execution ORDER BY task_id",
        "submissions": "SELECT seq,task_id,stage,iteration,digest,data FROM submissions ORDER BY seq",
        "artifacts": "SELECT id,owner,scope,path,digest FROM artifacts ORDER BY id",
        "task_artifacts": "SELECT task_id,artifact_id FROM task_artifacts ORDER BY task_id,artifact_id",
        "sessions": "SELECT id,task_id FROM sessions ORDER BY id",
        "handoffs": "SELECT seq,actor,request_id,task_id,state,data FROM handoffs ORDER BY seq",
        "work_packets": "SELECT task_id,stage,iteration,digest FROM work_packets ORDER BY task_id,stage,iteration",
        "task_results": "SELECT task_id,submission_id,data FROM task_results ORDER BY task_id,submission_id",
        "task_events": "SELECT seq,task_id,version,at,data FROM task_events ORDER BY seq",
        "evidence": "SELECT id,task_id,stage,iteration,data FROM evidence ORDER BY id",
        "journal": "SELECT seq,session_id,task_id,event,data FROM journal ORDER BY seq",
    }
    with runtime.store.transaction() as db:
        database = {
            name: [tuple(row) for row in db.execute(statement)]
            for name, statement in queries.items()
        }
    files = {
        path.relative_to(task_root).as_posix(): path.read_bytes()
        for path in sorted(task_root.rglob("*"))
        if path.is_file()
    }
    return {"database": database, "files": files}


def declare(
    tools,
    runtime,
    path: str,
    request_id: str = "declare-analysis-v6",
    scope: str = "task",
) -> dict:
    return tools.invoke(request("artifact_drafts", {
        "action": "declare",
        "request_id": request_id,
        "task_id": "T1",
        "expected_version": task_record(runtime)["_version"],
        "drafts": [{"scope": scope, "path": path}],
        "reason": "The acceptance document remains editable through review and rework.",
    }))


def decide(tools, runtime, action: str, artifact_id: str, request_id: str) -> dict:
    return tools.invoke(request("artifact_drafts", {
        "action": action,
        "request_id": request_id,
        "task_id": "T1",
        "expected_version": task_record(runtime)["_version"],
        "artifact_ids": [artifact_id],
        "reason": f"The authorized reviewer records the {action} decision.",
        "authorization": f"Independent reviewer explicitly authorized {action} for this draft.",
    }))


def recover(tools, runtime, artifact_id: str, **changes) -> dict:
    payload = {
        "action": "recover",
        "request_id": "recover-erp-analysis-v6",
        "task_id": "T1",
        "expected_version": task_record(runtime)["_version"],
        "artifacts": [{
            "id": artifact_id,
            "scope": "task",
            "path": "ERP-AI-ANALYSIS-v6.md",
        }],
        "reason": "The acceptance file was registered as immutable before review rework ended.",
        "authorization": "The user explicitly authorized same-path correction of ERP-AI-ANALYSIS v6.",
    }
    payload.update(changes)
    return tools.invoke(request("artifact_drafts", payload))


def skip_only_missing_operation(exc: PoiseError) -> None:
    if str(exc) == "Unknown work operation":
        pytest.skip("The dedicated RED probe owns the missing-operation failure.")
    raise exc


def rework(tools) -> dict:
    return tools.invoke(request("bootstrap", {
        "task": None,
        "decision": "rework",
        "feedback": "Correct the reviewed acceptance draft in place.",
        "rework_stage": "tests",
    }))


def release(tools, request_id: str) -> dict:
    return tools.invoke(request("handoff", {
        "request_id": request_id,
        "reason": "Release the verified Task for an independent artifact decision.",
        "result": None,
        "commit_message": None,
        "artifact_paths": [],
    }))


def acquire_and_rework(project, session: str) -> tuple[WorkPoise, WorkTools, dict]:
    runtime = WorkPoise(project["config_path"], session)
    tools = WorkTools(runtime)
    tools.invoke(request("bootstrap", {
        "task": {"id": "T1"},
        "decision": None,
        "feedback": None,
        "rework_stage": None,
    }))
    return runtime, tools, rework(tools)


def test_artifact_drafts_public_operation_exists(project):
    configure(project)
    runtime = WorkPoise(project["config_path"], "operation-probe")
    tools = WorkTools(runtime)
    context = bootstrap(tools, project)
    add_test(context["worktree"])
    try:
        declare(tools, runtime, "probe.md", "probe-artifact-drafts-operation")
    except PoiseError as exc:
        if str(exc) == "Unknown work operation":
            raise ArtifactDraftOperationMissing(
                "ARTIFACT_DRAFT_LIFECYCLE_MISSING"
            ) from exc
        raise


def test_declared_draft_is_reviewed_twice_at_one_path_then_finalized(project):
    configure(project)
    runtime = WorkPoise(project["config_path"], "draft-owner")
    tools = WorkTools(runtime)
    context = bootstrap(tools, project)
    add_test(context["worktree"])

    try:
        opened = declare(tools, runtime, "ERP-AI-ANALYSIS-v6.md")
    except PoiseError as exc:
        skip_only_missing_operation(exc)

    task_root = Path(context["task_root"])
    for invalid_scope, invalid_path in (("runtime", "bad-scope.md"), ("task", "../bad-path.md")):
        before_invalid = protected_state(runtime, task_root)
        with pytest.raises(PoiseError, match="scope|path|owner|relative"):
            declare(
                tools,
                runtime,
                invalid_path,
                f"reject-{invalid_scope}-{invalid_path}",
                invalid_scope,
            )
        assert protected_state(runtime, task_root) == before_invalid

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
    assert opened["drafts"][0]["artifact_id"] == artifact_id

    release(tools, "release-first-review")
    review_runtime = WorkPoise(project["config_path"], "first-reviewer")
    first_review = decide(
        WorkTools(review_runtime), review_runtime, "review", artifact_id, "review-analysis-v6-1"
    )
    assert first_review["status"] == "artifact_drafts_reviewed"
    first_snapshot = Path(first_review["snapshots"][0]["snapshot_path"])
    assert first_snapshot.read_bytes() == b"initial reviewed text\n"
    assert first_review["snapshots"][0]["digest"] == hashlib.sha256(
        b"initial reviewed text\n"
    ).hexdigest()

    _, editor, second_context = acquire_and_rework(project, "draft-editor-2")
    artifact_path.write_text("corrected reviewed text\n", encoding="utf-8")
    second_result = result(second_context, "Review the corrected v6 draft at the same path.")
    second_result["artifact_paths"] = [str(artifact_path)]
    second = verify(editor, second_result)
    second_digest = sha256(artifact_path)

    assert {item["id"]: item["path"] for item in second["artifacts"]}[artifact_id] == str(
        artifact_path
    )
    assert second_digest != first_digest
    assert [event for event, _ in draft_events(runtime)] == [
        "artifact_draft.declared",
        "artifact_draft.revised",
        "artifact_draft.reviewed",
        "artifact_draft.revised",
    ]
    revisions = [data for event, data in draft_events(runtime)
                 if event == "artifact_draft.revised"]
    assert [(item["previous_digest"], item["digest"]) for item in revisions] == [
        (None, first_digest),
        (first_digest, second_digest),
    ]

    release(editor, "release-second-review")
    final_runtime = WorkPoise(project["config_path"], "final-reviewer")
    final_tools = WorkTools(final_runtime)
    second_review = decide(
        final_tools, final_runtime, "review", artifact_id, "review-analysis-v6-2"
    )
    second_snapshot = Path(second_review["snapshots"][0]["snapshot_path"])
    assert second_snapshot.read_bytes() == b"corrected reviewed text\n"
    second_literal_digest = hashlib.sha256(b"corrected reviewed text\n").hexdigest()
    assert second_review["snapshots"][0]["digest"] == second_literal_digest
    assert first_snapshot.read_bytes() == b"initial reviewed text\n"
    assert first_snapshot != second_snapshot

    closed = decide(
        final_tools, final_runtime, "finalize", artifact_id, "finalize-analysis-v6"
    )
    assert closed["status"] == "artifact_drafts_finalized"
    assert closed["artifact_ids"] == [artifact_id]
    assert closed["authorization"].startswith("Independent reviewer explicitly authorized")
    assert first_snapshot.read_bytes() == b"initial reviewed text\n"
    assert second_snapshot.read_bytes() == b"corrected reviewed text\n"
    persisted_reviews = [data for event, data in draft_events(final_runtime)
                         if event == "artifact_draft.reviewed"]
    assert [(item["digest"], item["snapshot_path"]) for item in persisted_reviews] == [
        (hashlib.sha256(b"initial reviewed text\n").hexdigest(), str(first_snapshot)),
        (second_literal_digest, str(second_snapshot)),
    ]
    assert not artifact_path.with_name("ERP-AI-ANALYSIS-v7.md").exists()

    _, immutable_tools, immutable_context = acquire_and_rework(project, "post-acceptance-editor")
    artifact_path.write_text("unauthorized post-acceptance edit\n", encoding="utf-8")
    immutable_result = result(immutable_context, "A finalized artifact cannot be revised.")
    immutable_result["artifact_paths"] = [str(artifact_path)]
    with pytest.raises(PoiseError, match="изменён после регистрации"):
        verify(immutable_tools, immutable_result)
    assert {item["id"]: item["digest"] for item in artifact_records(runtime)}[
        artifact_id
    ] == second_digest


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

    task_root = Path(context["task_root"])
    before_claimed = protected_state(owner_runtime, task_root)
    foreign_runtime = WorkPoise(project["config_path"], "foreign-recovery")
    try:
        recover(WorkTools(foreign_runtime), foreign_runtime, artifact["id"])
    except PoiseError as exc:
        if str(exc) == "Unknown work operation":
            pytest.skip("The dedicated RED probe owns the missing-operation failure.")
        assert "released" in str(exc) or "claim" in str(exc)
    else:
        pytest.fail("Foreign recovery unexpectedly changed a claimed Task.")
    assert protected_state(owner_runtime, task_root) == before_claimed
    with pytest.raises(PoiseError, match="immutable|registered|recover"):
        declare(owner, owner_runtime, "ERP-AI-ANALYSIS-v6.md", "declare-frozen-v6")

    release(owner, "release-erp-v6")
    recovery_runtime = WorkPoise(project["config_path"], "erp-recovery")
    recovery_tools = WorkTools(recovery_runtime)
    before = protected_state(recovery_runtime, task_root)
    with pytest.raises(PoiseError, match="version"):
        recover(
            recovery_tools,
            recovery_runtime,
            artifact["id"],
            request_id="stale-erp-v6",
            expected_version=task_record(recovery_runtime)["_version"] + 1,
        )
    assert protected_state(recovery_runtime, task_root) == before

    unauthorized = {
        "action": "recover",
        "request_id": "unauthorized-erp-v6",
        "task_id": "T1",
        "expected_version": task_record(recovery_runtime)["_version"],
        "artifacts": [{
            "id": artifact["id"],
            "scope": "task",
            "path": "ERP-AI-ANALYSIS-v6.md",
        }],
        "reason": "Attempt recovery without authority.",
        "authorization": "",
    }
    before = protected_state(recovery_runtime, task_root)
    with pytest.raises(PoiseError, match="authorization"):
        recovery_tools.invoke(request("artifact_drafts", unauthorized))
    assert protected_state(recovery_runtime, task_root) == before

    for request_id, target in (
        ("invalid-recovery-scope", {"id": artifact["id"], "scope": "sprint", "path": "ERP-AI-ANALYSIS-v6.md"}),
        ("invalid-recovery-path", {"id": artifact["id"], "scope": "task", "path": "different.md"}),
        ("unknown-recovery-id", {"id": "not-registered", "scope": "task", "path": "ERP-AI-ANALYSIS-v6.md"}),
    ):
        before_invalid = protected_state(recovery_runtime, task_root)
        with pytest.raises(PoiseError, match="registered|scope|path|identity|target"):
            recover(
                recovery_tools,
                recovery_runtime,
                artifact["id"],
                request_id=request_id,
                artifacts=[target],
            )
        assert protected_state(recovery_runtime, task_root) == before_invalid

    artifact_path = Path(verified["artifacts"][0]["path"])
    artifact_path.write_text("conflicting unreviewed bytes\n", encoding="utf-8")
    before_conflict = protected_state(recovery_runtime, task_root)
    with pytest.raises(PoiseError, match="digest|conflict|changed"):
        recover(
            recovery_tools,
            recovery_runtime,
            artifact["id"],
            request_id="conflicting-erp-v6",
        )
    assert protected_state(recovery_runtime, task_root) == before_conflict
    artifact_path.write_text("premature immutable review\n", encoding="utf-8")

    recovered = recover(recovery_tools, recovery_runtime, artifact["id"])
    assert recovered["status"] == "artifact_drafts_recovered"
    assert recovered["artifact_ids"] == [artifact["id"]]
    assert recover(recovery_tools, recovery_runtime, artifact["id"]) == recovered
    recovery = [data for event, data in draft_events(recovery_runtime)
                if event == "artifact_draft.recovered"]
    assert len(recovery) == 1
    assert recovery[0]["baseline_digest"] == original_digest
    assert recovery[0]["authorization"].startswith("The user explicitly authorized")
    baseline_snapshot = Path(recovered["snapshots"][0]["snapshot_path"])
    assert baseline_snapshot.read_bytes() == b"premature immutable review\n"

    before_replay_conflict = protected_state(recovery_runtime, task_root)
    with pytest.raises(PoiseError, match="request|conflict"):
        recover(
            recovery_tools,
            recovery_runtime,
            artifact["id"],
            reason="Different intent under a reused request ID.",
        )
    assert protected_state(recovery_runtime, task_root) == before_replay_conflict

    _, correction_tools, correction_context = acquire_and_rework(
        project, "erp-v6-correction"
    )
    artifact_path.write_text("corrected ERP analysis v6\n", encoding="utf-8")
    correction_result = result(correction_context, "Correct ERP v6 in place after recovery.")
    correction_result["artifact_paths"] = [str(artifact_path)]
    corrected = verify(correction_tools, correction_result)
    assert corrected["artifacts"][0]["id"] == artifact["id"]
    release(correction_tools, "release-corrected-erp-v6")
    corrected_review_runtime = WorkPoise(project["config_path"], "erp-v6-reviewer")
    corrected_review = decide(
        WorkTools(corrected_review_runtime),
        corrected_review_runtime,
        "review",
        artifact["id"],
        "review-corrected-erp-v6",
    )
    assert Path(corrected_review["snapshots"][0]["snapshot_path"]).read_bytes() == (
        b"corrected ERP analysis v6\n"
    )
    assert baseline_snapshot.read_bytes() == b"premature immutable review\n"
    assert not artifact_path.with_name("ERP-AI-ANALYSIS-v7.md").exists()


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
    next_context = rework(tools)
    path.write_text("conflicting bytes\n", encoding="utf-8")
    changed = result(next_context, "Reject changed immutable evidence.")
    changed["artifact_paths"] = [str(path)]
    before = protected_state(runtime, Path(context["task_root"]))
    with pytest.raises(PoiseError, match="изменён после регистрации"):
        verify(tools, changed)

    assert protected_state(runtime, Path(context["task_root"])) == before


def _run_as_registered_check() -> int:
    class FailureRecorder:
        def __init__(self):
            self.failures = []

        def pytest_runtest_logreport(self, report):
            if report.failed:
                self.failures.append((report.nodeid, report.when, str(report.longrepr)))

    recorder = FailureRecorder()
    stdout = StringIO()
    stderr = StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        exit_code = pytest.main(["-q", __file__], plugins=[recorder])
    captured_out = stdout.getvalue()
    captured_err = stderr.getvalue()
    if exit_code == 0:
        print("ARTIFACT_DRAFT_LIFECYCLE_VERIFIED")
        return 0
    expected = {"test_lifecycle.py::test_artifact_drafts_public_operation_exists"}
    observed = {nodeid.rsplit("/", 1)[-1] for nodeid, when, _ in recorder.failures
                if when == "call"}
    exact_missing_operation = (
        exit_code == 1
        and observed == expected
        and len(recorder.failures) == len(expected)
        and all(
            "ArtifactDraftOperationMissing: ARTIFACT_DRAFT_LIFECYCLE_MISSING" in detail
            and "Regex pattern did not match" not in detail
            for _, when, detail in recorder.failures if when == "call"
        )
    )
    if exact_missing_operation:
        print("ARTIFACT_DRAFT_LIFECYCLE_MISSING", file=sys.stderr)
        return 1
    sys.stdout.write(captured_out)
    sys.stderr.write(captured_err)
    return int(exit_code)


if __name__ == "__main__":
    raise SystemExit(_run_as_registered_check())
