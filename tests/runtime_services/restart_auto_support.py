"""Test-owned accepted history, commits and effect observations for public replay."""

from copy import deepcopy
from pathlib import Path
import json
import subprocess

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
        "branch": git(root, "symbolic-ref", "--short", "HEAD"),
        "effects": case["log"].read_text().splitlines(),
        "owned": owned_rows(case),
    }


def capture_checkout(root, commit):
    """Capture fixture-owned Git objects before the operation under test."""
    entries = subprocess.check_output([
        "git", "-C", str(root), "ls-tree", "-rz", "--full-tree", commit,
    ]).split(b"\0")
    index = []
    files = {}
    for entry in filter(None, entries):
        metadata, path = entry.split(b"\t", 1)
        mode, kind, oid = metadata.split()
        assert kind == b"blob"
        index.append(mode + b" " + oid + b" 0\t" + path)
        files[path.decode()] = subprocess.check_output([
            "git", "-C", str(root), "cat-file", "blob", oid.decode(),
        ])
    return {"tree": git(root, "rev-parse", f"{commit}^{{tree}}"),
            "index": sorted(index), "files": files, "untracked": b""}


def assert_checkout(case, commit):
    expected = case["checkouts"][commit]
    root = case["root"]
    assert git(root, "rev-parse", "HEAD") == commit
    index = subprocess.check_output([
        "git", "-C", str(root), "ls-files", "--stage", "-z",
    ])
    assert sorted(filter(None, index.split(b"\0"))) == expected["index"]
    assert git(root, "diff-index", "--cached", "--name-only", expected["tree"]) == ""
    assert {path: (root / path).read_bytes() for path in expected["files"]} == expected["files"]
    assert subprocess.check_output([
        "git", "-C", str(root), "ls-files", "--others", "--exclude-standard", "-z",
    ]) == expected["untracked"]
    assert git(root, "diff", "--name-only") == ""


def owned_rows(case):
    """Read persisted effects in this disposable test DB, never live state."""
    columns = {
        "task_events": "seq,task_id,version,at,data",
        "submissions": "seq,task_id,stage,iteration,digest,data",
        "task_results": "task_id,submission_id,data",
        "evidence": "id,task_id,stage,iteration,data",
        "journal": "seq,at,session_id,task_id,event,data",
    }
    with case["client"].runtime.store.transaction() as database:
        return {
            table: [tuple(row) for row in database.execute(
                f"SELECT {fields} FROM {table} WHERE task_id=? ORDER BY rowid", ("T1",),
            )]
            for table, fields in columns.items()
        }


def replay_effects(case, baseline):
    rows = owned_rows(case)
    events = rows["task_events"][len(baseline["task_events"]):]
    return {
        "launches": case["log"].read_text().splitlines(),
        "receipts": rows["evidence"][len(baseline["evidence"]):],
        "assessments": [row for row in events if json.loads(row[-1])["event"] == "evidence_assessed"],
        "transitions": [row for row in events if json.loads(row[-1])["event"] == "stage_progressed"],
    }


def assert_effect_counts(effects, expected):
    assert tuple(len(effects[key]) for key in (
        "launches", "receipts", "assessments", "transitions",
    )) == expected
    for key in ("receipts", "assessments", "transitions"):
        assert len({row[0] for row in effects[key]}) == len(effects[key])


def assert_projection(case, response, *, mode="target", target="code_review",
                      stage="code_review", reason="target_reached", count=3,
                      subject=None, noop=False, task_stage=None):
    replay = response["replay"]
    assert set(replay) == {
        "mode", "target_stage", "recovery_ref", "start_commit", "subject_commit",
        "stopped_at", "reason", "passed",
    }
    expected = {
        "mode": mode, "target_stage": target,
        "start_commit": case["saved"], "subject_commit": subject,
        "stopped_at": stage, "reason": reason,
        "passed": case["visits"][:count],
    }
    assert {key: replay[key] for key in expected} == expected
    for visit in replay["passed"]:
        assert set(visit) == {"stage", "visit_id", "commit"}
        assert type(visit["visit_id"]) is int
    record = case["client"].runtime.task_queries.record("T1")
    assert record["process"]["stages"][record["stage_index"]]["id"] == (task_stage or stage)
    assert record["id"] == "T1"
    assert record["worktree"] == str(case["root"])
    assert git(case["root"], "symbolic-ref", "--short", "HEAD") == case["branch"]
    checkout = subject or (case["visits"][count - 1]["commit"] if count else case["saved"])
    assert_checkout(case, checkout)
    if noop:
        assert replay["recovery_ref"] is None
    else:
        assert_recovery(case, response)


