"""Test-owned accepted history, commits and effect observations for public replay."""

from copy import deepcopy
from pathlib import Path

import pytest

from batch.helpers import configure, request, result, verify
from conftest import WorkPoise, bind_task_requirements, git, write_json
from poise.application.work import WorkTools
from poise.common import PoiseError
from runtime_services.test_task_restart import restart


def bootstrap(client, task):
    return client.invoke(request("bootstrap", {
        "task": task, "decision": None, "feedback": None, "rework_stage": None,
    }))


def launch(case, *, target=None, request_id="replay", maximum=False):
    arguments = {"request_id": request_id, "task_id": "T1"}
    if not maximum:
        arguments["target_stage"] = target
    try:
        response = case["client"].invoke(request("advance", arguments))
    except PoiseError as exc:
        pytest.fail(f"Valid replay request was rejected: {exc}")
    assert "replay" in response, "Public replay assessment is missing"
    return response


def snapshot(case):
    root = case["root"]
    return {
        "task": deepcopy(case["client"].runtime.task_queries.record("T1")),
        "head": git(root, "rev-parse", "HEAD"),
        "index": Path(git(root, "rev-parse", "--git-path", "index")).read_bytes(),
        "status": git(root, "status", "--porcelain=v1", "--untracked-files=all"),
        "files": {
            str(path.relative_to(root)): path.read_bytes()
            for path in root.rglob("*") if path.is_file() and path.name != ".git"
        },
        "refs": git(root, "show-ref"),
        "effects": case["log"].read_text().splitlines(),
    }


def assert_recovery(case, response):
    proof = response["replay"]
    assert proof["start_commit"] == case["saved"]
    assert isinstance(proof["recovery_ref"], str)
    assert git(case["root"], "rev-parse", proof["recovery_ref"]) == case["saved"]
    assert git(case["root"], "show", f"{proof['recovery_ref']}:src/saved.txt") == "saved work"


def prepared(project, *, file_proof=None, checks=True, alternating_roles=False, revise=False):
    configure(project)
    project["cfg"]["automatic_checks"] = []
    write_json(project["config_path"], project["cfg"])
    if alternating_roles:
        project["process"]["stages"][1]["role"] = "reviewer"
        write_json(project["root"] / "config/processes/development.json", project["process"])
    method = deepcopy(project["task"]["methods"][1])
    log = project["root"] / "historical-checks.txt"
    log.write_text("")
    fail_file = project["root"] / "fail-historical-check"
    method["environment"] = {
        "REPLAY_LOG": str(log), "REPLAY_FAIL_FILE": str(fail_file),
    }
    method["verification_plan"]["change_surface"] = ["tests/**"]
    method["verification_plan"]["green_stages"] = [
        "tests", "test_review", "implementation", "code_review",
    ]
    project["task"]["methods"] = [method] if checks else []
    project["task"]["method_inputs"] = [
        deepcopy(project["task"]["method_inputs"][1])
    ] if checks else []
    project["task"]["checks"] = {
        stage["id"]: ["GREEN"] if checks else []
        for stage in project["process"]["stages"]
    }
    client = WorkTools(WorkPoise(project["config_path"], "replay-owner"))
    context = bootstrap(client, deepcopy(project["task"]))
    root = Path(context["worktree"])
    (root / "tests").mkdir()
    subject_test = Path(__file__).parent / "fixtures" / "restart_auto_subject_test.py"
    (root / "tests" / "test_double.py").write_bytes(subject_test.read_bytes())
    proof = None
    if file_proof is not None:
        proof = Path(context["task_root"]) / "artifacts" / "accepted.bin"
        proof.parent.mkdir(parents=True, exist_ok=True)
        proof.write_bytes(file_proof)
    commits = []
    for index, stage in enumerate(("tests", "test_review", "implementation")):
        assert context["stage"] == stage
        if stage == "implementation":
            (root / "src" / "double.py").write_text(
                "def double(n):\n    return n * 2\n"
            )
        value = result(context)
        if index == 0 and proof is not None:
            value["artifact_paths"] = [str(proof)]
        delivered = verify(client, value)
        assert delivered["status"] == "verified", delivered
        commits.append(delivered["commit"])
        following = ("test_review", "implementation", "code_review")[index]
        context = client.invoke(request("advance", {
            "request_id": f"historical-{index}", "task_id": "T1",
            "target_stage": following,
        }))
        if context["status"] == "role_handoff_required":
            release = client.invoke(request("handoff", {
                "request_id": f"historical-release-{index}",
                "reason": "Historical participants transfer their own stage result.",
                "result": None, "commit_message": None, "artifact_paths": [],
            }))
            assert release["status"] == "handed_off"
            client = WorkTools(WorkPoise(project["config_path"], f"historical-owner-{index}"))
            bootstrap(client, {"id": "T1"})
            context = client.invoke(request("advance", {
                "request_id": f"historical-{index}", "task_id": "T1",
                "target_stage": following,
            }))
        assert context["stage"] == following, context
    (root / "src" / "saved.txt").write_text("saved work\n")
    git(root, "add", "src/saved.txt")
    git(root, "commit", "-m", "Preserve task work before restart")
    saved = git(root, "rev-parse", "HEAD")
    current = client.runtime.task_queries.record("T1")
    with client.runtime.store.transaction() as database:
        historical_results = [tuple(row) for row in database.execute(
            "SELECT submission_id,data FROM task_results WHERE task_id=? ORDER BY submission_id",
            ("T1",),
        )]
    born = restart(client, "T1", current["version"])
    if revise:
        revised = deepcopy(project["task"])
        revised["requirements"] = ["Accepted proof remains valid after Task revision."]
        bind_task_requirements(revised, project["requirements_registry"])
        born = client.invoke(request("task", {
            "action": "edit", "task_id": "T1", "request_id": "revise-requirements",
            "expected_revision": born["revision"],
            "patch": {key: revised[key] for key in (
                "requirements", "requirements_snapshot", "requirements_agreement",
            )}, "remove": [],
        }))
    ready = client.invoke(request("task", {
        "action": "ready", "task_id": "T1", "request_id": "restart-ready",
        "expected_revision": born["revision"],
    }))
    assert ready["ready"] is True
    restarted = bootstrap(client, {"id": "T1"})
    assert restarted["stage"] == "tests"
    if revise:
        assert restarted["requirements"] == ["Accepted proof remains valid after Task revision."]
    log.write_text("")
    return {
        "client": client, "root": root, "log": log, "commits": commits,
        "saved": saved, "proof": proof, "project": project, "fail_file": fail_file,
        "historical_results": historical_results,
    }
