"""Specific consumers arbitrate retirement; unrelated Tasks continue."""
from copy import deepcopy
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from conftest import WorkPoise, write_json
from poise.application.work import WorkTools
from poise.common import PoiseError
from tests.batch.helpers import bootstrap, request
from tests.batch.test_independent_material_operations import (
    assert_material_success, isolated_result, paused_overlap, pause_link, publish,
)
from tests.result_integration.helpers import integration_input
from tests.runtime_services.test_duplicate_reuse import reuse
from tests.sprints.helpers import setup, task
from .helpers import (agree, arrange, declaration, delivery_packet, delivery_status,
                      file_output, finish, fresh, settle)
from .test_artifact_consumers import family


def unrelated(project):
    contract = task(project, "B")
    contract.update(sprint_id=None, methods=[], method_inputs=[], checks={"work": []})
    tools = WorkTools(WorkPoise(project["config_path"], "independent-material-B"))
    bootstrap(tools, dict(project, task=contract))
    return tools


def pause_read(source):
    def intercept(patch, pause):
        original = os.open

        def opened(path, *args, **kwargs):
            if not isinstance(path, int) and Path(path) == source:
                pause()
            return original(path, *args, **kwargs)
        patch.setattr(os, "open", opened)
    return intercept


def pause_removal(root):
    def intercept(patch, pause):
        original = shutil.rmtree

        def removed(path, *args, **kwargs):
            if Path(path) == root:
                pause()
            return original(path, *args, **kwargs)
        patch.setattr(shutil, "rmtree", removed)
    return intercept


def require_success(value, marker):
    if value[0] == "error" and value[1] == "LockUnavailable":
        pytest.fail(marker + ": shared lock prevented a concrete-record decision", pytrace=False)
    assert value[0] == "ok", value
    return value[1]


@pytest.mark.parametrize("operation", [
    "agree", "integrate-settle", "reuse-preparation", "bootstrap", "handoff", "cleanup",
    "export-preparation",
])
def test_unrelated_task_progresses_during_material_effect(project, operation):
    if operation == "export-preparation":
        project["cfg"]["runtime_services"]["transfer"]["recovery"]["mapping"]["materials"].append({
            "id": "accepted-result", "role": "task-material", "source": "artifacts/result.txt",
            "destination": "artifacts/result.txt", "include": True,
        })
        project["cfg"]["runtime_services"]["transfer"]["recovery"]["mapping"]["materials"].append({
            "id": "check-runs", "role": "task-material", "source": "runs",
            "destination": "runs", "include": True,
        })
        write_json(project["config_path"], project["cfg"])
    if operation == "bootstrap":
        setup(project)
        a = WorkTools(WorkPoise(project["config_path"], "bootstrap-A"))
        contract = task(project, "A")
        contract.update(sprint_id=None, methods=[], method_inputs=[], checks={"work": []})
        root = a.runtime.state / a.runtime.paths["standalone_tasks"] / "A"
        first = lambda: bootstrap(a, dict(project, task=contract))

        def intercept(patch, pause):
            original = os.mkdir
            def mkdir(path, *args, **kwargs):
                if Path(path) == root:
                    pause()
                return original(path, *args, **kwargs)
            patch.setattr(os, "mkdir", mkdir)
    elif operation in ("reuse-preparation", "export-preparation"):
        saved, consumer, _ = family(project)
        source = Path(saved["record"]["path"])
        if operation == "reuse-preparation":
            first = lambda: reuse(consumer)
            intercept = pause_read(source)
        else:
            exporter = WorkTools(WorkPoise(project["config_path"], "export-preparation"))
            first = lambda: exporter.invoke(request("transfer", {
                "action": "export", "request_id": "independent-export-preparation",
                "task_ids": ["P"], "sprint_id": None, "handoff": None,
            }))
            def intercept(patch, pause):
                original = os.open
                original_path = Path.open
                def opened(path, *args, **kwargs):
                    if not isinstance(path, int) and Path(path) == source:
                        pause()
                        # Exercise preparation failure before any external
                        # recovery command; no external harness fixture needed.
                        raise PoiseError("INJECTED-EXPORT-SOURCE-REFUSAL")
                    return original(path, *args, **kwargs)
                patch.setattr(os, "open", opened)
                def opened_path(path, *args, **kwargs):
                    if path == source:
                        pause()
                        raise PoiseError("INJECTED-EXPORT-SOURCE-REFUSAL")
                    return original_path(path, *args, **kwargs)
                patch.setattr(Path, "open", opened_path)
    else:
        if operation == "handoff":
            project["cfg"]["runtime_services"]["handoff"] = {
                "directory": "handoffs", "preserved_directory": "materials",
                "file_mode": 384, "bundle": "source.bundle", "receipt": "receipt.json",
            }
            write_json(project["config_path"], project["cfg"])
        producer, root, source, commit = arrange(project)
        destination = project["root"] / "permanent.txt"
        declaration_packet = delivery_packet(producer, declaration([file_output(source, destination)]))
        if operation == "handoff":
            first = lambda: producer.invoke(request("handoff", {
                "request_id": "independent-handoff", "reason": "Hand the accepted result to its reviewer.",
                "result": None, "commit_message": None, "artifact_paths": [],
            }))
            def intercept(patch, pause):
                original = os.mkdir
                def mkdir(path, *args, **kwargs):
                    if Path(path).is_relative_to(root / "handoffs"):
                        pause()
                    return original(path, *args, **kwargs)
                patch.setattr(os, "mkdir", mkdir)
        elif operation == "cleanup":
            first = lambda: finish(project, producer, commit, "cancelled")
            def intercept(patch, pause):
                original = subprocess.Popen
                def popen(argv, *args, **kwargs):
                    if isinstance(argv, list) and "worktree" in argv and "remove" in argv:
                        pause()
                    return original(argv, *args, **kwargs)
                patch.setattr(subprocess, "Popen", popen)
        elif operation == "agree":
            first = lambda: producer.invoke(declaration_packet)
            intercept = pause_read(source)
        else:
            producer.invoke(declaration_packet)
            producer.invoke(request("accept", {}))
            first = lambda: producer.invoke(request("integrate", integration_input(project, commit)))
            intercept = pause_link(destination)
    b = unrelated(project)
    left, right = paused_overlap(first, lambda: publish(b), intercept)
    assert_material_success(right)
    if operation == "export-preparation":
        assert left[0] == "error" and left[4] is True, left
        assert "INJECTED-EXPORT-SOURCE-REFUSAL" in left[2]
    else:
        require_success(left, "FIRST-OPERATION-BLOCKED")


