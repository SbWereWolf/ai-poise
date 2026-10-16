"""005: genuine current setup/update -> exact source receipt readers.

These checks never reclassify modern processes or claim historical migration.
"""
import json

import pytest

from poise.common import PoiseError
from tests.conftest import write_json
from .project_criteria_receipt_helpers import (
    assert_readiness, files, native_receipt, negative_copy, read_receipt,
)


@pytest.mark.parametrize("producer", ["setup", "update"])
@pytest.mark.parametrize("enabled", [False, True], ids=["off", "on"])
def test_native_producer_readiness(project, producer, enabled):
    case = native_receipt(project, producer, enabled)
    assert_readiness(case["result"]["readiness"], enabled, case["commit"])
    assert_readiness(case["document"]["result"]["readiness"], enabled, case["commit"])
    assert case["receipt"].read_bytes() == case["raw"]


@pytest.mark.parametrize("producer", ["setup", "update"])
@pytest.mark.parametrize("enabled", [False, True], ids=["off", "on"])
def test_genuine_native_receipt_reader(project, producer, enabled):
    case = native_receipt(project, producer, enabled)
    assert_readiness(case["document"]["result"]["readiness"], enabled, case["commit"])
    with case["owner"].stage_criteria_lock():
        before = files(case)
        try:
            selected = read_receipt(case, producer)
            assert selected["path"] == str(case["receipt"])
            assert selected["revision"] == case["revision"]
            assert selected["revision_field"] == "revision"
            assert selected["schema"] == (
                "project-receipt-1" if producer == "setup" else "project-config-receipt-1"
            )
        finally:
            assert files(case) == before
            assert case["receipt"].read_bytes() == case["raw"]


BAD_READINESS = [
    None,
    [],
    {"remote": "not_checked"},
    {"repository": "not_checked"},
    {"repository": "not_checked", "remote": "not_checked", "unknown": "value"},
    {"repository": False, "remote": "not_checked"},
    {"repository": "not_checked", "remote": ""},
    {"repository": "verified", "remote": "not_required"},
    {"repository": "verified", "remote": "not_required", "base_revision": False},
    {"repository": "verified", "remote": "not_required", "base_revision": ""},
    {"repository": "not_checked", "remote": "not_checked", "base_revision": "extra"},
]


@pytest.mark.parametrize("producer", ["setup", "update"])
@pytest.mark.parametrize("readiness", BAD_READINESS)
def test_invalid_readiness_refuses_without_effects(project, producer, readiness):
    case = native_receipt(project, producer, False)
    original = negative_copy(
        case, lambda d: d["result"].__setitem__("readiness", readiness),
    )
    with case["owner"].stage_criteria_lock():
        before = files(case)
        try:
            with pytest.raises(PoiseError, match="receipt"):
                read_receipt(case, producer)
        finally:
            assert files(case) == before
            assert original.read_bytes() == case["raw"]


@pytest.mark.parametrize("producer", ["setup", "update"])
@pytest.mark.parametrize("field", ["schema", "config_path", "project"])
def test_foreign_receipt_refuses_as_native_source(project, producer, field):
    case = native_receipt(project, producer, False)
    if producer == "update" and field == "project":
        # Update result has no project field: an added field is a schema error.
        change = lambda d: d["result"].__setitem__("project", "foreign")
    elif field == "schema":
        change = lambda d: d.__setitem__("schema", "manual-installed-storage-layout-correction")
    else:
        change = lambda d: d["result"].__setitem__(field, "foreign")
    negative_copy(case, change)
    with case["owner"].stage_criteria_lock():
        before = files(case)
        try:
            with pytest.raises(PoiseError):
                read_receipt(case, producer)
        finally:
            assert files(case) == before


@pytest.mark.parametrize("producer", ["setup", "update"])
@pytest.mark.parametrize("semantic_change", [False, True], ids=["whitespace", "identity"])
def test_selected_source_canonical_identity(project, producer, semantic_change):
    case = native_receipt(project, producer, False)
    with case["owner"].stage_criteria_lock():
        selected = read_receipt(case, producer)
        publication = {
            "source": selected, "config_path": str(case["config"]),
            "revision": case["revision"],
        }
        document = json.loads(case["raw"])
        if semantic_change:
            document["request_id"] = "different-native-request"
            write_json(case["receipt"], document)
        else:
            case["receipt"].write_text(json.dumps(document, indent=4) + "\n")
        before = files(case)
        try:
            if semantic_change:
                with pytest.raises(PoiseError, match="source receipt changed"):
                    case["owner"].require_stage_criteria_source(publication)
            else:
                case["owner"].require_stage_criteria_source(publication)
        finally:
            assert files(case) == before
