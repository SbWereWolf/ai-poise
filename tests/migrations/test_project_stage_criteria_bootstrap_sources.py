"""INTEGRATION-RECEIPT-001: native SOURCE and strict COMPLETION are distinct.

Integration subjects: actual project publication, its source/recheck/completion
owner and historical migration. Expected documents originate in test literals.
"""
from copy import deepcopy
import json

import pytest

from poise.common import PoiseError
from poise.composition import project_config_tools
from tests.conftest import write_json
from tests.projects.test_update import workflow_change, read_workflow, update_request
from .project_criteria_bootstrap_helpers import (
    DATA, historical_bootstrap, literal_receipt, native_bootstrap, source_publication,
)
from .project_criteria_helpers import fresh_work_read, observe, project_receipts, run_migration
from .project_criteria_receipt_helpers import files, native_receipt, negative_copy
from .stage_criteria_helpers import identity
from .test_project_stage_criteria_migration import assert_preserved, assert_success, interrupt_first_workflow


def selected(case, revision=None):
    return case["owner"].stage_criteria_source(
        case["config"], case["revision"] if revision is None else revision)


def test_current_native_bootstrap_is_stage_criteria_source(project):
    case = native_bootstrap(project)
    before = files(case)
    source = selected(case)
    assert source == {"path": str(case["receipt"]), "digest": identity(json.loads(case["raw"])),
                      "revision": case["revision"], "revision_field": "revision",
                      "schema": "project-config-receipt-1"}
    case["owner"].require_stage_criteria_source(source_publication(case, source))
    assert files(case) == before and case["receipt"].read_bytes() == case["raw"]


def test_bootstrap_prior_revision_is_native_source(project):
    case = native_bootstrap(project, prior_update=True)
    assert case["prior"] != case["setup_revision"]
    before = files(case)
    source = selected(case, case["prior"])
    assert source["path"] == str(case["receipt"])
    assert source["revision"] == case["prior"]
    assert source["revision_field"] == "prior_revision"
    case["owner"].require_stage_criteria_source(
        {**source_publication(case, source), "revision": case["prior"]})
    assert files(case) == before


def test_native_bootstrap_preserves_valid_raw_text(project):
    raw_id = "  native bootstrap identity  "
    case = native_bootstrap(project, request_id=raw_id)
    source = selected(case)
    assert json.loads(case["raw"])["request_id"] == raw_id
    assert source["digest"] == identity(json.loads(case["raw"]))
    case["owner"].require_stage_criteria_source(source_publication(case, source))


def test_bootstrap_replay_preserves_original_source(project):
    case = native_bootstrap(project)
    replay = project_config_tools(case["settings"]).apply(case["request"])
    assert replay["replayed"] is True
    assert case["receipt"].read_bytes() == case["raw"]
    source = selected(case)
    assert source["digest"] == identity(json.loads(case["raw"]))


def test_native_durable_replayed_true_is_source(project, monkeypatch):
    case = native_bootstrap(project, durable_retry=True, monkeypatch=monkeypatch)
    assert json.loads(case["raw"])["result"]["replayed"] is True
    before = files(case)
    source = selected(case)
    assert source == {"path": str(case["receipt"]), "digest": identity(json.loads(case["raw"])),
                      "revision": case["revision"], "revision_field": "revision",
                      "schema": "project-config-receipt-1"}
    case["owner"].require_stage_criteria_source(source_publication(case, source))
    assert files(case) == before
    assert project_config_tools(case["settings"]).apply(case["request"])["replayed"] is True
    assert case["receipt"].read_bytes() == case["raw"]


def test_valid_bootstrap_history_does_not_block_later_native_source(project):
    case = native_bootstrap(project)
    request = update_request(case["config"], case["revision"], workflow_updates=[
        workflow_change(read_workflow(case["config"]), "Later actual native publication.")])
    request["receipt_path"] = "operations/20-after-bootstrap.json"
    done = project_config_tools(case["settings"]).apply(request)
    before = files(case)
    source = selected(case, done["revision"])
    assert source["path"] == str(case["settings"].parent / request["receipt_path"])
    assert source["revision_field"] == "revision"
    assert files(case) == before and case["receipt"].read_bytes() == case["raw"]