def accepted_visit(client, root, stage):
    with client.runtime.store.transaction() as database:
        row = database.execute(
            "SELECT r.submission_id,s.stage,s.iteration,r.data FROM task_results r "
            "JOIN submissions s ON s.task_id=r.task_id AND s.seq=r.submission_id "
            "WHERE r.task_id=? AND r.submission_id=(SELECT current_submission_id FROM tasks WHERE id=?)",
            ("T1", "T1"),
        ).fetchone()
    report = json.loads(row[3])
    assert row[1] == stage
    assert report["stage"] == stage
    assert report["iteration"] == row[2]
    assert report["status"] == "verified"
    assert report["commit"] == git(root, "rev-parse", "HEAD")
    event = client.runtime.task_queries.record("T1")["history"][-1]
    assert event["event"] == "verified"
    assert (event["stage"], event["iteration"], event["submission"]) == (stage, row[2], row[0])
    return {"stage": stage, "visit_id": row[0], "commit": report["commit"]}


def assert_recovery(case, response):
    proof = response["replay"]
    assert proof["start_commit"] == case["saved"]
    assert isinstance(proof["recovery_ref"], str)
    assert git(case["root"], "rev-parse", proof["recovery_ref"]) == case["saved"]
    recovery_file = case.get("recovery_file", "src/saved.txt")
    expected_bytes = case.get("recovery_bytes", "saved work")
    assert git(case["root"], "show", f"{proof['recovery_ref']}:{recovery_file}") == expected_bytes


