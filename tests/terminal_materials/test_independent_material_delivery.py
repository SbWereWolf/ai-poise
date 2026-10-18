"""Specific consumers arbitrate retirement; unrelated Tasks continue."""
from copy import deepcopy
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from conftest import WorkPoise
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


def source_read_boundary(source, refusal=False):
    """Pause the real file reader; retain the actual admission and DB owners."""
    def intercept(patch, pause):
        original_fd, original_path = os.open, Path.open
        def selected():
            pause()
            if refusal:
                raise PoiseError("INJECTED-SOURCE-CONSUMER-REFUSAL")
        def opened(path, *args, **kwargs):
            if not isinstance(path, int) and Path(path) == source:
                selected()
            return original_fd(path, *args, **kwargs)
        def opened_path(path, *args, **kwargs):
            if path == source:
                selected()
            return original_path(path, *args, **kwargs)
        patch.setattr(os, "open", opened)
        patch.setattr(Path, "open", opened_path)
    return intercept


def recovery_packet(tools, saved, identity):
    return request("recover_artifacts", {
        "request_id": identity, "task_id": "P",
        "expected_version": tools.runtime.task_queries.record("P")["version"],
        "artifact_ids": [saved["record"]["id"]],
        "source_roots": {"task": str(saved["root"])},
        "reason": "Use the exact released registered source while its owned root is live.",
        "authorization": "The fixture owner authorized this exact registered source operation.",
    })


