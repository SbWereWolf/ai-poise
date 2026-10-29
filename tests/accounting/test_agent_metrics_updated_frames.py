"""Literal source frames and independent response/link/output inventory oracles."""
from copy import deepcopy
import hashlib
import json

import pytest

from tests.accounting.agent_metrics_support import literal, replace_tokens


def normalized_frame(tmp_path, case):
    from poise.infrastructure.agent_metrics.sources import SourceRecord
    from poise.modules.accounting.metrics.normalization import response_observations

    tokens = {"@CHECKOUT_URI@": tmp_path.as_uri(), "@CHECKOUT@": tmp_path}
    events = replace_tokens(deepcopy(case["events"]), tokens)
    request = replace_tokens(literal("updated-normalization-request.json"), tokens)
    if "path_policy" in case:
        request["coverage"]["path_policy"] = replace_tokens(deepcopy(case["path_policy"]), tokens)
    records, offset, raw_lines = [], 0, []
    for event in events:
        raw = json.dumps(event, separators=(",", ":"), ensure_ascii=False).encode()
        records.append(SourceRecord(offset, hashlib.sha256(raw).hexdigest(), event, len(raw)))
        raw_lines.append(raw + b"\n")
        offset += len(raw) + 1
    (tmp_path / "normalization-source.jsonl").write_bytes(b"".join(raw_lines))
    (tmp_path / "normalization-request.json").write_text(json.dumps(request, ensure_ascii=False, indent=2))
    result = response_observations(records, request["scope"], request["source"],
                                   request["coverage"], request["processed_at"])
    (tmp_path / "normalization-result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
    return result, records


@pytest.mark.parametrize("case", literal("updated-invocation-frame-cases.json"), ids=lambda row: row["id"])
def test_updated_invocation_frame_decisions(tmp_path, case):
    result, _ = normalized_frame(tmp_path, case)
    usages = [row for row in result["facts"] if row["family"] == "usage-current"
              and row["name"] == "total_tokens" and row["availability"]["state"] == "known"]
    actual_total = sum(row["value"] for row in usages)
    if actual_total != case["expected_known_total"]:
        raise AssertionError("F16_UPDATED_FRAME_COST_IDENTITY:" + case["id"] + ":expected="
                             + str(case["expected_known_total"]) + ":actual=" + str(actual_total))
    if "expected_unknown_usage" in case:
        unknown = sum(row["fields"]["usage_status"] != "known" for row in result["metadata"]
                      if row["family"] == "invocation-provenance")
        if unknown != case["expected_unknown_usage"]:
            raise AssertionError("F16_UPDATED_INVENTORY_UNKNOWN_USAGE:" + case["id"])
    selected_response = case.get("selected_response_id", "r1")
    responses = [row["fields"] for row in result["metadata"]
                 if row["family"] == "invocation-provenance" and row["fields"]["response_id"] == selected_response]
    if not case["expected_owned_response"]:
        if responses:
            raise AssertionError("F16_FOREIGN_RESPONSE_ENTERED_OWN_SESSION:" + case["id"])
        return
    assert len(responses) == 1, ("F16_OWN_RESPONSE_IDENTITY_LOST", case["id"], responses)
    if responses[0]["classification"] != case["expected_classification"]:
        raise AssertionError("F16_UPDATED_FRAME_PURPOSE:" + case["id"] + ":expected="
                             + case["expected_classification"] + ":actual=" + responses[0]["classification"])
    if case.get("expected_target") is not None:
        assert case["expected_target"].replace("@CHECKOUT@", str(tmp_path)) in responses[0]["classification_reason"]
    if "expected_response_purposes" in case:
        assert {row["fields"]["response_id"]: row["fields"]["classification"]
                for row in result["metadata"] if row["family"] == "invocation-provenance"} == case["expected_response_purposes"]


@pytest.mark.parametrize("case", literal("updated-invocation-frame-cases.json"), ids=lambda row: row["id"])
def test_updated_invocation_retains_normalized_link_inventory_evidence(tmp_path, case):
    result, records = normalized_frame(tmp_path, case)
    selected_response = case.get("selected_response_id", "r1")
    responses = [row["fields"] for row in result["metadata"]
                 if row["family"] == "invocation-provenance" and row["fields"]["response_id"] == selected_response]
    if not case["expected_owned_response"]:
        if responses:
            raise AssertionError("F16_FOREIGN_RESPONSE_ENTERED_OWN_SESSION:" + case["id"])
        return
    assert len(responses) == 1
    response = responses[0]
    # Proposed normalized metadata fields; exact contract is handed over before code.
    if "linked_calls" not in response:
        raise AssertionError("F16_NORMALIZED_LINK_EVIDENCE_MISSING:" + case["id"])
    assert [row["call_id"] for row in response["linked_calls"]] == case["expected_linked_call_ids"]
    for linked in response["linked_calls"]:
        assert linked["namespace"] == case.get("expected_namespace")
        assert linked["response_id"] == selected_response
        assert case["expected_link_reason"] in linked["link_reason"]
        actual = next(record for record in records
                      if record.event["payload"].get("call_id") == linked["call_id"]
                      and record.event["payload"].get("type") in ("custom_tool_call", "function_call"))
        assert linked["source_offset"] == actual.offset
        assert linked["event_sha256"] == actual.event_hash
        assert "arguments" not in linked, "F16_RAW_ARGUMENT_LEDGER_IN_NORMALIZED_EVIDENCE"
        assert "input" not in linked
    if case.get("expect_output_hash_in_evidence"):
        assert records[-1].event_hash in json.dumps(response)
    if "expected_inventory" in case:
        if "output_inventory" not in response:
            raise AssertionError("F16_NORMALIZED_OUTPUT_INVENTORY_MISSING:" + case["id"])
        inventory = response["output_inventory"]
        assert bool(inventory) is case["expected_inventory"]
        if inventory:
            assert inventory[0]["version"] == case["expected_inventory_version"]
            assert inventory[0]["generated_items"] == case["expected_generated_items"]
            assert inventory[0]["boundary_kind"] == case["expected_inventory_kind"]
            assert inventory[0]["previous_response_id"] == (
                None if case["expected_inventory_kind"] == "turn_start" else "r-previous")
            if "expected_inventory_provenance_count" in case:
                assert len(inventory[0]["provenance"]) == case["expected_inventory_provenance_count"]
            original = {record.offset: record.event_hash for record in records}
            for reference in inventory[0]["provenance"]:
                assert reference["source_path"] == str(tmp_path / "fixture-session.jsonl")
                assert original[reference["source_offset"]] == reference["event_sha256"]


def test_updated_response_versions_describe_actual_parser_and_classifier(tmp_path):
    case = literal("updated-invocation-frame-cases.json")[1]
    result, _ = normalized_frame(tmp_path, case)
    response = next(row["fields"] for row in result["metadata"]
                    if row["family"] == "invocation-provenance")
    assert response["parser_version"] == "invocations-2.5", "F16_UPDATED_PARSER_PROVENANCE"
    assert response["classification_version"] == "purpose-14", "F16_UPDATED_CLASSIFIER_PROVENANCE"
