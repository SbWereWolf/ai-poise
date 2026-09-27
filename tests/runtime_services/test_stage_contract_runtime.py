from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess

import pytest

from conftest import WorkPoise as Poise, write_json
from poise.application.work import WorkTools
from poise.common import PoiseError
from batch.helpers import request


def _feature():
    required = (
        "initialize_stage_contracts",
        "revise_stage_contract",
        "stage_contract_context",
    )
    missing = [name for name in required if not hasattr(Poise, name)]
    assert not missing, f"runtime StageContract API is missing: {missing}"


def _contracts(process, *, first_entry=(), first_scope=None):
    values = []
    for index, stage in enumerate(process["stages"]):
        values.append({
            "stage_id": stage["id"],
            "allowed_paths": list(stage["allowed_paths"] if index or first_scope is None else first_scope),
            "entry_requirements": list(first_entry if index == 0 else ()),
            "exit_requirements": [],
        })
    return values


def _configure(project, *, contracts=True, entry_required=False, exit_required=False,
               first_scope=None, inspection=False, review_gate=False):
    process = deepcopy(project["process"])
    task = deepcopy(project["task"])
    if inspection:
        inspect = deepcopy(process["stages"][1])
        revise = deepcopy(process["stages"][2])
        inspect["handler"] = "inspect"
        inspect["transitions"] = {"clear": None, "changes_requested": revise["id"]}
        inspect["rework_targets"] = [revise["id"]]
        revise["transitions"] = {"complete": inspect["id"]}
        revise["rework_targets"] = [revise["id"]]
        process["stages"] = [inspect, revise]
        process["route"]["entry"] = inspect["id"]
        selected_stages = {stage["id"] for stage in process["stages"]}
        task["decomposition"]["phases"] = [
            phase for phase in task["decomposition"]["phases"]
            if phase["stage"] in selected_stages
        ]
        task["methods"] = []
        task["method_inputs"] = []
        task["checks"] = {stage["id"]: [] for stage in process["stages"]}
        task["evidence_plan"] = {
            stage["id"]: {"subject_methods": {}, "arguments": [], "review_arguments": []}
            for stage in process["stages"]
        }
        project["cfg"]["automatic_checks"] = []
        write_json(project["config_path"], project["cfg"])
    if exit_required:
        task["methods"] = []
        task["method_inputs"] = []
        task["checks"] = {stage["id"]: [] for stage in process["stages"]}
        task["evidence_plan"] = {
            stage["id"]: {"subject_methods": {}, "arguments": [], "review_arguments": []}
            for stage in process["stages"]
        }
        project["cfg"]["automatic_checks"] = []
        write_json(project["config_path"], project["cfg"])
    requirements = []
    if entry_required:
        requirements.append({
                "id": "entry-input",
                "kind": "artifact",
                "stages": [process["stages"][0]["id"]],
                "phase": "pre",
                "scope": "task",
                "pattern": "inputs/ready.txt",
                "minimum": 1,
                "maximum": 1,
                "source": {"kind": "preexisting"},
            })
    if exit_required:
        requirements.append({
            "id": "stage-output",
            "kind": "artifact",
            "stages": [process["stages"][0]["id"]],
            "phase": "post",
            "scope": "task",
            "pattern": "outputs/result.txt",
            "minimum": 1,
            "maximum": 1,
            "source": {"kind": "stage_output", "producer_stage": process["stages"][0]["id"]},
        })
    if review_gate:
        revision_stage = process["stages"][1]["id"]
        requirements.extend([
            {
                "id": requirement_id,
                "kind": "section",
                "stages": [revision_stage],
                "phase": "pre",
                "section": "report",
                "states": [state],
            }
            for requirement_id, state in (
                ("review-gate-a", "template"),
                ("review-gate-b", "populated"),
            )
        ])
    task["content_contract"] = {"sections": [], "routes": [], "requirements": requirements}
    if contracts:
        task["stage_contracts"] = _contracts(
            process,
            first_entry=("entry-input",) if entry_required else (),
            first_scope=first_scope,
        )
        if exit_required:
            task["stage_contracts"][0]["exit_requirements"] = ["stage-output"]
        if review_gate:
            task["stage_contracts"][1]["entry_requirements"] = ["review-gate-a"]
    write_json(project["root"] / "config/processes/development.json", process)
    return process, task


