"""Complete public invocation after physical retirement, not internal-only replay (R5,R7)."""
from copy import deepcopy
import os

import pytest

from batch.helpers import request
from poise.common import PoiseError
from .helpers import (absent, agree, arrange, cli, declaration, file_output,
                      finish, fresh, settle)


@pytest.mark.parametrize("status", ["completed", "cancelled"])
@pytest.mark.parametrize("member", [False, True])
def test_terminal_cli_read_never_recreates_retired_folder_or_binds_task(project, status, member):
    tools, root, source, commit = arrange(project, member)
    destination = project["root"] / "result.txt"
    agree(tools, declaration([file_output(source, destination)]))
    finish(project, tools, commit, status)
    absent(root)
    before = tools.runtime.task_queries.history("T1")
    counts = tools.runtime.store.counts("T1")
    packet = request("bootstrap", {
        "task": {"id": "T1"}, "decision": None, "feedback": None, "rework_stage": None,
    })
    for _ in range(2):
        code, result = cli(project, packet)
        assert code == 0
        assert result["status"] == status
        assert result["result_template"] is None
        absent(root)
        assert tools.runtime.task_queries.history("T1") == before
        assert tools.runtime.store.counts("T1") == counts
    code, taskless = cli(project, request("bootstrap", {
        "task": None, "decision": None, "feedback": None, "rework_stage": None,
    }))
    assert code == 0 and taskless["status"] == "read_only"
    code, checked = cli(project, request("verify", {"result": None, "artifacts": []}))
    assert code == 0 and checked["status"] == "read_only_verified"
    absent(root)
    assert destination.read_bytes() == b"customer result\n"


@pytest.mark.parametrize("status", ["completed", "cancelled"])
def test_settlement_exact_replay_keeps_delivery_and_deleted_materials_unchanged(project, status):
    tools, root, source, commit = arrange(project)
    destination = project["root"] / "result.txt"
    agree(tools, declaration([file_output(source, destination)]))
    finish(project, tools, commit, status)
    absent(root)
    resumer = fresh(project)
    first = settle(resumer)
    assert first["status"] == "delivery_complete"
    inode = destination.stat().st_ino
    history = resumer.runtime.task_queries.history("T1")
    second = settle(resumer)
    assert second["status"] == "delivery_complete" and second["replayed"] is True
    assert destination.stat().st_ino == inode
    assert destination.read_bytes() == b"customer result\n"
    assert resumer.runtime.task_queries.history("T1") == history
    absent(root)


def test_integration_exact_cli_replay_preserves_historical_receipt_without_old_check_files(project):
    from result_integration.helpers import integration_input
    tools, root, source, commit = arrange(project)
    destination = project["root"] / "result.txt"
    agree(tools, declaration([file_output(source, destination)]))
    tools.invoke(request("accept", {}))
    packet = request("integrate", integration_input(project, commit))
    first = tools.invoke(packet)
    assert first["status"] == "integrated"
    absent(root)
    # Exact original packet, not a newly calculated target or rewritten intent.
    code, result = cli(project, packet)
    assert code == 0 and result["status"] == "integrated" and result["replayed"] is True
    assert result["source_commit"] == commit
    assert result["target_after"] == first["target_after"]
    absent(root)


def test_terminal_refusal_of_new_verify_does_not_create_a_response_archive(project):
    tools, root, source, commit = arrange(project)
    agree(tools, declaration([file_output(source, project["root"] / "result.txt")]))
    finish(project, tools, commit, "cancelled")
    absent(root)
    code, result = cli(project, request("verify", {"result": {}, "artifacts": []}))
    assert code == 2 and result["status"] == "rejected"
    absent(root)


def test_changed_delivery_agreement_after_retirement_is_not_accepted_as_exact_replay(project):
    tools, root, source, commit = arrange(project)
    packet = agree(tools, declaration([file_output(source, project["root"] / "result.txt")]))
    finish(project, tools, commit, "cancelled")
    changed = deepcopy(packet)
    changed["input"]["declaration"]["outputs"][0]["digest"] = "0" * 64
    with pytest.raises(PoiseError, match="identity|intent|digest|request"):
        fresh(project).invoke(changed)
    absent(root)