def test_historical_bootstrap_fixture_has_coherent_independent_identity(project):
    case = historical_bootstrap(project)
    document = json.loads(case["raw"])
    assert document["request_digest"] == identity(case["bootstrap_request"])
    assert document["result"]["prior_revision"] == case["prior"]
    assert document["result"]["revision"] == case["source_revision"]
    # Binding validates historical setup provenance without source admission.
    with case["owner"].stage_criteria_lock():
        case["owner"].stage_criteria_binding(case["config"])


def test_historical_bootstrap_source_migrates_and_replays(project):
    case = historical_bootstrap(project)
    assert selected(case)["path"] == str(case["receipt"])
    before = observe(case)
    code, result, err = run_migration(case, fresh=True)
    assert_success(case, code, result, err)
    assert_preserved(before, observe(case))
    assert len([(p, v) for p, v in project_receipts(case) if p != case["receipt"]]) == 1
    assert case["receipt"].read_bytes() == case["raw"]
    after = observe(case)
    code, replay, err = run_migration(case, fresh=True)
    assert_success(case, code, replay, err)
    assert replay == {**result, "replayed": True}
    assert observe(case) == after
    read = fresh_work_read(case)
    assert read.returncode == 0, read.stderr


@pytest.mark.parametrize("fault", DATA["faults"], ids=lambda x: x["path"] + ("-missing" if x.get("delete") else "-" + repr(x.get("value"))))
def test_malformed_bootstrap_source_refused_without_effects(project, fault):
    case = native_bootstrap(project)
    def change(document):
        fields = fault["path"].split(".")
        parent = document
        for field in fields[:-1]:
            parent = parent[field]
        if fault.get("delete"):
            del parent[fields[-1]]
        else:
            parent[fields[-1]] = deepcopy(fault["value"])
    original = negative_copy(case, change)
    before = files(case)
    with pytest.raises(PoiseError):
        selected(case)
    assert files(case) == before and original.read_bytes() == case["raw"]


@pytest.mark.parametrize("field", ["prior_revision", "before_revision", "revision", "after_revision"])
def test_bootstrap_revision_pair_mismatch_refused(project, field):
    case = native_bootstrap(project)
    negative_copy(case, lambda d: d["result"].__setitem__(field, "f" * 64))
    before = files(case)
    with pytest.raises(PoiseError):
        selected(case)
    assert files(case) == before


@pytest.mark.parametrize("fault", ["foreign-config", "manual-schema", "unknown-revision", "equal-revisions"])
def test_foreign_bootstrap_cannot_prove_selected_source(project, fault):
    case = native_bootstrap(project)
    target = case["revision"]
    def change(d):
        if fault == "foreign-config":
            d["result"]["config_path"] = str(case["config"].with_name("foreign.json"))
        elif fault == "manual-schema":
            d["schema"] = "manual-installed-storage-layout-correction"
        elif fault == "equal-revisions":
            d["result"]["before_revision"] = d["result"]["prior_revision"] = target
    if fault == "unknown-revision":
        target = "f" * 64
    else:
        negative_copy(case, change)
    before = files(case)
    with pytest.raises(PoiseError):
        selected(case, target)
    assert files(case) == before


@pytest.mark.parametrize("semantic", [False, True], ids=["whitespace", "identity"])
def test_bootstrap_source_drift_is_rejected_by_intent(project, semantic):
    case = native_bootstrap(project)
    source = selected(case)
    publication = source_publication(case, source)
    document = json.loads(case["raw"])
    if semantic:
        document["request_id"] = document["result"]["request_id"] = "changed-native-id"
    case["receipt"].write_text(json.dumps(document, indent=4) + "\n")
    before = files(case)
    if semantic:
        with pytest.raises(PoiseError, match="source receipt changed"):
            case["owner"].require_stage_criteria_source(publication)
    else:
        case["owner"].require_stage_criteria_source(publication)
    assert files(case) == before