def _bootstrap(project, session="executor", **options):
    _feature()
    _, task = _configure(project, **options)
    tools = WorkTools(Poise(project["config_path"], session))
    result = tools.invoke(request("bootstrap", {
        "task": task,
        "decision": None,
        "feedback": None,
        "rework_stage": None,
    }))
    return tools, result, task


def _counts(runtime, task_id):
    tables = ("submissions", "journal", "artifacts", "action_runs", "task_results")
    with runtime.store.transaction() as db:
        counts = {}
        for table in tables:
            columns = {row[1] for row in db.execute(f"PRAGMA table_info({table})")}
            where = " WHERE task_id=?" if "task_id" in columns else ""
            args = (task_id,) if where else ()
            counts[table] = db.execute(f"SELECT COUNT(*) FROM {table}{where}", args).fetchone()[0]
        row = db.execute(
            "SELECT status,stage_index,iteration,claimed_by,version,current_submission_id "
            "FROM tasks WHERE id=?",
            (task_id,),
        ).fetchone()
    return counts, tuple(row) if row is not None else None


def _raw_snapshot(runtime, task_id="T1"):
    with runtime.store.transaction() as db:
        task = tuple(db.execute(
            "SELECT status,stage_index,iteration,claimed_by,version,current_submission_id,metadata "
            "FROM tasks WHERE id=?",
            (task_id,),
        ).fetchone())
        events = tuple(tuple(row) for row in db.execute(
            "SELECT version,data FROM task_events WHERE task_id=? ORDER BY seq", (task_id,)
        ))
        journal = tuple(tuple(row) for row in db.execute(
            "SELECT session_id,event,data FROM journal WHERE task_id=? ORDER BY seq", (task_id,)
        ))
    return {"task": task, "events": events, "journal": journal}


def _full_contract_snapshot(runtime, task_id="T1"):
    raw = _raw_snapshot(runtime, task_id)
    metadata = json.loads(raw["task"][-1])
    return {
        **raw,
        "metadata": metadata,
        "contracts": deepcopy(metadata["contract"].get("stage_contracts")),
        "history": deepcopy(metadata.get("stage_contract_history", [])),
    }


def _transition_effects(runtime, task_id="T1"):
    with runtime.store.transaction() as db:
        tables = [row[0] for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )]
        rows = {
            table: tuple(tuple(row) for row in db.execute(f'SELECT * FROM "{table}" ORDER BY rowid'))
            for table in tables
        }
    return rows, runtime.ownership.snapshot("executor")


def _legacy(runtime, task_id="T1"):
    with runtime.store.transaction() as db:
        row = db.execute("SELECT metadata FROM tasks WHERE id=?", (task_id,)).fetchone()
        metadata = json.loads(row[0])
        metadata["contract"].pop("stage_contracts")
        db.execute(
            "UPDATE tasks SET metadata=? WHERE id=?",
            (json.dumps(metadata, ensure_ascii=False, sort_keys=True, separators=(",", ":")), task_id),
        )


def _seed_artifact(runtime, task_id="T1"):
    root = Path(runtime.state) / runtime.paths["standalone_tasks"] / task_id
    path = root / "artifacts" / "inputs" / "ready.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("ready\n", encoding="utf-8")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    artifact_id = "fixture-entry-input"
    with runtime.store.transaction() as db:
        db.execute(
            "INSERT INTO artifacts(id,owner,scope,path,digest) VALUES(?,?,?,?,?)",
            (artifact_id, task_id, "task", str(path), digest),
        )
        db.execute("INSERT INTO task_artifacts(task_id,artifact_id) VALUES(?,?)", (task_id, artifact_id))
    return path


