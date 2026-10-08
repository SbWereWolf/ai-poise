"""Bounded optimistic, declaration, destination and acknowledgement partitions."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from poise.common import PoiseError
from batch.helpers import request
from conftest import WorkPoise
from poise.application.work import WorkTools
from sprints.helpers import task
from .helpers import (agree, arrange, declaration, delivery_packet, delivery_status,
                      file_output, finish, fresh, settle)


def registered_foreign(project):
    foreign = WorkTools(WorkPoise(project["config_path"], "foreign-material-owner"))
    contract = task(project, "FOREIGN")
    contract["sprint_id"] = None
    context = foreign.invoke(request("bootstrap", {
        "task": contract, "decision": None, "feedback": None, "rework_stage": None}))
    source = Path(context["task_root"]) / "artifacts" / "foreign.txt"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"foreign owned result\n")
    assert foreign.runtime.task_queries.record("FOREIGN")["claimed_by"] == "foreign-material-owner"
    assert context["status"] == "active" and Path(context["worktree"]).is_dir()
    return foreign, context, source


def test_registered_foreign_arrangement_has_independent_live_owner(project):
    tools, root, source, _ = arrange(project)
    foreign, _, marker = registered_foreign(project)
    assert tools.runtime.task_queries.record("T1")["claimed_by"] == "material-owner"
    assert foreign.runtime.current_task()["id"] == "FOREIGN"
    assert marker.read_bytes() == b"foreign owned result\n"
    assert root.is_dir() and source.read_bytes() == b"customer result\n"


def test_existing_other_owned_task_rejects_agreement_without_changing_either_owner(project):
    tools, root, source, _ = arrange(project)
    foreign, context, marker = registered_foreign(project)
    before = deepcopy(foreign.runtime.task_queries.record("FOREIGN"))
    history = foreign.runtime.task_queries.history("FOREIGN")
    own = deepcopy(tools.runtime.task_queries.record("T1"))
    destination = project["root"] / "foreign-destination.txt"
    output = file_output(marker, destination)
    import hashlib
    output["digest"] = hashlib.sha256(b"foreign owned result\n").hexdigest()
    packet = delivery_packet(tools, declaration([output]), identifier="FOREIGN")
    packet["input"]["expected_version"] = context["version"]
    with pytest.raises(PoiseError, match="(?i)owner|claim|another|foreign|session"):
        tools.invoke(packet)
    assert foreign.runtime.task_queries.record("FOREIGN") == before
    assert foreign.runtime.task_queries.history("FOREIGN") == history
    assert tools.runtime.task_queries.record("T1") == own
    assert tools.runtime.current_task()["id"] == "T1"
    assert foreign.runtime.current_task()["id"] == "FOREIGN"
    assert marker.read_bytes() == b"foreign owned result\n"
    assert root.is_dir() and source.read_bytes() == b"customer result\n"
    assert not destination.exists()


@pytest.mark.parametrize("fault,match", [
    ("stale-version", "version"), ("null-version", "version"),
    ("missing-declaration", "declaration|fields"), ("null-declaration", "declaration"),
    ("unknown-disposition", "disposition"), ("unknown-kind", "kind"),
    ("bad-digest", "digest"), ("duplicate-id", "duplicate|unique|id"),
    ("foreign-task", "Task|task|owner|registered"), ("foreign-source", "source|owner|Task|task"),
])
def test_invalid_fresh_agreement_preserves_owner_and_unagreed_state(project, fault, match):
    tools, root, source, _ = arrange(project)
    destination = project["root"] / "result.txt"
    packet = delivery_packet(tools, declaration([file_output(source, destination)]))
    before = delivery_status(tools)
    history = tools.runtime.task_queries.history("T1")
    value = packet["input"]
    output = value["declaration"]["outputs"][0]
    foreign = root.parent / "FOREIGN" / "input.txt"
    foreign.parent.mkdir()
    foreign.write_bytes(b"foreign source\n")
    if fault == "stale-version":
        value["expected_version"] -= 1
    elif fault == "null-version":
        value["expected_version"] = None
    elif fault == "missing-declaration":
        value.pop("declaration")
    elif fault == "null-declaration":
        value["declaration"] = None
    elif fault == "unknown-disposition":
        value["declaration"]["disposition"] = "automatic"
    elif fault == "unknown-kind":
        output["kind"] = "automatic"
    elif fault == "bad-digest":
        output["digest"] = "invalid"
    elif fault == "duplicate-id":
        value["declaration"]["outputs"].append(deepcopy(output))
    elif fault == "foreign-task":
        value["task_id"] = "FOREIGN"
    else:
        output["source"] = str(source.parent / ".." / ".." / "FOREIGN" / "input.txt")
    with pytest.raises(PoiseError, match=match):
        tools.invoke(packet)
    assert delivery_status(tools) == before
    assert tools.runtime.task_queries.history("T1") == history
    assert source.read_bytes() == b"customer result\n"
    assert foreign.read_bytes() == b"foreign source\n"
    assert not destination.exists()
    assert root.is_dir()


@pytest.mark.parametrize("fault", ["receiver", "digest", "path", "received-bytes",
    "empty", "invalid-utf8", "malformed-json", "null", "array", "missing-field", "wrong-field-type"])
def test_invalid_customer_acknowledgement_never_confirms_or_overwrites_delivery(project, fault):
    tools, root, source, commit = arrange(project)
    received = project["root"] / "received.txt"
    received.write_bytes(b"wrong customer bytes\n" if fault == "received-bytes" else b"customer result\n")
    original = received.read_bytes()
    inode = received.stat().st_ino
    ack = project["root"] / "ack.json"
    receipt = json.loads((Path(__file__).parent / "fixtures" / "protocol.json").read_text())["customer_acknowledgement"]
    receipt["received_path"] = str(received)
    if fault == "receiver":
        receipt["receiver"] = "another-customer"
    elif fault == "digest":
        receipt["digest"] = "0" * 64
    elif fault == "path":
        receipt["received_path"] = str(project["root"] / "unreceived.txt")
    if fault == "missing-field":
        receipt.pop("digest")
    elif fault == "wrong-field-type":
        receipt["receiver"] = ["fixture-customer"]
    encoded = {"empty": b"", "invalid-utf8": b"\xff\xfe", "malformed-json": b"{broken",
               "null": b"null", "array": b"[]"}
    ack.write_bytes(encoded.get(fault, json.dumps(receipt).encode()))
    original_ack = ack.read_bytes()
    output = file_output(source, received, "customer")
    output["acknowledgement"] = {"path": str(ack), "receiver": "fixture-customer"}
    agree(tools, declaration([output]))
    finish(project, tools, commit, "cancelled")
    result = settle(fresh(project))
    assert result["status"] == "delivery_blocked"
    assert result["reason"] == "customer_acknowledgement_invalid"
    assert received.read_bytes() == original and received.stat().st_ino == inode
    assert ack.read_bytes() == original_ack
    assert root.is_dir() and source.read_bytes() == b"customer result\n"


@pytest.mark.parametrize("fault", ["directory", "parent-file"])
def test_wrong_type_destination_blocks_delivery_without_losing_source(project, fault):
    tools, root, source, commit = arrange(project)
    parent = project["root"] / "destination"
    if fault == "directory":
        parent.mkdir()
        sentinel = parent / "foreign.txt"
        sentinel.write_bytes(b"foreign destination\n")
        destination = parent
    else:
        parent.write_bytes(b"foreign destination\n")
        sentinel = parent
        destination = parent / "result.txt"
    agree(tools, declaration([file_output(source, destination)]))
    finish(project, tools, commit, "cancelled")
    result = settle(fresh(project))
    assert result["status"] == "delivery_blocked"
    assert result["reason"] == "destination_unavailable"
    assert sentinel.read_bytes() == b"foreign destination\n"
    assert root.is_dir() and source.read_bytes() == b"customer result\n"


def test_inaccessible_destination_preserves_inputs_and_truthful_failure(project, monkeypatch):
    import os
    tools, root, source, commit = arrange(project)
    destination = project["root"] / "denied-result.txt"
    agree(tools, declaration([file_output(source, destination)]))
    denied = []
    def inaccessible(original):
        def invoke(*args, **kwargs):
            candidates = args[:2]
            if any(isinstance(p, (str, bytes, Path)) and Path(p) == destination for p in candidates):
                denied.append(str(destination))
                raise PermissionError("INJECTED-DESTINATION-EACCES")
            return original(*args, **kwargs)
        return invoke
    for operation in ("open", "replace", "rename", "link"):
        monkeypatch.setattr(os, operation, inaccessible(getattr(os, operation)))
    monkeypatch.setattr(Path, "open", inaccessible(Path.open))
    try:
        first = finish(project, tools, commit, "cancelled")
    except (OSError, PoiseError) as error:
        assert "INJECTED-DESTINATION-EACCES" in str(error)
    else:
        assert first["status"] in ("cleanup_blocked", "delivery_blocked", "delivery_partial")
    assert denied, "The real destination publication must meet the EACCES boundary"
    state = delivery_status(fresh(project))
    assert state["status"] in ("delivery_pending", "delivery_blocked", "delivery_partial")
    assert root.is_dir() and source.read_bytes() == b"customer result\n"
    assert not destination.exists()


def test_task_root_replaced_by_regular_file_never_authorizes_foreign_deletion(project):
    tools, root, source, commit = arrange(project)
    agree(tools, declaration([file_output(source, project["root"] / "result.txt")]))
    original = root.with_name("original-T1")
    root.rename(original)
    root.write_bytes(b"foreign root substitution\n")
    try:
        first = finish(project, tools, commit, "cancelled")
    except PoiseError as error:
        assert any(word in str(error).lower() for word in ("root", "owner", "directory", "path"))
    else:
        assert first["status"] in ("cleanup_blocked", "delivery_blocked")
    assert root.read_bytes() == b"foreign root substitution\n"
    assert (original / "artifacts" / "result.txt").read_bytes() == b"customer result\n"