def ready_source_retirement(project, monkeypatch):
    """Complete real Git cleanup but stop delivery before retirement admission."""
    saved, consumer, report = family(project)
    producer = saved["producer"]
    tree = Path(producer.runtime.task_queries.record("P")["worktree"])
    destination = project["root"] / "permanent-family-input.txt"
    original = os.link
    fired = False
    def obstruct(source, target, *args, **kwargs):
        nonlocal fired
        if Path(target) == destination:
            fired = True
            raise OSError("INJECTED-SOURCE-DELIVERY-STOP")
        return original(source, target, *args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(os, "link", obstruct)
        try:
            producer.invoke(request("integrate", integration_input(
                project, report["commit"], task_id="P", request_id="prepare-live-source")))
        except (OSError, PoiseError) as exc:
            assert "INJECTED-SOURCE-DELIVERY-STOP" in str(exc)
    assert fired, "ARRANGEMENT: source delivery did not reach its file publication"
    assert not tree.exists(), "ARRANGEMENT: source Git resources are still present"
    assert saved["root"].is_dir() and not destination.exists()
    assert Path(saved["record"]["path"]).read_bytes() == b"accepted family result\n"
    return saved, consumer, report


@pytest.mark.parametrize("operation", [
    "agree", "integrate-settle", "reuse-preparation", "bootstrap", "cleanup",
])
def test_unrelated_task_progresses_during_material_effect(project, operation):
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
    elif operation == "reuse-preparation":
        saved, consumer, _ = family(project)
        source = Path(saved["record"]["path"])
        first = lambda: reuse(consumer)
        intercept = pause_read(source)
    else:
        producer, root, source, commit = arrange(project)
        destination = project["root"] / "permanent.txt"
        declaration_packet = delivery_packet(producer, declaration([file_output(source, destination)]))
        if operation == "cleanup":
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
            # The ordered output is already delivered; integration re-observes
            # its bytes before retiring the Task working resources.
            assert destination.read_bytes() == b"customer result\n"
            first = lambda: producer.invoke(request("integrate", integration_input(project, commit)))
            intercept = pause_read(destination)
    b = unrelated(project)
    left, right = paused_overlap(first, lambda: publish(b), intercept)
    assert_material_success(right)
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


def test_delayed_source_operation_protects_its_root(project, monkeypatch):
    saved, _consumer, _report = ready_source_retirement(project, monkeypatch)
    source = Path(saved["record"]["path"])
    caller = WorkTools(WorkPoise(project["config_path"], "held-recovery"))
    b = unrelated(project)
    packet = recovery_packet(caller, saved, "held-source-recovery")
    observer = WorkTools(WorkPoise(project["config_path"], "source-retirement-observer"))
    def retire_and_progress():
        state = settle(observer, identifier="P", request_id="retire-held-source")
        return state, source.read_bytes(), saved["root"].is_dir(), publish(b)
    left, right = paused_overlap(lambda: caller.invoke(packet), retire_and_progress,
                                 source_read_boundary(source))
    state, source_bytes, root_exists, independent = require_success(right, "HELD-SOURCE-RETIREMENT-BLOCKED")
    assert state["status"] == "delivery_blocked"
    assert state["reason"] == "artifact_consumer_pending"
    assert source_bytes == b"accepted family result\n" and root_exists is True
    assert independent["status"] == "artifacts_created"
    independent_path = Path(independent["artifact_paths"][0])
    assert independent_path.read_bytes() == b"B material\n"
    assert require_success(left, "HELD-RECOVERY-FAILED")["status"] == "artifacts_recovered"
    assert caller.invoke(deepcopy(packet))["status"] == "artifacts_recovered"
    assert settle(observer, identifier="P", request_id="retire-held-source")["status"] == "delivery_complete"
    assert not saved["root"].exists()
    assert (project["root"] / "permanent-family-input.txt").read_bytes() == b"accepted family result\n"
    assert independent_path.read_bytes() == b"B material\n"


def test_one_refusal_preserves_another_unfinished_consumer(project, monkeypatch):
    saved, consumer, _report = ready_source_retirement(project, monkeypatch)
    source = Path(saved["record"]["path"])
    b = unrelated(project)
    foreign_file = Path(publish(b)["artifact_paths"][0])
    foreign_inode = foreign_file.stat().st_ino
    packet = request("reuse", {"task_id": "D", "source_task_id": "P",
                              "expected_version": consumer.runtime.task_queries.record("D")["version"],
                              "request_id": "surviving-source-consumer"})
    def stop():
        raise SystemExit("INJECTED-SURVIVING-CONSUMER-INTERRUPTION")
    def interrupted_first_consumer():
        with pytest.MonkeyPatch.context() as patch:
            source_read_boundary(source)(patch, stop)
            return consumer.invoke(packet)
    stopped = isolated_result(interrupted_first_consumer)
    assert stopped[0:2] == ("error", "SystemExit")
    assert stopped[2] == "INJECTED-SURVIVING-CONSUMER-INTERRUPTION"
    second = WorkTools(WorkPoise(project["config_path"], "refused-second-source-user"))
    second_packet = recovery_packet(second, saved, "refused-second-source-use")
    with monkeypatch.context() as patch:
        source_read_boundary(source, refusal=True)(patch, lambda: None)
        with pytest.raises(PoiseError, match="INJECTED-SOURCE-CONSUMER-REFUSAL"):
            second.invoke(second_packet)
    observer = WorkTools(WorkPoise(project["config_path"], "survivor-retirement-observer"))
    state = settle(observer, identifier="P", request_id="retire-with-survivor")
    if state.get("reason") != "artifact_consumer_pending":
        pytest.fail("FOREIGN-CONSUMER-LOST: one refusal released a different unfinished source use", pytrace=False)
    assert saved["root"].is_dir() and source.read_bytes() == b"accepted family result\n"
    assert foreign_file.read_bytes() == b"B material\n" and foreign_file.stat().st_ino == foreign_inode
    resumer = WorkTools(WorkPoise(project["config_path"], "consumer"))
    resumed = resumer.invoke(deepcopy(packet))
    assert resumed["status"] == "reuse_verified" and len(resumed["checks"]) == 1
    assert settle(observer, identifier="P", request_id="retire-with-survivor")["status"] == "delivery_complete"
    assert not saved["root"].exists()
    assert (project["root"] / "permanent-family-input.txt").read_bytes() == b"accepted family result\n"
    assert foreign_file.read_bytes() == b"B material\n" and foreign_file.stat().st_ino == foreign_inode


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
