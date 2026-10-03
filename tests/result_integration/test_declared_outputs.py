"""Integration must preserve the ordinary declared-output producer contract."""
from hashlib import sha256
from pathlib import Path
import sys

import pytest

from conftest import git
from poise.common import PoiseError
from poise.infrastructure.result_integration import RuntimeResultIntegration
from .helpers import integration_input, prepare_completed_task, request, source_change


def output_method(mode, *, required=True, declarations=True, failure_marker=None):
    producer = Path(__file__).with_name("fixtures").joinpath("declared_output_producer.py").read_text()
    return {
        "id": "DECLARED_RESULT",
        "argv": [sys.executable, "-c", producer],
        "cwd": ".", "environment": {
            "FIXTURE_OUTPUT_MODE": mode,
            "FIXTURE_OUTPUT_FAILURE_MARKER": "" if failure_marker is None else str(failure_marker),
        },
        "source_under_test": {"kind": "repository", "bindings": [{"kind": "cwd", "path": "."}]},
        "expected_exit_code": 0, "stdout_contains": ["producer-ran"], "stderr_contains": [],
        "outputs": [{"id": "result", "path": "result.txt", "required": required}] if declarations else [],
    }


def integrate(project, method):
    mode_file = project["root"] / "integration-output-mode.txt"
    mode = method["environment"]["FIXTURE_OUTPUT_MODE"]
    method["environment"]["FIXTURE_OUTPUT_MODE"] = "required"
    method["environment"]["FIXTURE_OUTPUT_MODE_FILE"] = str(mode_file)
    tools, worktree, accepted = prepare_completed_task(
        project, source_change, methods=[method], checks=[method["id"]],
    )
    mode_file.write_text(mode)
    packet = request("integrate", integration_input(project, accepted))
    return tools, worktree, accepted, packet


def invoke_with_producer_proof(tools, packet):
    result = tools.invoke(packet)
    check = result["checks"][-1]
    assert check["actual_exit_code"] == 0, Path(check["stderr"]).read_text()
    return result


@pytest.mark.parametrize("mode,required,declarations,passed,status", [
    ("required", True, True, True, "captured"),
    ("empty-file", True, True, True, "captured"),
    ("missing", True, True, False, "missing"),
    ("optional", False, True, True, "captured"),
    ("missing", False, True, True, "missing"),
    ("none", True, False, True, None),
    ("invalid", True, True, False, "invalid"),
    ("optional-invalid", False, True, False, "invalid"),
])
def test_public_integration_declared_output_contract(project, mode, required, declarations, passed, status):
    tools, worktree, accepted, packet = integrate(project, output_method(mode, required=required, declarations=declarations))
    target = git(project["app"], "rev-parse", "HEAD")
    foreign = project["app"] / "unrelated.note"
    foreign.write_bytes(b"keep unrelated WIP\n")
    done = invoke_with_producer_proof(tools, packet)
    assert done["status"] == ("integrated" if passed else "blocked")
    assert done["accepted_commit"] == accepted
    check = done["checks"][0]
    assert check["actual_exit_code"] == 0
    assert check["passed"] is passed
    assert Path(check["stdout"]).read_text() == "producer-ran\n"
    assert Path(check["stderr"]).read_bytes() == b""
    assert check["integration_head"] == done["integration_head"]
    assert len(check["definition_digest"]) == len(check["contract_digest"]) == 64
    outputs = check["outputs"]
    if not declarations:
        assert outputs == []
    else:
        assert len(outputs) == 1
        output = outputs[0]
        assert output["id"] == "result" and output["declared_path"] == "result.txt"
        assert output["required"] is required and output["status"] == status
        if status == "captured":
            expected = b"" if mode == "empty-file" else b"candidate output\n"
            path = Path(output["path"])
            assert path.parent == Path(check["stdout"]).parent / "outputs"
            assert path.read_bytes() == expected
            assert output["size"] == len(expected)
            assert output["digest"] == sha256(expected).hexdigest()
        else:
            assert output["path"] is output["digest"] is output["size"] is None
    assert foreign.read_bytes() == b"keep unrelated WIP\n"
    if passed:
        assert done["target_after"] == git(project["app"], "rev-parse", "HEAD")
        assert check["verified_tree"] == git(project["app"], "rev-parse", "HEAD^{tree}")
        assert not worktree.exists()
        assert tools.invoke(packet)["replayed"] is True
    else:
        assert done["phase"] == "checks_failed" and done["publication"] is None
        assert git(project["app"], "rev-parse", "HEAD") == target
        assert git(worktree, "rev-parse", "HEAD") == done["integration_head"]
        assert tools.runtime.task_queries.record("T1")["result_commit"] == accepted


def test_public_integration_rejects_output_escape_without_publication(project):
    tools, worktree, accepted, packet = integrate(project, output_method("escape"))
    target = git(project["app"], "rev-parse", "HEAD")
    with pytest.raises(PoiseError, match="Declared output escapes"):
        invoke_with_producer_proof(tools, packet)
    assert git(project["app"], "rev-parse", "HEAD") == target
    assert worktree.exists()
    assert tools.runtime.task_queries.record("T1")["result_commit"] == accepted


@pytest.mark.parametrize("damage", ["changed-bytes", "deleted"])
@pytest.mark.parametrize("fresh_failure", [False, True])
def test_retry_rechecks_corrupt_declared_snapshot_before_publication(project, monkeypatch, damage, fresh_failure):
    marker = project["root"] / "output-failure.marker"
    tools, worktree, accepted, packet = integrate(project, output_method("required", failure_marker=marker))
    target = git(project["app"], "rev-parse", "HEAD")

    def postpone_publication(self, run, repository):
        return run

    with monkeypatch.context() as patch:
        patch.setattr(RuntimeResultIntegration, "_publish", postpone_publication)
        first = invoke_with_producer_proof(tools, packet)
    assert first["phase"] == "publishing"
    assert first["publication"] is None
    original = first["checks"][0]
    snapshot = Path(original["outputs"][0]["path"])
    if damage == "changed-bytes":
        snapshot.write_bytes(b"corrupt captured output\n")
    else:
        snapshot.unlink()
    assert git(project["app"], "rev-parse", "HEAD") == target
    if fresh_failure:
        marker.write_bytes(b"withhold required output on fresh invocation\n")
    done = tools.invoke(packet)
    assert done["status"] == ("blocked" if fresh_failure else "integrated")
    assert done["accepted_commit"] == accepted
    fresh = done["checks"][-1]
    assert fresh["id"] != original["id"]
    assert fresh["integration_head"] == first["integration_head"]
    assert fresh["actual_exit_code"] == 0
    if fresh_failure:
        assert fresh["passed"] is False
        assert fresh["outputs"][0]["status"] == "missing"
        assert done["phase"] == "checks_failed" and done["publication"] is None
        assert git(project["app"], "rev-parse", "HEAD") == target
        assert worktree.exists()
    else:
        assert Path(fresh["outputs"][0]["path"]).read_bytes() == b"candidate output\n"
        assert len({check["outputs"][0]["path"] for check in done["checks"]}) == 2
        assert not worktree.exists()
    assert Path(original["stdout"]).read_text() == "producer-ran\n"