def _transition(runtime, action, *, request_id, task_id="T1", expected_version,
                contracts=None, stage_id=None, contract=None, role="creator"):
    if action == "initialize":
        return runtime.initialize_stage_contracts(
            task_id=task_id,
            expected_version=expected_version,
            request_id=request_id,
            contracts=contracts,
            reason="Explicit stage contract transition",
            authorization={"role": role},
        )
    return runtime.revise_stage_contract(
        task_id=task_id,
        expected_version=expected_version,
        request_id=request_id,
        stage_id=stage_id,
        contract=contract,
        reason="Reviewer corrected the Task-specific gate",
        authorization={"role": role},
    )


def test_entry_failure_precedes_claim_and_context(project):
    tools, result, _ = _bootstrap(project, entry_required=True)
    assert result["status"] == "broken"
    assert result["failure"]["kind"] == "content_requirements_failed"
    assert result["failure"]["content_requirements"]["phase"] == "pre"
    assert tools.runtime.current_task() is None
    with tools.runtime.store.transaction() as db:
        row = db.execute("SELECT status,claimed_by FROM tasks WHERE id='T1'").fetchone()
    assert tuple(row) == ("available", None)


def test_entry_recheck_precedes_submission_and_artifact_effects(project):
    first, rejected, task = _bootstrap(project, entry_required=True)
    assert rejected["status"] == "broken"
    runtime = first.runtime
    path = _seed_artifact(runtime)
    tools = WorkTools(Poise(project["config_path"], "executor"))
    context = tools.invoke(request("bootstrap", {
        "task": {"id": task["id"]}, "decision": None, "feedback": None, "rework_stage": None,
    }))
    before = _counts(runtime, "T1")
    worktree = Path(context["worktree"])
    git_before = (
        subprocess.check_output(["git", "-C", str(worktree), "rev-parse", "HEAD"], text=True),
        subprocess.check_output(["git", "-C", str(worktree), "status", "--porcelain=v1"], text=True),
    )
    forbidden = Path(runtime.state) / runtime.paths["standalone_tasks"] / "T1" / "artifacts" / "should-not-exist.txt"
    path.unlink()
    with pytest.raises(PoiseError, match="entry"):
        tools.invoke(request("verify", {
            "result": context["result_template"],
            "artifacts": [{
                "scope": "task",
                "path": "should-not-exist.txt",
                "source": {"kind": "text", "text": "no side effect"},
            }],
        }))
    assert _counts(runtime, "T1") == before
    assert not forbidden.exists()
    assert (
        subprocess.check_output(["git", "-C", str(worktree), "rev-parse", "HEAD"], text=True),
        subprocess.check_output(["git", "-C", str(worktree), "status", "--porcelain=v1"], text=True),
    ) == git_before


def test_entry_rejection_preserves_all_pre_effect_counters(project):
    tools, result, _ = _bootstrap(project, entry_required=True)
    counts, state = _counts(tools.runtime, "T1")
    assert result["status"] == "broken"
    assert state[0:4] == ("available", 0, 1, None)
    assert counts["submissions"] == counts["artifacts"] == counts["action_runs"] == counts["task_results"] == 0


def test_exit_gate_sees_stage_outputs_before_verification(project):
    tools, context, _ = _bootstrap(project, exit_required=True)
    draft = deepcopy(context["result_template"])
    draft["sections"]["report"] = "Produced the declared output."
    draft["commit_message"] = "test: stage output"
    result = tools.invoke(request("verify", {
        "result": draft,
        "artifacts": [{
            "scope": "task",
            "path": "outputs/result.txt",
            "source": {"kind": "text", "text": "ready"},
        }],
    }))
    assert result["status"] == "verified"


