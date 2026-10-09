"""Agreed durable destinations and truthful delivery refusals (R0,R1,R3,R4,R6)."""
import json
from pathlib import Path

import pytest

from batch.helpers import request
from conftest import git
from poise.common import PoiseError
from .helpers import (PAYLOAD, PROOF, absent, agree, arrange, declaration,
                      delivery_packet, file_output, finish, fresh, settle)


@pytest.mark.parametrize("member", [False, True], ids=["standalone", "sprint"])
@pytest.mark.parametrize("status", ["completed", "cancelled"])
def test_terminal_routes_deliver_then_remove_entire_owned_folder(project, member, status):
    tools, root, source, commit = arrange(project, member)
    destination = project["root"] / "customer" / "result.txt"
    agree(tools, declaration([file_output(source, destination)]))
    for relative in ("runs/full-stdout.txt", "reports/result.txt", "checkpoints/local.zip",
                     "nested/deep/temporary.bin"):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(PROOF)
    foreign = root.parent / "FOREIGN" / "keep.txt"
    foreign.parent.mkdir(parents=True)
    foreign.write_bytes(b"foreign task\n")
    shared = root.parent.parent / "artifacts" / "shared.txt"
    if member:
        shared.parent.mkdir(parents=True, exist_ok=True)
        shared.write_bytes(b"shared sprint\n")
    before = tools.runtime.task_queries.history("T1")

    finish(project, tools, commit, status)

    absent(root)
    assert destination.read_bytes() == b"customer result\n"
    assert foreign.read_bytes() == b"foreign task\n"
    if member:
        assert shared.read_bytes() == b"shared sprint\n"
    assert tools.runtime.task_queries.record("T1")["status"] == status
    assert tools.runtime.task_queries.history("T1")[:len(before)] == before
    # Proof bytes must not be displaced into another service directory.
    for path in tools.runtime.state.rglob("*"):
        if path.is_file() and path.suffix not in (".sqlite", ".lock"):
            assert PROOF not in path.read_bytes(), str(path)


def test_git_is_delivered_only_when_exact_commit_is_in_agreed_target(project):
    tools, root, _, commit = arrange(project)
    agree(tools, declaration([{
        "id": "source", "kind": "git", "repository": str(project["app"]),
        "target_ref": "refs/heads/main", "commit": commit,
    }]))
    assert tools.invoke(request("accept", {}))["status"] == "completed"
    pending = settle(tools)
    assert pending["status"] == "delivery_blocked"
    assert pending["reason"] == "integration_required"
    assert root.is_dir()
    from result_integration.helpers import integration_input
    tools.invoke(request("integrate", integration_input(project, commit)))
    absent(root)
    assert git(project["app"], "merge-base", commit, "main") == commit
    assert git(project["app"], "show", "main:src/delivered.py") == "VALUE = 'agreed result'"


def test_ignored_configuration_delivery_preserves_bytes_and_stays_out_of_git(project):
    tools, root, source, commit = arrange(project)
    destination = project["app"] / "local" / "operator.env"
    exclude = Path(git(project["app"], "rev-parse", "--absolute-git-dir")) / "info" / "exclude"
    with exclude.open("a") as stream:
        stream.write("\n/local/\n")
    agree(tools, declaration([file_output(source, destination, "ignored_configuration")]))
    finish(project, tools, commit, "completed")
    absent(root)
    assert destination.read_bytes() == b"customer result\n"
    assert git(project["app"], "check-ignore", "local/operator.env") == "local/operator.env"
    assert git(project["app"], "ls-files", "local/operator.env") == ""


@pytest.mark.parametrize("acknowledged", [False, True])
def test_customer_delivery_requires_observed_received_bytes_and_acknowledgement(project, acknowledged):
    tools, root, source, commit = arrange(project)
    customer = project["root"] / "customer"
    customer.mkdir()
    received = customer / "received.txt"
    acknowledgement = customer / "ack.json"
    received.write_bytes(PAYLOAD)
    if acknowledged:
        # Test-owned customer receipt; the product must not manufacture this.
        receipt = json.loads((Path(__file__).parent / "fixtures" / "protocol.json").read_text())["customer_acknowledgement"]
        receipt["received_path"] = str(received)
        acknowledgement.write_text(json.dumps(receipt))
    output = file_output(source, received, "customer")
    output["acknowledgement"] = {"path": str(acknowledgement), "receiver": "fixture-customer"}
    agree(tools, declaration([output]))
    finish(project, tools, commit, "cancelled")
    if acknowledged:
        absent(root)
    else:
        assert root.is_dir()
        result = settle(fresh(project))
        assert result["status"] == "delivery_blocked"
        assert result["reason"] == "customer_acknowledgement_required"
        assert not acknowledgement.exists()
    assert received.read_bytes() == b"customer result\n"


