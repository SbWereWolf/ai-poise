"""Accepted file proofs and preserved work through real post-restart replay."""

from pathlib import Path

import pytest

from conftest import git
from runtime_services.restart_auto_support import assert_projection, assert_recovery, launch, prepared


@pytest.mark.parametrize("name,contents", [
    ("accepted.txt", b"accepted text\n"), ("accepted.weird", b"\x00\xff\x80"),
    ("accepted.json", b"{}"), ("no-extension", b"arbitrary bytes"),
])
def test_any_unchanged_accepted_file_is_sufficient(project, name, contents):
    case = prepared(project, file_proof=contents, proof_name=name, checks=False)
    response = launch(case, target="code_review")
    assert response["status"] == "progression_target_reached"
    assert response["stage"] == "code_review"
    assert [item["stage"] for item in response["replay"]["passed"]] == [
        "tests", "test_review", "implementation",
    ]
    assert case["proof"].read_bytes() == contents
    assert case["log"].read_text() == ""
    assert_recovery(case, response)
    assert_projection(case, response)


@pytest.mark.parametrize("mutation,reason", [
    ("missing", "evidence_missing"), ("changed", "evidence_changed"),
])
def test_required_file_failure_stops_at_first_gate(project, mutation, reason):
    case = prepared(project, file_proof=b"accepted bytes", checks=False)
    if mutation == "missing":
        case["proof"].unlink()
        (case["proof"].parent / "unregistered.bin").write_bytes(b"accepted bytes")
    else:
        case["proof"].write_bytes(b"changed bytes")
    response = launch(case, target="code_review")
    assert response["status"] == "progression_stopped"
    assert response["stage"] == "tests"
    assert response["replay"]["reason"] == reason
    assert response["replay"]["passed"] == []
    assert response["replay"]["subject_commit"] == case["saved"]
    assert git(case["root"], "rev-parse", "HEAD") == case["saved"]
    assert case["log"].read_text() == ""
    assert_recovery(case, response)
    assert_projection(case, response, stage="tests", reason=reason, count=0,
                      subject=case["saved"])


def test_missing_historical_diagnostic_commit_does_not_block_current_recheck(project):
    case = prepared(project)
    source = case["commits"][0]
    common = Path(git(case["root"], "rev-parse", "--path-format=absolute", "--git-common-dir"))
    (common / "objects" / source[:2] / source[2:]).unlink()
    response = launch(case, target="code_review")
    assert response["status"] == "progression_target_reached"
    assert git(case["root"], "rev-parse", "HEAD") == case["saved"]
    assert case["log"].read_text().splitlines() == [case["saved"]] * 3
    assert_projection(case, response)
    # Prior visit identity remains diagnostic; it is never forged as new proof.
    assert response["replay"]["passed"][0]["commit"] == source


def test_mixed_gate_rechecks_current_tests_and_preserves_file(project):
    case = prepared(project, file_proof=b"accepted mixed proof")
    response = launch(case, target="code_review")
    assert response["stage"] == "code_review"
    if case["log"].read_text().splitlines() != [case["saved"]] * 3:
        pytest.fail("Fresh stage checks did not use the current checkpoint")
    assert case["proof"].read_bytes() == b"accepted mixed proof"
    assert_recovery(case, response)
    assert_projection(case, response)


def test_requirement_revision_does_not_add_a_new_proof_gate(project):
    case = prepared(project, file_proof=b"accepted proof", revise=True)
    response = launch(case, target="code_review")
    assert response["status"] == "progression_target_reached"
    assert case["log"].read_text().splitlines() == [case["saved"]] * len(case["commits"])
    assert case["proof"].read_bytes() == b"accepted proof"
    assert_recovery(case, response)
    assert_projection(case, response)


def test_role_changes_do_not_require_participants_during_replay(project):
    case = prepared(project, alternating_roles=True)
    response = launch(case, target="code_review")
    assert response["status"] == "progression_target_reached"
    assert response["stage"] == "code_review"
    assert case["log"].read_text().splitlines() == [case["saved"]] * len(case["commits"])
    assert [item["stage"] for item in response["replay"]["passed"]] == [
        "tests", "test_review", "implementation",
    ]
    assert case["client"].runtime.task_queries.record("T1")["claimed_by"] == case["client"].runtime.session
    assert_recovery(case, response)
    assert_projection(case, response)


def test_failed_current_test_stops_before_later_stage(project):
    case = prepared(project)
    case["fail_file"].write_text("test-owned failing historical check\n")
    response = launch(case, target="code_review")
    assert response["status"] == "progression_stopped"
    assert response["stage"] == "tests"
    assert response["replay"]["reason"] == "tests_failed"
    assert response["replay"]["passed"] == []
    assert git(case["root"], "rev-parse", "HEAD") == case["saved"]
    assert_recovery(case, response)
    assert_projection(case, response, stage="tests", reason="tests_failed", count=0,
                      subject=case["saved"])
    assert case["log"].read_text().splitlines() == [case["saved"]]


@pytest.mark.parametrize("failure,reason", [
    ("test", "tests_failed"), ("file_missing", "evidence_missing"),
    ("file_changed", "evidence_changed"),
])
def test_mixed_gate_requires_each_proof_component(project, failure, reason):
    case = prepared(project, file_proof=b"accepted mixed proof")
    if failure == "test":
        case["fail_file"].write_text("fail\n")
    elif failure == "file_missing":
        case["proof"].unlink()
    else:
        case["proof"].write_bytes(b"changed")
    response = launch(case, target="code_review")
    assert response["status"] == "progression_stopped"
    assert_projection(case, response, stage="tests", reason=reason, count=0,
                      subject=case["saved"])
    effects = case["log"].read_text().splitlines()
    assert effects in ([], [case["saved"]])
    if failure == "test":
        assert effects == [case["saved"]]
    assert git(case["root"], "rev-parse", "HEAD") == case["saved"]