def test_legacy_task_requires_explicit_stage_contract_initialization(project):
    tools, _, task = _bootstrap(project)
    runtime = tools.runtime
    _legacy(runtime)
    runtime.handoff({
        "request_id": "legacy-release",
        "reason": "exercise legacy reload",
        "result": None,
        "commit_message": "WIP: legacy fixture",
        "artifact_paths": [],
    })
    resumed = WorkTools(Poise(project["config_path"], "next"))
    with pytest.raises(PoiseError, match="stage_contract_transition_required"):
        resumed.invoke(request("bootstrap", {
            "task": {"id": "T1"}, "decision": None, "feedback": None, "rework_stage": None,
        }))
    assert task["stage_contracts"]


def test_stage_contract_initialization_replays_and_rejects_conflict(project):
    tools, context, task = _bootstrap(project)
    runtime = tools.runtime
    _legacy(runtime)
    version = runtime.task_queries.record("T1")["version"]
    before = _raw_snapshot(runtime)
    first = _transition(runtime, "initialize", request_id="init-1", expected_version=version,
                        contracts=task["stage_contracts"])
    after = _raw_snapshot(runtime)
    replay = _transition(runtime, "initialize", request_id="init-1", expected_version=version,
                         contracts=task["stage_contracts"])
    assert replay == {**first, "replayed": True}
    assert replay["replayed"] is True
    assert _raw_snapshot(runtime) == after
    assert after["task"][4] == before["task"][4] + 1
    assert len(after["events"]) == len(before["events"]) + 1
    assert len(after["journal"]) == len(before["journal"]) + 1
    entry = json.loads(after["journal"][-1][2])
    assert entry == {
        "action": "initialize_stage_contracts",
        "actor": "executor",
        "authorization": {"role": "creator"},
        "expected_version": version,
        "new": task["stage_contracts"],
        "old": None,
        "reason": "Explicit stage contract transition",
        "request_id": "init-1",
    }
    changed = deepcopy(task["stage_contracts"])
    changed[0]["allowed_paths"] = ["different/**"]
    with pytest.raises(PoiseError, match="request.*conflict"):
        _transition(runtime, "initialize", request_id="init-1", expected_version=version,
                    contracts=changed)
    assert _raw_snapshot(runtime) == after


def test_reviewer_revision_changes_gate_refs_atomically(project):
    tools, _, task = _bootstrap(project, inspection=True, review_gate=True)
    runtime = tools.runtime
    contract = deepcopy(task["stage_contracts"][1])
    contract["entry_requirements"] = ["review-gate-b"]
    before = _full_contract_snapshot(runtime)
    result = _transition(
        runtime,
        "revise",
        request_id="revise-gate",
        expected_version=before["task"][4],
        stage_id=contract["stage_id"],
        contract=contract,
        role="reviewer",
    )
    after = _full_contract_snapshot(runtime)
    assert result["new"]["entry_requirements"] == ["review-gate-b"]
    assert after["contracts"][1]["entry_requirements"] == ["review-gate-b"]
    assert after["task"][4] == before["task"][4] + 1
    assert len(after["events"]) == len(before["events"]) + 1
    assert len(after["journal"]) == len(before["journal"]) + 1


def test_content_addition_does_not_mutate_task_gate_contract_or_poison_reload(project):
    tools, context, task = _bootstrap(project)
    runtime = tools.runtime
    payload = deepcopy(context["result_template"])
    payload["sections"]["report"] = "Add a predicate for later reviewer selection."
    payload["commit_message"] = "test: preserve task gates"
    payload["content_additions"]["requirements"] = [{
        "id": "later-review-gate",
        "kind": "section",
        "stages": [context["stage"]],
        "phase": "pre",
        "section": "report",
        "states": ["populated"],
    }]
    runtime.task_commands.submit("T1", "executor", payload)
    restarted = Poise(project["config_path"], "executor")
    loaded = restarted.task_commands.workflow_context("T1")
    assessment = restarted.task_commands.assess_content("T1", "pre", ())
    assert loaded["stage_contract"] == task["stage_contracts"][0]
    assert assessment.to_dict()["requirements"] == []


