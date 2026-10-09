"""Actual required registered-artifact reuse before and after source retirement."""
import hashlib
import os
from copy import deepcopy
from pathlib import Path

import pytest

from batch.helpers import request
from poise.common import PoiseError
from runtime_services.test_duplicate_reuse_artifacts import artifact_family
from runtime_services.test_duplicate_reuse import reuse
from poise.application.work import WorkTools
from result_integration.helpers import integration_input
from .helpers import absent, agree, declaration, delivery_status


def family(project):
    saved = {}
    def agreement(producer, context, verified_report):
        records = producer.runtime.store.artifact_records("P")
        assert len(records) == 1
        source = Path(records[0]["path"])
        assert source.read_bytes() == b"accepted family result\n"
        outputs = [{"id": "family-code", "kind": "git", "repository": str(project["app"]),
                    "target_ref": "refs/heads/main", "commit": verified_report["commit"]},
                   {"id": "required-family-input", "kind": "file", "source": str(source),
                    "destination": str(project["root"] / "permanent-family-input.txt"),
                    "digest": hashlib.sha256(b"accepted family result\n").hexdigest()}]
        agree(producer, declaration(outputs), identifier="P")
        saved.update(producer=producer, root=Path(context["task_root"]), record=records[0])
    receiver, report = artifact_family(project, before_accept=agreement)
    return saved, WorkTools(receiver), report


def test_reserved_required_artifact_consumer_blocks_source_retirement_until_delivery(project, monkeypatch):
    saved, consumer, report = family(project)
    root = saved["root"]
    destination = consumer.runtime.state / consumer.runtime.paths["sprints"] / "S" / "task" / "D" / "artifacts" / "result.txt"
    original = os.link
    fired = []
    def interrupted(src, dst, *args, **kwargs):
        if Path(dst) == destination:
            fired.append(str(dst))
            raise OSError("INJECTED-REQUIRED-ARTIFACT-PUBLICATION")
        return original(src, dst, *args, **kwargs)
    with monkeypatch.context() as fault:
        fault.setattr(os, "link", interrupted)
        with pytest.raises(PoiseError, match="INJECTED-REQUIRED-ARTIFACT-PUBLICATION"):
            reuse(consumer, request_id="required-artifact-consumption")
        assert fired and not destination.exists()
        candidate = deepcopy(consumer.runtime.task_queries.record("D")["duplicate_reuse"]["candidate"])
        assert candidate["source_task_id"] == "P"
        assert candidate["artifact_source"]["records"][0]["path"] == saved["record"]["path"]
    packet = request("integrate", integration_input(project, report["commit"],
        request_id="source-before-consumer-finished", task_id="P"))
    first = saved["producer"].invoke(packet)
    assert first["status"] != "integrated"
    state = delivery_status(saved["producer"], identifier="P")
    assert state["status"] == "delivery_blocked" and state["reason"] == "artifact_consumer_pending"
    assert root.is_dir() and Path(saved["record"]["path"]).read_bytes() == b"accepted family result\n"
    # Original candidate identity/version, not a newly constructed reuse intent.
    verified = consumer.invoke(request("reuse", candidate["request"]))
    assert verified["status"] == "reuse_verified"
    assert destination.read_bytes() == b"accepted family result\n"
    assert consumer.runtime.task_queries.record("D")["status"] == "verified"
    assert saved["producer"].invoke(packet)["status"] == "integrated"
    absent(root)
    assert (project["root"] / "permanent-family-input.txt").read_bytes() == b"accepted family result\n"
    assert consumer.runtime.task_queries.record("D")["duplicate_reuse"]["candidate"] == candidate
    local = consumer.runtime.store.artifact_records("D")[0]
    assert local["id"] != saved["record"]["id"]
    assert local["digest"] == saved["record"]["digest"] == hashlib.sha256(b"accepted family result\n").hexdigest()
    assert consumer.invoke(request("accept", {}))["status"] == "completed"