def test_material_consumer_admission_precedes_source_preparation(project):
    saved, consumer, report = family(project)
    source = Path(saved["record"]["path"])
    b = unrelated(project)
    integration = request("integrate", integration_input(project, report["commit"],
                          request_id="retire-during-source-plan", task_id="P"))

    def retire_and_work():
        saved["producer"].invoke(integration)
        state = delivery_status(saved["producer"], identifier="P")
        reader = WorkTools(WorkPoise(project["config_path"], "second-source-reader"))
        record = reader.runtime.task_queries.record("P")
        reused = reader.invoke(request("recover_artifacts", {
            "request_id": "second-nonexclusive-source-use", "task_id": "P",
            "expected_version": record["version"], "artifact_ids": [saved["record"]["id"]],
            "source_roots": {"task": str(saved["root"])},
            "reason": "Read the same live source without excluding an already admitted consumer.",
            "authorization": "The fixture owner authorized this exact registered source use.",
        }))
        return state, reused, publish(b)

    left, right = paused_overlap(lambda: reuse(consumer), retire_and_work, pause_read(source))
    state, second_use, independent = require_success(right, "CONSUMER-ADMISSION-BLOCKED")
    assert state["status"] == "delivery_blocked"
    assert state["reason"] == "artifact_consumer_pending"
    assert second_use["status"] == "artifacts_recovered"
    assert independent["status"] == "artifacts_created"
    assert require_success(left, "SOURCE-CONSUMER-FAILED")["status"] == "reuse_verified"
    assert source.read_bytes() == b"accepted family result\n"
    assert saved["producer"].invoke(integration)["status"] == "integrated"
    assert not saved["root"].exists()