def test_cleanup_exact_cli_replay_after_retirement_preserves_native_receipt(project):
    from .helpers import cancel_and_discard
    tools, root, source, commit = arrange(project)
    agree(tools, declaration([file_output(source, project["root"] / "result.txt")]))
    first = cancel_and_discard(tools, commit)
    absent(root)
    packet = request("cleanup", {
        "task_id": "T1", "request_id": "explicit-resource-disposition",
        "authorization": "The fixture customer explicitly discards this exact unique commit.",
        "commit_disposition": {"kind": "discard_authorized", "expected_commit": commit},
    })
    code, replay = cli(project, packet)
    assert code == 0 and replay["status"] == "cleanup_complete" and replay["replayed"] is True
    assert replay["history"] == first["history"]
    absent(root)


@pytest.mark.parametrize("corrupt", [False, True])
def test_export_exact_replay_uses_permanent_package_integrity_after_retirement(project, corrupt):
    from pathlib import Path
    from transfer.helpers import enabled, export
    enabled(project)
    tools, root, source, commit = arrange(project)
    agree(tools, declaration([file_output(source, project["root"] / "result.txt")]))
    tools.invoke(request("accept", {}))
    exported = export(tools, ids=["T1"], request_id="permanent-customer-export")
    package = Path(exported["package_path"])
    assert not package.is_relative_to(root)
    original = package.read_bytes()
    from result_integration.helpers import integration_input
    tools.invoke(request("integrate", integration_input(project, commit)))
    absent(root)
    if corrupt:
        package.write_bytes(b"corrupt permanent input\n")
        with pytest.raises(PoiseError, match="digest|integrity|package"):
            export(tools, ids=["T1"], request_id="permanent-customer-export")
        assert package.read_bytes() == b"corrupt permanent input\n"
    else:
        replay = export(tools, ids=["T1"], request_id="permanent-customer-export")
        assert replay["status"] == "exported" and replay["replayed"] is True
        assert replay["package_digest"] == exported["package_digest"]
        assert package.read_bytes() == original
    absent(root)


def test_completed_reuse_exact_cli_replay_does_not_rebuild_working_materials(project):
    from pathlib import Path
    from conftest import git
    from poise.application.work import WorkTools
    from runtime_services.test_duplicate_reuse import family_result, reuse
    def agree_source(producer, _context, report):
        assert producer.runtime.task_queries.record("P")["status"] == "verified"
        agree(producer, declaration([{
            "id": "source-code", "kind": "git", "repository": str(project["app"]),
            "target_ref": "refs/heads/main", "commit": report["commit"],
        }]), identifier="P")
    receiver, source = family_result(project, before_accept=agree_source)
    # The helper's actual merge confirms the declared permanent Git input before
    # D consumes it; no_result cannot describe this existing reused code result.
    assert git(project["app"], "merge-base", source["commit"], "main") == source["commit"]
    assert git(project["app"], "show", "main:src/double.py") == "def double(n):\n    return n * 2"
    client = WorkTools(receiver)
    reused = reuse(client)
    assert reused["source_commit"] == reused["commit"] == source["commit"]
    root = Path(receiver._context(receiver.current_task(), prepare=False)["task_root"])
    agree(client, declaration([{
        "id": "reused-code", "kind": "git", "repository": str(project["app"]),
        "target_ref": "refs/heads/main", "commit": reused["commit"],
    }]), identifier="D")
    assert client.invoke(request("accept", {}))["status"] == "completed"
    assert settle(fresh(project), identifier="D")["status"] == "delivery_complete"
    absent(root)
    code, replay = cli(project, request("reuse", reused["request"]))
    assert code == 0 and replay["status"] == "completed" and replay["replayed"] is True
    assert receiver.current_task() is None
    assert replay["checks"] == reused["checks"]
    assert git(project["app"], "merge-base", source["commit"], "main") == source["commit"]
    assert git(project["app"], "show", "main:src/double.py") == "def double(n):\n    return n * 2"
    absent(root)
