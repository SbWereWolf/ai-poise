"""Participants continue the same work; cancelled drafts release their materials."""

from copy import deepcopy
from pathlib import Path

import pytest

from batch.helpers import configure, request, result, verify
from conftest import WorkPoise, add_test, git
from poise.application.work import WorkTools
from poise.modules.foundation.errors import PoiseError
from poise.modules.work.domain import parse_request
from runtime_services.test_draft_continuation import draft, saved_rows
from runtime_services.test_stage_progression import advance, bootstrap, configure_progression


def release(tools, task_id, version, request_id):
    return tools.invoke(request("release", {
        "task_id": task_id,
        "request_id": request_id,
        "expected_version": version,
        "reason": "The next participant continues in the existing working area.",
    }))


@pytest.mark.parametrize("keep_unused_tripwire", [False, True])
def test_next_actor_advances_in_existing_worktree_without_material_transport(project, keep_unused_tripwire):
    configure_progression(project)
    if keep_unused_tripwire:
        project["cfg"]["runtime_services"]["transfer"] = {"future_option": True}
    configure(project)
    assert "handoff" not in project["cfg"]["runtime_services"]
    assert ("transfer" in project["cfg"]["runtime_services"]) is keep_unused_tripwire
    executor = WorkTools(WorkPoise(project["config_path"], "executor"))
    first = bootstrap(executor, project["task"])
    add_test(first["worktree"])
    assert verify(executor, result(first))["status"] == "verified"
    implementation = advance(executor)
    assert implementation["stage"] == "implementation"
    root = Path(implementation["worktree"])
    (root / "src/double.py").write_text("def double(n):\n    return n * 2\n")
    note = root / "src/participant.note"
    note.write_bytes(b"continue this exact verified working material\n")
    assert verify(executor, result(implementation))["status"] == "verified"
    advance(executor)
    original = executor.runtime.current_task()
    head = git(root, "rev-parse", "HEAD")
    status = git(root, "status", "--porcelain=v1")

    released = release(executor, "T1", original["version"], "release-for-inspection")

    assert released["status"] == "ownership_released"
    assert executor.runtime.current_task() is None
    assert executor.runtime.ownership.snapshot("executor").worktree_task_id is None
    reviewer = WorkTools(WorkPoise(project["config_path"], "reviewer"))
    acquired = bootstrap(reviewer, {"id": "T1"})
    assert acquired["worktree"] == str(root)
    assert git(root, "rev-parse", "HEAD") == head
    assert git(root, "status", "--porcelain=v1") == status
    assert note.read_bytes() == b"continue this exact verified working material\n"

    progressed = advance(reviewer)

    assert progressed["status"] == "progression_target_reached", "ORDINARY_RELEASE_MUST_ALLOW_ROLE_CONTINUATION"
    assert progressed["stage"] == "code_review"
    assert reviewer.runtime.current_task()["claimed_by"] == "reviewer"
    assert git(root, "rev-parse", "HEAD") == head
    assert note.read_bytes() == b"continue this exact verified working material\n"
    assert advance(reviewer)["status"] == "progression_target_reached"


@pytest.mark.parametrize("kind", ["fresh", "selected", "restarted"])
def test_newborn_participant_release_keeps_draft_and_existing_work_in_place(project, kind):
    owner, tools = draft(project, kind)
    before = deepcopy(owner.current_task())
    task_id = before["id"]
    immutable = saved_rows(owner, task_id)
    if kind == "restarted":
        root = Path(before["worktree"])
        head = git(root, "rev-parse", "HEAD")
        status = git(root, "status", "--porcelain=v1")

    packet = request("release", {
        "task_id": task_id,
        "request_id": "release-unfinished-draft",
        "expected_version": before["_version"],
        "reason": "Continue this draft in its existing working area.",
    })
    released = tools.invoke(packet)

    assert released["status"] == "ownership_released"
    assert owner.current_task() is None
    after = owner.task_queries.record(task_id)
    assert after["status"] == "newborn"
    assert after["draft"] == before["draft"]
    assert after["worktree"] == before["worktree"]
    assert after["history"][:-1] == before["history"]
    assert after["history"][-1]["event"] == "ownership_released"
    assert saved_rows(owner, task_id) == immutable
    receiver = WorkTools(WorkPoise(project["config_path"], "receiver"))
    acquired = bootstrap(receiver, {"id": task_id})
    assert acquired["status"] == "newborn"
    assert acquired["draft"] == before["draft"]
    assert acquired["worktree"] == before["worktree"]
    assert tools.invoke(packet)["replayed"] is True
    assert receiver.runtime.current_task()["claimed_by"] == "receiver"
    if kind == "restarted":
        assert git(root, "rev-parse", "HEAD") == head
        assert git(root, "status", "--porcelain=v1") == status
        assert (root / "untracked-note.txt").read_bytes() == b"WIP to preserve\n"


def test_cancelled_untyped_draft_removes_working_material_without_another_agreement(project):
    owner, tools = draft(project, "fresh")
    root = owner.state / owner.paths["standalone_tasks"] / "DRAFT"
    root.mkdir(parents=True, exist_ok=True)
    (root / "discarded-plan.txt").write_bytes(b"internal working material\n")
    unrelated = root.parent / "FOREIGN" / "keep.txt"
    unrelated.parent.mkdir(parents=True, exist_ok=True)
    unrelated.write_bytes(b"unrelated working material\n")

    cancelled = tools.invoke(request("cancel", {"reason": "Discard the unfinished draft."}))

    assert cancelled["status"] == "cancelled"
    assert not root.exists(), "CANCELLED_TASK_ROOT_RETAINED"
    assert unrelated.read_bytes() == b"unrelated working material\n"
    assert tools.runtime.task_queries.record("DRAFT")["status"] == "cancelled"


@pytest.mark.parametrize("operation", ["handoff", "transfer"])
def test_retired_material_transport_is_outside_the_supported_work_api(project, operation):
    with pytest.raises(PoiseError, match="^Unknown work operation$"):
        parse_request(request(operation, {}), project["cfg"]["batch"])