def test_stage_contract_initialization_rejects_stale_version_atomically(project):
    tools, _, task = _bootstrap(project)
    runtime = tools.runtime
    _legacy(runtime)
    before = _full_contract_snapshot(runtime)
    with pytest.raises(PoiseError, match="version"):
        _transition(
            runtime,
            "initialize",
            request_id="fresh-stale-initialize",
            expected_version=before["task"][4] + 1,
            contracts=task["stage_contracts"],
            role="creator",
        )
    assert _full_contract_snapshot(runtime) == before


def test_stage_contract_initialization_rejects_unauthorized_role_atomically(project):
    tools, _, task = _bootstrap(project)
    runtime = tools.runtime
    _legacy(runtime)
    before = _full_contract_snapshot(runtime)
    with pytest.raises(PoiseError, match="creator"):
        _transition(
            runtime,
            "initialize",
            request_id="fresh-unauthorized-initialize",
            expected_version=before["task"][4],
            contracts=task["stage_contracts"],
            role="executor",
        )
    assert _full_contract_snapshot(runtime) == before


def test_initialization_rejects_future_output_outside_candidate_scope_atomically(project):
    tools, _, task = _bootstrap(project)
    runtime = tools.runtime
    _legacy(runtime)
    candidate = deepcopy(task["stage_contracts"])
    candidate[0]["allowed_paths"] = ["tests/exact/**"]
    version = runtime.task_queries.record("T1")["version"]
    before = _transition_effects(runtime)

    with pytest.raises(PoiseError, match="path tests is outside allowed_paths of producer tests"):
        _transition(runtime, "initialize", request_id="invalid-future-init",
                    expected_version=version, contracts=candidate)

    assert _transition_effects(runtime) == before


def test_reviewer_revision_rejects_future_output_outside_candidate_scope_atomically(project):
    process, task = _configure(project, inspection=True)
    producer = process["stages"][1]["id"]
    output = "tests/review-output"
    covered_surface = "tests/other/covered.py"
    task["stage_contracts"][1]["allowed_paths"] = [output, "tests/other/**"]
    method = deepcopy(project["task"]["methods"][1])
    method["argv"] = [method["argv"][0], "-m", "unittest", "discover", "-s", output]
    method["verification_plan"]["change_surface"] = [covered_surface]
    method["verification_plan"]["green_stages"] = [producer]
    task["methods"] = [method]
    task["method_inputs"] = [{
        "method_id": method["id"],
        "repository_inputs": [],
        "future_outputs": [{"path": output, "producer_stage": producer}],
        "reference_profile": {
            "runner": "unittest", "parser": "discover-start-directory", "version": 1,
        },
    }]
    task["checks"][producer] = [method["id"]]
    tools = WorkTools(Poise(project["config_path"], "executor"))
    tools.invoke(request("bootstrap", {
        "task": task, "decision": None, "feedback": None, "rework_stage": None,
    }))
    runtime = tools.runtime
    candidate = deepcopy(task["stage_contracts"][1])
    candidate["allowed_paths"] = ["tests/other/**"]
    version = runtime.task_queries.record("T1")["version"]
    before = _transition_effects(runtime)
    candidate_contracts = deepcopy(task["stage_contracts"])
    candidate_contracts[1] = candidate
    with runtime.store.unit_of_work() as unit:
        domain_task = unit.tasks.load("T1")
        domain_task.check_registry.validate_route(
            domain_task.route.with_stage_scopes(candidate_contracts)
        )

    with pytest.raises(PoiseError, match="path tests/review-output is outside allowed_paths"):
        _transition(runtime, "revise", request_id="invalid-future-revise",
                    expected_version=version, stage_id=producer, contract=candidate,
                    role="reviewer")

    assert _transition_effects(runtime) == before
    valid = deepcopy(task["stage_contracts"][1])
    valid["allowed_paths"].append("tests/new/**")
    revised = _transition(runtime, "revise", request_id="valid-future-revise",
                          expected_version=version, stage_id=producer, contract=valid,
                          role="reviewer")
    assert revised["new"]["allowed_paths"] == valid["allowed_paths"]


