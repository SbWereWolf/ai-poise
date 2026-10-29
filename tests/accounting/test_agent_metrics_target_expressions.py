"""Independent pinned donor literals exercise the real purpose producer."""
import json
from copy import deepcopy
import shlex

import pytest

from tests.accounting.agent_metrics_support import literal, replace_tokens
from tests.accounting.test_agent_metrics_updated_frames import normalized_frame


def normalized_tools(tmp_path, tools, *, path_policy=None):
    """Publish literal calls/UI/output to the real source-normalization producer."""
    template = literal("updated-purpose-producer-events.json")
    events = template[:2]
    identities = []
    for index, tool in enumerate(tools):
        call = deepcopy(template[2])
        call_id = "c" + str(index + 1)
        identities.append(call_id)
        call["payload"].update(id="ctc_" + call_id, call_id=call_id, name=tool["name"],
                               input=tool["arguments"], namespace=tool.get("namespace"))
        events.append(call)
    events.append(template[3])
    for call_id, tool in zip(identities, tools):
        for observed in tool.get("observed_actions", []):
            assert observed["kind"] == "command"
            execution = literal("updated-invocation-frame-cases.json")[0]["events"][12]
            execution["payload"]["item"].update(id="exec-" + call_id,
                command=observed["command"], cwd=observed["cwd"], aggregated_output=observed["output"])
            events.append(execution)
        output = deepcopy(template[4])
        output["payload"]["call_id"] = call_id
        if "paired_results" in tool:
            assert len(tool["paired_results"]) == 1
            output["payload"]["output"] = tool["paired_results"][0]["output"]
        events.append(output)
    for ordinal, event in enumerate(events):
        event["ordinal"] = ordinal
    frame = {"events": events}
    if path_policy is not None:
        frame["path_policy"] = path_policy
    result, _ = normalized_frame(tmp_path, frame)
    responses = [row["fields"] for row in result["metadata"]
                 if row["family"] == "invocation-provenance" and row["fields"]["response_id"] == "r1"]
    assert len(responses) == 1
    return responses[0]


@pytest.mark.parametrize("case", literal("updated-expression-static-cases.json"), ids=lambda row: row["id"])
def test_expression_target_contracts(tmp_path, case):
    cwd = tmp_path / case.get("cwd", ".")
    if case["language"] == "javascript":
        tool = {"name": "exec", "arguments": case["source"], "cwd": str(cwd)}
    else:
        command = case["source"]
        if case["language"] == "python":
            command = "python3 -c " + shlex.quote(command)
        tool = {"name": "exec_command", "arguments": json.dumps({"cmd": command,
                "workdir": str(cwd)}), "cwd": str(cwd)}
    if case.get("paired_compile_failure"):
        tool["paired_results"] = literal("updated-paired-compile-failure.json")
    # Input is inert source text. No journal command, Python or JS is executed.
    response = normalized_tools(tmp_path, [tool], path_policy=case.get("path_policy"))
    if response["classification"] != case["expected"]:
        raise AssertionError("F16_UPDATED_EXPRESSION_PURPOSE:" + case["id"] + ":expected="
                             + case["expected"] + ":actual=" + response["classification"])
    if "expected_target" in case:
        target = case["expected_target"].replace("@CHECKOUT@", str(tmp_path))
        try:
            decoded = json.loads(response["classification_reason"])
        except json.JSONDecodeError:
            decoded = response["classification_reason"]
        evidence = decoded.get("evidence", []) if isinstance(decoded, dict) else [decoded]
        if not any("target: " + target in item for item in evidence):
            raise AssertionError("F16_ACTUAL_TARGET_PROVENANCE_MISSING:" + case["id"])


@pytest.mark.parametrize("case", literal("updated-normalized-tool-cases.json"), ids=lambda row: row["id"])
def test_actual_tool_namespace_and_owned_execution_evidence(tmp_path, case):
    response = normalized_tools(tmp_path, case["tools"])
    if response["classification"] != case["expected"]:
        raise AssertionError("F16_UPDATED_TOOL_TARGET_PURPOSE:" + case["id"] + ":expected="
                             + case["expected"] + ":actual=" + response["classification"])


def test_journal_program_is_not_executed_during_target_classification(tmp_path):
    case = literal("updated-source-no-execution.json")
    source = replace_tokens(case["source"], {"@CHECKOUT@": tmp_path})
    (tmp_path / "src").mkdir()
    poison = tmp_path / "src/never-execute.py"
    tool = {"name": "exec_command", "arguments": json.dumps({
        "cmd": "python3 -c " + shlex.quote(source), "workdir": str(tmp_path)})}
    response = normalized_tools(tmp_path, [tool])
    assert not poison.exists(), "F16_CLASSIFIER_EXECUTED_JOURNAL_PROGRAM"
    if response["classification"] != "main":
        raise AssertionError("F16_SOURCE_WRITE_TARGET_NOT_RECOGNIZED")