def test_definitive_material_refusal_releases_only_its_own_consumer(project, monkeypatch):
    saved, consumer, report = family(project)
    source = Path(saved["record"]["path"])
    original = os.open

    def refused(path, *args, **kwargs):
        if not isinstance(path, int) and Path(path) == source:
            raise PoiseError("INJECTED-DEFINITIVE-MATERIAL-REFUSAL")
        return original(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(os, "open", refused)
        with pytest.raises(PoiseError, match="INJECTED-DEFINITIVE-MATERIAL-REFUSAL"):
            reuse(consumer)
    integration = request("integrate", integration_input(project, report["commit"], task_id="P"))
    assert saved["producer"].invoke(integration)["status"] == "integrated"
    assert not saved["root"].exists()


def test_retirement_closes_new_live_material_admission(project):
    saved, consumer, report = family(project)
    root = saved["root"]
    source = Path(saved["record"]["path"])
    b = unrelated(project)
    integration = request("integrate", integration_input(project, report["commit"], task_id="P"))

    def consume_permanent_and_work():
        # This is a filesystem tripwire: the accepted permanent output is ready
        # before retirement starts, and new consumers must use that output.
        original = os.open
        with pytest.MonkeyPatch.context() as patch:
            def opened(path, *args, **kwargs):
                if not isinstance(path, int) and Path(path) == source:
                    raise AssertionError("RETIRING-LIVE-SOURCE-READ")
                return original(path, *args, **kwargs)
            patch.setattr(os, "open", opened)
            result = reuse(consumer)
        return result, publish(b)

    left, right = paused_overlap(lambda: saved["producer"].invoke(integration),
                                 consume_permanent_and_work, pause_removal(root))
    reused, independent = require_success(right, "RETIREMENT-ADMISSION-BLOCKED")
    assert reused["status"] == "reuse_verified"
    assert independent["status"] == "artifacts_created"
    assert require_success(left, "RETIREMENT-FAILED")["status"] == "integrated"
    assert not root.exists()
    assert (project["root"] / "permanent-family-input.txt").read_bytes() == b"accepted family result\n"


def test_original_operation_resumes_unfinished_material_use(project, monkeypatch):
    saved, consumer, report = family(project)
    source = Path(saved["record"]["path"])
    contract = consumer.runtime.task_queries.record("D")
    packet = request("reuse", {"task_id": "D", "source_task_id": "P",
                              "expected_version": contract["version"],
                              "request_id": "resume-exact-material-use"})
    original = os.open
    fired = False

    def interrupted(path, *args, **kwargs):
        nonlocal fired
        if not fired and not isinstance(path, int) and Path(path) == source:
            fired = True
            # Process interruption, not a definitive ordinary error. No external
            # command was started; do not turn this into unknown-check recovery.
            raise SystemExit("INJECTED-SOURCE-USE-INTERRUPTION")
        return original(path, *args, **kwargs)

    def interrupted_process():
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(os, "open", interrupted)
            return consumer.invoke(packet)
    stopped = isolated_result(interrupted_process)
    assert stopped[0:2] == ("error", "SystemExit")
    assert stopped[2] == "INJECTED-SOURCE-USE-INTERRUPTION"
    integration = request("integrate", integration_input(project, report["commit"], task_id="P"))
    saved["producer"].invoke(integration)
    state = delivery_status(saved["producer"], identifier="P")
    if state.get("reason") != "artifact_consumer_pending":
        pytest.fail("UNFINISHED-MATERIAL-USE-WAS-LOST: source retirement ignored an interrupted consumer", pytrace=False)
    assert source.read_bytes() == b"accepted family result\n"
    resumer = WorkTools(WorkPoise(project["config_path"], "consumer"))
    result = resumer.invoke(deepcopy(packet))
    assert result["status"] == "reuse_verified"
    assert len(result["checks"]) == 1
    assert saved["producer"].invoke(integration)["status"] == "integrated"


def test_settlement_conflict_preserves_confirmed_outputs(project, monkeypatch):
    producer, root, source, commit = arrange(project)
    destination = project["root"] / "permanent.txt"
    agree(producer, declaration([file_output(source, destination)]))
    original = os.link

    def stop_initial_delivery(src, dst, *args, **kwargs):
        if Path(dst) == destination:
            raise OSError("INJECTED-INITIAL-DELIVERY-STOP")
        return original(src, dst, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(os, "link", stop_initial_delivery)
        try:
            finish(project, producer, commit, "cancelled")
        except (PoiseError, OSError) as exc:
            assert "INJECTED-INITIAL-DELIVERY-STOP" in str(exc)
    assert root.is_dir() and not destination.exists()
    a, b = fresh(project), WorkTools(WorkPoise(project["config_path"], "settlement-B"))
    def winning_settlement():
        result = settle(b, request_id="settle-B")
        return result, destination.stat().st_ino

    left, right = paused_overlap(lambda: settle(a), winning_settlement, pause_link(destination))
    result, winner_inode = require_success(right, "SETTLEMENT-CAS-BLOCKED")
    assert result["status"] == "delivery_complete"
    assert destination.read_bytes() == b"customer result\n"
    assert destination.stat().st_ino == winner_inode
    inode = destination.stat().st_ino
    assert left[0] == "ok" or (left[0] == "error" and left[4] is True), left
    assert settle(fresh(project))["status"] == "delivery_complete"
    assert destination.stat().st_ino == inode
    assert not root.exists()