def retired_family(project):
    saved, consumer, report = family(project)
    assert saved["producer"].invoke(request("integrate", integration_input(
        project, report["commit"], task_id="P")))["status"] == "integrated"
    absent(saved["root"])
    permanent = project["root"] / "permanent-family-input.txt"
    assert permanent.read_bytes() == b"accepted family result\n"
    from conftest import git
    assert git(project["app"], "merge-base", report["commit"], "main") == report["commit"]
    assert git(project["app"], "show", "main:src/double.py") == "def double(n):\n    return n * 2"
    return saved, consumer, report, permanent


def test_new_post_retirement_artifact_consumption_uses_agreed_permanent_input(project):
    saved, consumer, report, permanent = retired_family(project)
    verified = reuse(consumer)
    assert verified["status"] == "reuse_verified"
    assert len(verified["checks"]) == 1 and verified["checks"][0]["passed"]
    assert verified["checks"][0]["id"] != report["checks"][0]["id"]
    artifact = consumer.runtime.store.artifact_records("D")[0]
    assert artifact["id"] != saved["record"]["id"]
    assert artifact["digest"] == saved["record"]["digest"] == hashlib.sha256(b"accepted family result\n").hexdigest()
    assert Path(artifact["path"]).read_bytes() == b"accepted family result\n"
    assert consumer.invoke(request("accept", {}))["status"] == "completed"
    assert permanent.read_bytes() == b"accepted family result\n"
    absent(saved["root"])


@pytest.mark.parametrize("fault", ["missing", "changed"])
def test_new_reuse_refuses_corrupt_required_permanent_input_before_claim_or_checks(project, fault):
    saved, consumer, _, permanent = retired_family(project)
    sentinel = project["root"] / "foreign.txt"
    sentinel.write_bytes(b"foreign data\n")
    if fault == "missing":
        permanent.unlink()
    else:
        permanent.write_bytes(b"corrupt required permanent result\n")
    before = deepcopy(consumer.runtime.task_queries.record("D"))
    history = consumer.runtime.task_queries.history("D")
    counts = consumer.runtime.store.counts("D")
    parent = deepcopy(consumer.runtime.task_queries.record("P"))
    parent_counts = consumer.runtime.store.counts("P")
    assert before["claimed_by"] is None and consumer.runtime.current_task() is None
    assert consumer.runtime.store.artifact_records("D") == []
    with pytest.raises(PoiseError, match="(?i)digest|integrity|missing|changed|permanent|artifact"):
        reuse(consumer)
    assert consumer.runtime.task_queries.record("D") == before
    assert consumer.runtime.task_queries.history("D") == history
    assert consumer.runtime.store.counts("D") == counts
    assert consumer.runtime.task_queries.record("P") == parent
    assert consumer.runtime.store.counts("P") == parent_counts
    assert consumer.runtime.store.artifact_records("D") == []
    assert consumer.runtime.current_task() is None
    if fault == "missing":
        assert not permanent.exists()
    else:
        assert permanent.read_bytes() == b"corrupt required permanent result\n"
    assert sentinel.read_bytes() == b"foreign data\n"
    absent(saved["root"])


@pytest.mark.parametrize("fault", ["missing", "changed"])
def test_accept_after_source_retirement_still_validates_local_required_bytes(project, fault):
    saved, consumer, _, permanent = retired_family(project)
    verified = reuse(consumer)
    assert verified["status"] == "reuse_verified"
    local = consumer.runtime.store.artifact_records("D")[0]
    assert local["id"] != saved["record"]["id"]
    assert local["digest"] == saved["record"]["digest"] == hashlib.sha256(b"accepted family result\n").hexdigest()
    path = Path(local["path"])
    assert path.read_bytes() == b"accepted family result\n"
    if fault == "missing":
        path.unlink()
    else:
        path.write_bytes(b"corrupt local required result\n")
    before = deepcopy(consumer.runtime.task_queries.record("D"))
    counts = consumer.runtime.store.counts("D")
    assert before["status"] == "verified"
    with pytest.raises(PoiseError, match="(?i)digest|integrity|missing|changed|artifact|артефакт"):
        consumer.invoke(request("accept", {}))
    assert consumer.runtime.task_queries.record("D") == before
    assert consumer.runtime.store.counts("D") == counts
    assert consumer.runtime.current_task()["id"] == "D"
    assert permanent.read_bytes() == b"accepted family result\n"
    absent(saved["root"])