def prepared(project, *, file_proof=None, proof_name="accepted.bin", checks=True,
             alternating_roles=False, revise=False, finish=False, publish=False,
             rework=False, route_revision=False, sprint=False):
    configure(project)
    project["cfg"]["automatic_checks"] = []
    write_json(project["config_path"], project["cfg"])
    # Make each accepted commit distinct without altering historical test semantics.
    project["process"]["stages"][1].update(read_only=False, allowed_paths=["tests/**"])
    project["task"]["stage_contracts"][1]["allowed_paths"] = ["tests/**"]
    if rework:
        project["process"]["stages"][-1]["rework_targets"].append("tests")
    if publish:
        terminal = deepcopy(project["process"]["stages"][-1])
        terminal.update(id="publish", handler="publish", transitions={"complete": None},
                        rework_targets=[])
        project["process"]["stages"][-1].update(
            handler="inspect", role="reviewer",
            transitions={"clear": "publish", "changes_requested": "implementation"},
            rework_targets=["implementation"],
        )
        project["process"]["stages"].append(terminal)
        project["task"]["stage_contracts"].append({
            "stage_id": "publish", "allowed_paths": [],
            "entry_requirements": [], "exit_requirements": [],
        })
        project["task"]["decomposition"]["phases"].append({
            "stage": "publish", "skills": ["task-domain"], "areas": [],
        })
        project["task"]["evidence_plan"]["publish"] = {
            "subject_methods": {}, "arguments": [], "review_arguments": [],
        }
    write_json(project["root"] / "config/processes/development.json", project["process"])
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
        stage["id"]: ["GREEN"] if checks and stage["id"] != "publish" else []
        for stage in project["process"]["stages"]
    }
    if route_revision:
        project["task"]["planning"] = {
            "schema": "task-planning-1", "template": None,
            "restart_revision_policy": {"reviewer": [], "user": ["process"]},
        }
    client = WorkTools(WorkPoise(project["config_path"], "replay-owner"))
    if sprint:
        from sprints.helpers import draft, policy, publish as publish_sprint
        project["cfg"]["sprint"] = policy()
        write_json(project["config_path"], project["cfg"])
        client = WorkTools(WorkPoise(project["config_path"], "replay-owner"))
        member = deepcopy(project["task"])
        member["sprint_id"] = "S"
        successor = deepcopy(member)
        successor["id"] = "T2"
        successor["goal"] = "Проверить результат предыдущей задачи спринта."
        bind_task_requirements(successor, project["requirements_registry"])
        planned = draft(client, [member, successor], [
            {"predecessor": "T1", "successor": "T2", "kind": "completion"},
        ])
        assert planned["errors"] == []
        published = publish_sprint(client, planned["revision"])
        assert published["eligible"] == ["T1"]
        context = bootstrap(client, {"id": "T1"})
    else:
        context = bootstrap(client, deepcopy(project["task"]))
    root = Path(context["worktree"])
    (root / "tests").mkdir()
    subject_test = Path(__file__).parent / "fixtures" / "restart_auto_subject_test.py"
    (root / "tests" / "test_double.py").write_bytes(subject_test.read_bytes())
    proof = None
    if file_proof is not None:
        proof = Path(context["task_root"]) / "artifacts" / proof_name
        proof.parent.mkdir(parents=True, exist_ok=True)
        proof.write_bytes(file_proof)
    commits = []
    visits = []
    for index, stage in enumerate(("tests", "test_review", "implementation")):
        assert context["stage"] == stage
        if stage == "implementation":
            (root / "src" / "double.py").write_text(
                "def double(n):\n    return n * 2\n"
            )
        if stage == "test_review":
            (root / "tests" / "review.txt").write_text("accepted independent stage bytes\n")
        value = result(context)
        if index == 0 and proof is not None:
            value["artifact_paths"] = [str(proof)]
        delivered = verify(client, value)
        assert delivered["status"] == "verified", delivered
        commits.append(delivered["commit"])
        visits.append(accepted_visit(client, root, stage))
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
    if rework:
        delivered = verify(client, result(context))
        assert delivered["status"] == "verified", delivered
        context = client.invoke(request("bootstrap", {
            "task": None, "decision": "rework", "feedback": "Repeat accepted visit.",
            "rework_stage": "tests",
        }))
        for index, stage in enumerate(("tests", "test_review", "implementation")):
            if stage == "tests":
                (root / "tests" / "second.txt").write_text("second accepted visit\n")
            delivered = verify(client, result(context))
            assert delivered["status"] == "verified", delivered
            commits[index] = delivered["commit"]
            visits[index] = accepted_visit(client, root, stage)
            context = client.invoke(request("advance", {
                "request_id": f"reworked-{index}", "task_id": "T1",
                "target_stage": ("test_review", "implementation", "code_review")[index],
            }))
    if finish or publish:
        final_result = result(context)
        if publish:
            final_result["stage_work"]["coverage"] = "Independent historical implementation review."
        delivered = verify(client, final_result)
        assert delivered["status"] == "verified", delivered
        commits.append(delivered["commit"])
        visits.append(accepted_visit(client, root, "code_review"))
    (root / "src" / "saved.txt").write_text("saved work\n")
    git(root, "add", "src/saved.txt")
    git(root, "commit", "-m", "Preserve task work before restart")
    saved = git(root, "rev-parse", "HEAD")
    checkouts = {commit: capture_checkout(root, commit) for commit in {*commits, saved}}
    current = client.runtime.task_queries.record("T1")
    with client.runtime.store.transaction() as database:
        historical_results = [tuple(row) for row in database.execute(
            "SELECT submission_id,data FROM task_results WHERE task_id=? ORDER BY submission_id",
            ("T1",),
        )]
    sprint_before_restart = None
    if sprint:
        with client.runtime.store.transaction() as database:
            sprint_before_restart = {
                "members": [tuple(row) for row in database.execute(
                    "SELECT task_id,sprint_id FROM sprint_members ORDER BY task_id")],
                "dependencies": [tuple(row) for row in database.execute(
                    "SELECT sprint_id,predecessor,successor,kind FROM sprint_dependencies")],
            }
        sprint_before_restart["peer"] = deepcopy(client.runtime.task_queries.record("T2"))
    authorization = ({"role": "user", "decision": "Revise the fixture route."}
                     if route_revision else "User authorized recovery of this unfinished Task.")
    born = restart(client, "T1", current["version"], authorization=authorization)
    if route_revision:
        revised_process = deepcopy(project["process"])
        revised_process["stages"][0]["transitions"] = {"complete": "implementation"}
        revised_process["stages"][1]["transitions"] = {"complete": "code_review"}
        revised_process["stages"][2]["transitions"] = {"complete": "test_review"}
        born = client.invoke(request("task", {
            "action": "edit", "task_id": "T1", "request_id": "reorder-route",
            "expected_revision": born["revision"], "patch": {"process": revised_process},
            "remove": [],
        }))
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
        "visits": visits, "branch": git(root, "symbolic-ref", "--short", "HEAD"),
        "checkouts": checkouts, "sprint_before_restart": sprint_before_restart,
    }