def test_reviewer_revision_replays_and_records_old_new_history(project):
    tools, _, task = _bootstrap(project, inspection=True)
    runtime = tools.runtime
    contract = deepcopy(task["stage_contracts"][1])
    contract["allowed_paths"] = ["docs/review-fix/**"]
    version = runtime.task_queries.record("T1")["version"]
    before = _raw_snapshot(runtime)
    first = _transition(runtime, "revise", request_id="revise-1", expected_version=version,
                        stage_id=contract["stage_id"], contract=contract, role="reviewer")
    after = _raw_snapshot(runtime)
    restarted = Poise(project["config_path"], "executor")
    replay = _transition(restarted, "revise", request_id="revise-1", expected_version=version,
                         stage_id=contract["stage_id"], contract=contract, role="reviewer")
    history = restarted.stage_contract_context("T1")["history"]
    assert replay["replayed"] is True
    assert replay == {**first, "replayed": True}
    assert _raw_snapshot(restarted) == after
    assert after["task"][4] == before["task"][4] + 1
    assert len(after["events"]) == len(before["events"]) + 1
    assert len(after["journal"]) == len(before["journal"]) + 1
    assert first["old"] != first["new"]
    assert history[-1]["old"] == first["old"] and history[-1]["new"] == first["new"]
    assert history[-1]["reason"] and history[-1]["authorization"] == {"role": "reviewer"}
    assert history[-1]["actor"] == "executor" and history[-1]["request_id"] == "revise-1"


def test_executor_and_non_inspection_stage_cannot_revise_contract(project):
    tools, _, task = _bootstrap(project)
    runtime = tools.runtime
    contract = deepcopy(task["stage_contracts"][0])
    version = runtime.task_queries.record("T1")["version"]
    before = _raw_snapshot(runtime)
    with pytest.raises(PoiseError, match="inspection"):
        _transition(runtime, "revise", request_id="bad-revise", expected_version=version,
                    stage_id=contract["stage_id"], contract=contract, role="reviewer")
    assert _raw_snapshot(runtime) == before


def test_inspection_stage_executor_role_cannot_revise_contract(project):
    tools, _, task = _bootstrap(project, inspection=True)
    runtime = tools.runtime
    contract = deepcopy(task["stage_contracts"][0])
    before = _raw_snapshot(runtime)
    with pytest.raises(PoiseError, match="reviewer"):
        _transition(runtime, "revise", request_id="executor-role", expected_version=before["task"][4],
                    stage_id=contract["stage_id"], contract=contract, role="executor")
    assert _raw_snapshot(runtime) == before


def test_inspection_reviewer_stale_version_cannot_revise_contract(project):
    tools, _, task = _bootstrap(project, inspection=True)
    runtime = tools.runtime
    contract = deepcopy(task["stage_contracts"][0])
    before = _raw_snapshot(runtime)
    with pytest.raises(PoiseError, match="version"):
        _transition(runtime, "revise", request_id="stale-review", expected_version=before["task"][4] + 1,
                    stage_id=contract["stage_id"], contract=contract, role="reviewer")
    assert _raw_snapshot(runtime) == before


def _legacy_guard(project, operation):
    tools, context, task = _bootstrap(project)
    runtime = tools.runtime
    _legacy(runtime)
    before = _raw_snapshot(runtime)
    payload = deepcopy(context["result_template"])
    payload["sections"]["report"] = "Must stay unsubmitted."
    payload["commit_message"] = "test: forbidden legacy operation"
    calls = {
        "acquire": lambda: runtime.ownership.acquire_task("T1"),
        "context": lambda: runtime.task_commands.workflow_context("T1"),
        "submit": lambda: runtime.task_commands.submit("T1", "executor", payload),
    }
    with pytest.raises(PoiseError, match="stage_contract_transition_required"):
        calls[operation]()
    assert _raw_snapshot(runtime) == before
    assert _counts(runtime, "T1")[0]["submissions"] == 0
    version = before["task"][4]
    _transition(runtime, "initialize", request_id=f"unlock-{operation}", expected_version=version,
                contracts=task["stage_contracts"])
    assert runtime.task_commands.workflow_context("T1")["stage_contract"] == task["stage_contracts"][0]