@pytest.mark.parametrize("existing", [b"customer result\n", b"foreign destination\n"])
def test_identical_delivery_is_reused_and_conflicting_destination_is_never_overwritten(project, existing):
    tools, root, source, commit = arrange(project)
    destination = project["root"] / "result.txt"
    destination.write_bytes(existing)
    inode = destination.stat().st_ino
    agree(tools, declaration([file_output(source, destination)]))
    finish(project, tools, commit, "cancelled")
    if existing == b"customer result\n":
        absent(root)
        assert destination.stat().st_ino == inode
    else:
        result = settle(fresh(project))
        assert result["status"] == "delivery_blocked"
        assert result["reason"] == "destination_conflict"
        assert root.is_dir() and source.read_bytes() == b"customer result\n"
    assert destination.read_bytes() == existing


def test_one_failed_item_blocks_retirement_of_all_remaining_source_materials(project):
    tools, root, source, commit = arrange(project)
    first = project["root"] / "first.txt"
    second = project["root"] / "second.txt"
    second.write_bytes(b"foreign destination\n")
    a, b = file_output(source, first), file_output(source, second)
    b["id"] = "second-file"
    agree(tools, declaration([a, b]))
    finish(project, tools, commit, "cancelled")
    assert root.is_dir() and source.read_bytes() == b"customer result\n"
    assert second.read_bytes() == b"foreign destination\n"
    assert settle(fresh(project))["status"] == "delivery_blocked"


@pytest.mark.parametrize("location", ["inside", "alias", "live-root"])
def test_agreement_refuses_a_task_local_alias_or_protected_destination(project, location):
    tools, root, source, _ = arrange(project)
    if location == "inside":
        destination = root / "permanent.txt"
    elif location == "alias":
        link = project["root"] / "alias"
        link.symlink_to(root, target_is_directory=True)
        destination = link / "permanent.txt"
    else:
        destination = project["config_path"]
    before = Path(project["config_path"]).read_bytes()
    packet = delivery_packet(tools, declaration([file_output(source, destination)]))
    with pytest.raises(PoiseError, match="destination|owner|Task|symlink|protected"):
        tools.invoke(packet)
    assert Path(project["config_path"]).read_bytes() == before
    assert source.read_bytes() == b"customer result\n"
    assert root.is_dir()


def test_active_or_unagreed_terminal_task_cannot_be_retired(project):
    tools, root, source, commit = arrange(project)
    try:
        result = settle(tools)
    except PoiseError as exc:
        if str(exc) == "Unknown work operation":
            pytest.fail("DELIVERY-AGREEMENT-MISSING: explicit destination agreement is unavailable")
        raise
    assert result["status"] == "delivery_blocked"
    assert result["reason"] == "agreement_required"
    assert root.is_dir()
    assert tools.invoke(request("accept", {}))["status"] == "completed"
    assert settle(tools)["reason"] == "agreement_required"
    assert root.is_dir() and source.read_bytes() == b"customer result\n"


def test_agreement_exact_replay_does_not_change_contract_and_changed_intent_rejects(project):
    tools, root, source, _ = arrange(project)
    packet = agree(tools, declaration([file_output(source, project["root"] / "result.txt")]))
    assert tools.invoke(packet)["replayed"] is True
    packet["input"]["declaration"]["outputs"][0]["destination"] = str(project["root"] / "other.txt")
    with pytest.raises(PoiseError, match="identity|intent|digest|request"):
        tools.invoke(packet)
    assert root.is_dir()


def test_agreed_otherwise_eligible_nonterminal_task_is_never_retired(project):
    tools, root, source, commit = arrange(project)
    # Integrate the exact fixture commit first, so unfinished integration is
    # not a substitute for the independent terminal-status admission guard.
    git(project["app"], "merge", "--ff-only", commit)
    destination = project["root"] / "result.txt"
    agree(tools, declaration([file_output(source, destination)]))
    history = tools.runtime.task_queries.history("T1")
    result = settle(tools)
    assert result["status"] == "delivery_blocked"
    assert result["reason"] == "task_not_terminal"
    assert tools.runtime.task_queries.record("T1")["status"] == "verified"
    assert tools.runtime.task_queries.history("T1") == history
    assert root.is_dir() and source.read_bytes() == b"customer result\n"
    finish(project, tools, commit, "cancelled")
    absent(root)
    assert destination.read_bytes() == b"customer result\n"