def test_bootstrap_source_never_satisfies_migration_completion(project):
    case = native_receipt(project, "update", False)
    owner = case["owner"]
    snapshot = owner.stage_criteria_snapshot(case["config"])
    source = selected(case)  # Existing ordinary source: independent GREEN guard.
    request = update_request(case["config"], snapshot["revision"], request_id="wrong-kind-completion")
    request["receipt_path"] = "operations/negative-completion.json"
    receipt = owner.settings.root / request["receipt_path"]
    wrong = literal_receipt(case["config"], snapshot["revision"], "f" * 64, request)
    write_json(receipt, wrong)
    publication = {**snapshot, **source_publication(case, source),
                   "candidate": deepcopy(snapshot["workflows"]), "request": request}
    before = files(case)
    with pytest.raises(PoiseError) as failure:
        owner.completed_stage_criteria(publication)
    assert str(failure.value) == f"Invalid project migration receipt: {receipt}"
    assert files(case) == before and case["receipt"].read_bytes() == case["raw"]


def test_historical_pending_bootstrap_source_drift_preserves_intent(project, monkeypatch):
    case = historical_bootstrap(project)
    assert selected(case)["path"] == str(case["receipt"])
    interrupt_first_workflow(case, monkeypatch)
    document = json.loads(case["raw"])
    document["request_id"] = document["result"]["request_id"] = "changed-after-intent"
    write_json(case["receipt"], document)
    altered = observe(case)
    code, result, err = run_migration(case, fresh=True)
    assert code == 2 and result["status"] == "rejected", (result, err)
    assert "source" in result["reason"].lower() and "receipt" in result["reason"].lower()
    assert observe(case) == altered and [(p, v) for p, v in project_receipts(case) if p != case["receipt"]] == []


def recheck_negative(case, change, *, revision_field="revision", envelope_schema_missing=False):
    """Bind digest to the negative document, so drift cannot mask validation.

Choose a retained valid revision field: invalid pair values never provide the
lookup identity. Recheck this exact source instead of finding another receipt.
"""
    original = negative_copy(case, change)
    document = json.loads(case["receipt"].read_text())
    value = document["result"][revision_field] if isinstance(document.get("result"), dict) else case["revision"]
    source = {"path": str(case["receipt"]), "digest": identity(document),
              "schema": "project-config-receipt-1", "revision": value,
              "revision_field": revision_field}
    publication = {**source_publication(case, source), "revision": value}
    before = files(case)
    with pytest.raises(PoiseError) as failure:
        case["owner"].require_stage_criteria_source(publication)
    if envelope_schema_missing:
        assert str(failure.value) == "Migration source receipt changed"
    else:
        assert str(failure.value).startswith("Invalid project ") and "receipt" in str(failure.value)
    assert files(case) == before and original.read_bytes() == case["raw"]


@pytest.mark.parametrize("value", DATA["invalid_equal_ids"], ids=repr)
def test_equal_invalid_request_ids_are_rejected(project, value):
    case = native_bootstrap(project)
    def change(document):
        document["request_id"] = document["result"]["request_id"] = value
    recheck_negative(case, change)


@pytest.mark.parametrize("pair", ["before", "after"])
@pytest.mark.parametrize("value", DATA["invalid_coherent_revisions"], ids=repr)
def test_coherent_invalid_revision_pair_is_rejected(project, pair, value):
    case = native_bootstrap(project)
    fields = ("before_revision", "prior_revision") if pair == "before" else ("after_revision", "revision")
    def change(document):
        for field in fields:
            document["result"][field] = value
    recheck_negative(case, change, revision_field="revision" if pair == "before" else "prior_revision")


@pytest.mark.parametrize("value", DATA["invalid_full_length_digests"], ids=lambda v: v[0])
def test_full_length_invalid_request_digest_is_rejected(project, value):
    case = native_bootstrap(project)
    assert len(value) == 64
    recheck_negative(case, lambda document: document.__setitem__("request_digest", value))


@pytest.mark.parametrize("field", DATA["required_envelope_keys"])
def test_every_required_envelope_key_is_required(project, field):
    case = native_bootstrap(project)
    recheck_negative(case, lambda document: document.pop(field), envelope_schema_missing=field == "schema")


@pytest.mark.parametrize("field", DATA["required_result_keys"])
def test_every_required_result_key_is_required(project, field):
    case = native_bootstrap(project)
    def change(document):
        del document["result"][field]
    recheck_negative(case, change, revision_field="prior_revision" if field == "revision" else "revision")