def test_legacy_missing_contract_blocks_start_until_initialize(project):
    _feature()
    _, task = _configure(project)
    runtime = Poise(project["config_path"], "executor")
    # Start admission needs an available Task, not an already bootstrapped run.
    runtime.task_commands.create(
        task, runtime.session, runtime.processes["development"], [],
        {"config_hash": runtime.config_hash}, None, runtime.cfg.get("task_ids"),
        runtime._creation_base(), runtime.cfg["task_decomposition"],
    )
    _legacy(runtime)
    before = _raw_snapshot(runtime)
    assert before["task"][0] == "available"
    assert before["task"][3] is None
    with runtime.store.transaction() as db:
        assert db.execute(
            "SELECT COUNT(*) FROM task_execution WHERE task_id=?", ("T1",)
        ).fetchone()[0] == 0
    with pytest.raises(PoiseError, match="stage_contract_transition_required"):
        runtime.task_commands.start("T1", "executor", {})
    assert _raw_snapshot(runtime) == before
    assert _counts(runtime, "T1")[0]["submissions"] == 0
    _transition(
        runtime, "initialize", request_id="unlock-start", expected_version=before["task"][4],
        contracts=task["stage_contracts"],
    )
    result = WorkTools(runtime).invoke(request("bootstrap", {
        "task": {"id": "T1"}, "decision": None, "feedback": None, "rework_stage": None,
    }))
    assert result["status"] == "active"
    assert runtime.task_commands.workflow_context("T1")["stage_contract"] == task["stage_contracts"][0]


def test_legacy_missing_contract_blocks_acquire_until_initialize(project):
    _legacy_guard(project, "acquire")


def test_legacy_missing_contract_blocks_context_until_initialize(project):
    _legacy_guard(project, "context")


def test_legacy_missing_contract_blocks_submit_until_initialize(project):
    _legacy_guard(project, "submit")


def test_reopen_preserves_initialized_stage_contract(project):
    tools, _, task = _bootstrap(project)
    runtime = tools.runtime
    _legacy(runtime)
    version = runtime.task_queries.record("T1")["version"]
    _transition(runtime, "initialize", request_id="init-reopen", expected_version=version,
                contracts=task["stage_contracts"])
    runtime.handoff({
        "request_id": "reopen-release",
        "reason": "Reload initialized contracts",
        "result": None,
        "commit_message": "WIP: contract persistence",
        "artifact_paths": [],
    })
    restarted = Poise(project["config_path"], "reopener")
    WorkTools(restarted).invoke(request("bootstrap", {
        "task": {"id": "T1"}, "decision": None, "feedback": None, "rework_stage": None,
    }))
    assert restarted.stage_contract_context("T1")["current"] == task["stage_contracts"]


def test_task_specific_scope_narrows_and_expands_template_enforcement(project):
    tools, context, _ = _bootstrap(project, first_scope=["tests", "tests/exact/**", "docs/task/**"])
    scope = tools.runtime.stage_contract_context("T1")["current"][0]["allowed_paths"]
    assert scope == ["tests", "tests/exact/**", "docs/task/**"]
    assert "tests/**" not in scope


def test_inspection_fixture_rejects_mismatched_phases_before_creation(project):
    process, task = _configure(project, inspection=True)
    # Deliberately retain a phase outside the actual two-stage route.
    task["decomposition"]["phases"] = deepcopy(project["task"]["decomposition"]["phases"])
    tools = WorkTools(Poise(project["config_path"], "executor"))
    before = _counts(tools.runtime, "T1")
    with pytest.raises(PoiseError, match="phases"):
        tools.invoke(request("bootstrap", {
            "task": task, "decision": None, "feedback": None, "rework_stage": None,
        }))
    assert _counts(tools.runtime, "T1") == before
    assert before[1] is None
