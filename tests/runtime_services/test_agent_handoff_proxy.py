"""Integration subject: the public handoff-proxy CLI and its external boundaries.

Literal contracts and transparent stdio peers provide independent expectations.
No production proxy helpers, constants or serializers generate test oracles.
"""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from runtime_services.stage_result_handoff_helpers import start, source_state, count


FIXTURES = Path(__file__).parent / "fixtures" / "agent_handoff_proxy"
CHECKOUT = Path(__file__).resolve().parents[2]


def read_fixture(name):
    return json.loads((FIXTURES / name).read_text())


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2))


@pytest.fixture
def proxy(tmp_path):
    root = tmp_path / "peers"
    root.mkdir()
    packet = read_fixture("deliver.json")
    scenario = {"handoff": read_fixture("handoff-completed.json")}
    settings = {
        "schema": "poise-agent-handoff-proxy-settings-1", "project": "fixture-project",
        "launcher": [sys.executable, str(FIXTURES / "peer.py"), "handoff", str(root)],
        "notification": {"command": [sys.executable, str(FIXTURES / "peer.py"), "mcp", str(root)]},
        "state_root": str(tmp_path / "proxy-state"),
    }
    environment = dict(os.environ)
    # Test-owned signals apply only to isolated peers and temporary test storage.
    environment.update(CODEX_THREAD_ID="fixture-native", CODEX_SESSION_ID="fixture-native")
    environment.pop("POISE_CALLER_BINDING", None)
    environment["PYTHONPATH"] = os.pathsep.join([str(CHECKOUT / "src"), str(CHECKOUT / "tests"), str(CHECKOUT)])
    return {"root": root, "packet": packet, "scenario": scenario, "settings": settings,
            "settings_path": tmp_path / "settings.json", "environment": environment}


def invoke(proxy, packet=None, *, raw=None, expected_exit=None, prepare=True):
    if prepare:
        write_json(proxy["settings_path"], proxy["settings"])
        write_json(proxy["root"] / "scenario.json", proxy["scenario"])
    process = subprocess.run(
        [sys.executable, "-B", "-m", "poise", "handoff-proxy", "--settings", str(proxy["settings_path"])],
        input=raw if raw is not None else json.dumps(proxy["packet"] if packet is None else packet),
        text=True, capture_output=True, cwd=CHECKOUT, env=proxy["environment"], timeout=30,
    )
    if "invalid choice: 'handoff-proxy'" in process.stderr:
        pytest.fail("Missing public handoff-proxy command", pytrace=False)
    if expected_exit is not None:
        assert process.returncode == expected_exit, (process.stdout, process.stderr)
    assert process.stdout.strip(), (process.returncode, process.stderr)
    result = json.loads(process.stdout)
    assert set(result) == {"schema", "status", "phase", "request_id", "task", "handoff",
                           "notification", "error", "replayed", "response_path"}
    assert result["schema"] == "poise-agent-handoff-proxy-result-1"
    return process, result


def events(proxy, kind):
    path = proxy["root"] / "trace.jsonl"
    rows = [] if not path.exists() else [json.loads(line) for line in path.read_text().splitlines()]
    return [row for row in rows if row["event"] == kind]


def assert_delivered(proxy, result):
    assert (result["status"], result["phase"], result["error"]) == ("delivered", "complete", None)
    assert result["request_id"] == "proxy-request-1" and result["task"] == "T1"
    assert result["handoff"]["result"] == proxy["scenario"]["handoff"]
    assert result["handoff"]["response_path"] == str(proxy["root"] / "native-response.json")
    assert result["notification"]["status"] == "confirmed"
    saved = json.loads(Path(result["response_path"]).read_text())
    assert saved["handoff"] == result["handoff"]
    assert saved["notification"] == result["notification"]


def test_one_cli_call_preserves_payload_reads_full_receipt_and_sends_matching_message(proxy):
    proxy["scenario"]["handoff_delay"] = 0.2
    _, result = invoke(proxy, expected_exit=0)
    assert_delivered(proxy, result)
    assert result["replayed"] is False
    assert len(events(proxy, "handoff")) == len(events(proxy, "send")) == 1
    native = events(proxy, "handoff")[0]
    assert native["packet"] == proxy["packet"]["input"]["handoff"]
    assert native["arguments"] == ["--project", "fixture-project"]
    sent = events(proxy, "send")[0]["params"]
    assert sent["arguments"]["threadId"] == "fixture-receiver"
    assert sent["arguments"]["hostId"] == "local"
    assert sent["_meta"]["codexThreadId"] == "fixture-native"
    assert "turn_id" not in sent["_meta"]
    prompt = sent["arguments"]["prompt"]
    assert prompt.startswith(proxy["packet"]["input"]["message"] + "\n")
    context = json.loads(prompt.split("Контекст передачи:\n", 1)[1])
    expected = read_fixture("message-context.json")
    expected["response_path"] = str(proxy["root"] / "native-response.json")
    assert context == expected
    assert result["notification"]["response"] == {
        "isError": False, "content": [{"type": "text", "text": '{"threadId": "fixture-receiver"}'}]}


@pytest.mark.parametrize("continuation", ["role", "same_role", "acceptance"])
def test_notification_uses_actual_progression_and_preserves_separate_acceptance_gate(proxy, continuation):
    variants = read_fixture("progressions.json")
    proxy["scenario"]["handoff"]["progression"] = variants[continuation]
    _, result = invoke(proxy, expected_exit=0)
    assert_delivered(proxy, result)
    prompt = events(proxy, "send")[0]["params"]["arguments"]["prompt"]
    context = json.loads(prompt.split("Контекст передачи:\n", 1)[1])
    assert context["progression"] == variants[continuation]
    assert len(events(proxy, "handoff")) == 1


@pytest.mark.parametrize("case", ["missing_recipient", "empty_recipient", "wrong_result_type",
                                  "missing_message", "unknown_field", "malformed_json"])
def test_invalid_initial_packet_is_rejected_before_handoff_and_send(proxy, case):
    packet = deepcopy(proxy["packet"])
    if case == "missing_recipient":
        del packet["input"]["recipient"]
    elif case == "empty_recipient":
        packet["input"]["recipient"]["thread_id"] = ""
    elif case == "wrong_result_type":
        packet["input"]["handoff"]["input"]["result"] = []
    elif case == "missing_message":
        del packet["input"]["message"]
    elif case == "unknown_field":
        packet["input"]["caller"] = "foreign"
    _, result = invoke(proxy, packet, raw="{invalid" if case == "malformed_json" else None, expected_exit=2)
    assert (result["status"], result["phase"], result["error"]["code"]) == ("blocked", "admission", "invalid_input")
    assert result["error"]["message"] and result["error"]["continuation"]
    assert events(proxy, "handoff") == events(proxy, "send") == []


@pytest.mark.parametrize("case", ["project", "launcher", "transport", "tool_absent"])
def test_invalid_operator_configuration_is_rejected_before_effects(proxy, case):
    if case == "project":
        proxy["settings"]["project"] = ""
    elif case == "launcher":
        proxy["settings"]["launcher"] = [str(proxy["root"] / "absent-launcher")]
    elif case == "transport":
        proxy["settings"]["notification"]["command"] = [str(proxy["root"] / "absent-transport")]
    else:
        proxy["scenario"]["no_send_tool"] = True
    _, result = invoke(proxy, expected_exit=2)
    assert (result["status"], result["phase"], result["error"]["code"]) == ("blocked", "admission", "invalid_settings")
    assert result["error"]["message"] and result["error"]["continuation"]
    assert events(proxy, "handoff") == events(proxy, "send") == []


@pytest.mark.parametrize("case", ["absent", "conflict", "mixed_binding"])
def test_native_identity_is_required_and_conflicting_signals_never_impersonate_caller(proxy, case):
    if case == "absent":
        proxy["environment"].pop("CODEX_THREAD_ID")
        proxy["environment"].pop("CODEX_SESSION_ID")
    elif case == "conflict":
        proxy["environment"]["CODEX_SESSION_ID"] = "other-fixture"
    else:
        proxy["environment"]["POISE_CALLER_BINDING"] = str(proxy["root"] / "foreign-binding.json")
    _, result = invoke(proxy, expected_exit=2)
    assert (result["phase"], result["error"]["code"]) == ("admission", "invalid_identity")
    assert events(proxy, "handoff") == events(proxy, "send") == []


@pytest.mark.parametrize("retained", ["CODEX_THREAD_ID", "CODEX_SESSION_ID"])
def test_one_genuine_native_signal_is_sufficient_without_fabricating_the_other(proxy, retained):
    missing = "CODEX_SESSION_ID" if retained == "CODEX_THREAD_ID" else "CODEX_THREAD_ID"
    proxy["environment"].pop(missing)
    _, result = invoke(proxy, expected_exit=0)
    assert_delivered(proxy, result)
    assert events(proxy, "send")[0]["params"]["_meta"]["codexThreadId"] == "fixture-native"


@pytest.mark.parametrize("case", ["blocked", "wrong_task", "wrong_request", "claimed_task", "claimed_tree",
                                  "unverified", "unreleased", "release_other_task",
                                  "invalid_receipt", "missing_receipt", "exit_error"])
def test_handoff_failure_or_unconfirmed_release_never_notifies(proxy, case):
    receipt = proxy["scenario"]["handoff"]
    expected = read_fixture("handoff-failure-oracles.json")[case]
    if case == "blocked":
        receipt["status"] = "handoff_blocked"
        receipt["phase"] = "verification"
        receipt["verification"]["status"] = "checks_failed"
        receipt["progression"] = None
        receipt["release"] = None
        receipt["blocked"] = {"reason": "Verification failed", "continuation": "Repair original result"}
    elif case == "wrong_task":
        receipt["task"] = "foreign"
    elif case == "wrong_request":
        receipt["request_id"] = "foreign"
    elif case == "unverified":
        receipt["verification"]["status"] = "checks_failed"
    elif case == "unreleased":
        receipt["release"]["status"] = "release_blocked"
    elif case == "release_other_task":
        receipt["release"]["task"] = "foreign"
    elif case.startswith("claimed_"):
        receipt["release"]["after"]["task_id" if case == "claimed_task" else "worktree_task_id"] = "T1"
    else:
        proxy["scenario"]["handoff_exit" if case == "exit_error" else case] = 7 if case == "exit_error" else True
    _, result = invoke(proxy, expected_exit=2)
    assert (result["status"], result["phase"], result["error"]["code"]) == (
        expected["status"], "handoff", expected["code"])
    assert result["handoff"]["result"] == (None if expected["receipt"] == "unavailable" else receipt)
    assert result["handoff"]["process"]["exit_code"] == expected["exit_code"]
    assert result["notification"] is None
    assert result["error"]["message"]
    if case == "blocked":
        assert result["error"]["continuation"] == "Repair original result"
    else:
        assert all(term in result["error"]["continuation"] for term in expected["continuation_terms"])
    assert len(events(proxy, "handoff")) == 1 and events(proxy, "send") == []


def test_complete_native_output_is_preserved_without_internal_clipping(proxy):
    stderr = (FIXTURES / "long-stderr.txt").read_text()
    stdout = (FIXTURES / "long-stdout.txt").read_text()
    proxy["scenario"]["native_stderr"] = stderr.rstrip("\n")
    proxy["scenario"]["native_stdout"] = stdout
    proxy["scenario"]["handoff"]["verification"]["detail"] = (FIXTURES / "long-detail.txt").read_text()
    _, result = invoke(proxy, expected_exit=0)
    assert_delivered(proxy, result)
    assert result["handoff"]["process"]["stderr"] == stderr
    assert json.loads(result["handoff"]["process"]["stdout"])["diagnostic"] == stdout
    assert json.loads(result["handoff"]["process"]["stdout"])["response_path"] == str(proxy["root"] / "native-response.json")


def test_lost_native_ack_recovers_original_request_without_new_business_effects(proxy):
    proxy["scenario"]["lose_handoff_ack"] = True
    _, first = invoke(proxy, expected_exit=2)
    assert first["status"] == "unknown" and first["phase"] == "handoff"
    assert events(proxy, "send") == []
    _, recovered = invoke(proxy, expected_exit=0)
    assert_delivered(proxy, recovered)
    assert len(events(proxy, "handoff")) == 2
    assert len(events(proxy, "native_effect")) == len(events(proxy, "send")) == 1
    assert events(proxy, "handoff")[0]["packet"] == events(proxy, "handoff")[1]["packet"]


@pytest.mark.parametrize("outcome", ["disconnect", "malformed_json", "rpc_error", "malformed_result",
                                     "wrong_recipient", "missing_recipient", "is_error"])
def test_ambiguous_mcp_ack_remains_unknown_and_replay_never_sends_a_duplicate(proxy, outcome):
    proxy["scenario"]["notification"] = outcome
    _, first = invoke(proxy, expected_exit=2)
    assert (first["status"], first["phase"], first["notification"]["status"]) == ("unknown", "notification", "unknown")
    assert first["error"]["message"] and first["error"]["continuation"]
    _, replay = invoke(proxy, expected_exit=2)
    assert replay["notification"]["status"] == "unknown"
    assert len(events(proxy, "send")) == len(events(proxy, "handoff")) == 1


def test_process_death_during_send_does_not_authorize_automatic_resend(proxy):
    proxy["scenario"]["notification"] = "kill_proxy"
    write_json(proxy["settings_path"], proxy["settings"])
    write_json(proxy["root"] / "scenario.json", proxy["scenario"])
    process = subprocess.run([sys.executable, "-B", "-m", "poise", "handoff-proxy", "--settings",
                              str(proxy["settings_path"])], input=json.dumps(proxy["packet"]), text=True,
                             capture_output=True, env=proxy["environment"], cwd=CHECKOUT, timeout=30)
    if "invalid choice: 'handoff-proxy'" in process.stderr:
        pytest.fail("Missing public handoff-proxy command", pytrace=False)
    assert process.returncode == -9
    proxy["scenario"]["notification"] = "success"
    _, result = invoke(proxy, expected_exit=2)
    assert result["notification"]["status"] == "unknown"
    assert len(events(proxy, "handoff")) == len(events(proxy, "send")) == 1


@pytest.mark.parametrize("decision,send_count", [("delivered", 1), ("not_delivered", 2)])
def test_explicit_unknown_resolution_uses_recorded_evidence_and_only_missing_effect(proxy, decision, send_count):
    proxy["scenario"]["notification"] = "disconnect"
    invoke(proxy, expected_exit=2)
    proxy["scenario"]["notification"] = "success"
    resolution = read_fixture("resolve-recipient-readback.json")
    resolution["input"]["outcome"] = decision
    _, result = invoke(proxy, resolution, expected_exit=0)
    assert result["status"] == "delivered"
    assert result["notification"]["resolution"] == resolution["input"]
    assert len(events(proxy, "handoff")) == 1 and len(events(proxy, "send")) == send_count


@pytest.mark.parametrize("changed", ["recipient", "result", "message"])
def test_completed_replay_is_durable_and_changed_intent_is_rejected(proxy, changed):
    _, first = invoke(proxy, expected_exit=0)
    _, replay = invoke(proxy, expected_exit=0)
    assert_delivered(proxy, replay)
    assert replay["replayed"] is True
    assert replay["handoff"] == first["handoff"]
    packet = deepcopy(proxy["packet"])
    if changed == "recipient":
        packet["input"]["recipient"]["thread_id"] = "other-receiver"
    elif changed == "result":
        packet["input"]["handoff"]["input"]["result"]["stage_work"]["coverage"] = "A different review decision."
    else:
        packet["input"]["message"] = "A different substantive message."
    _, conflict = invoke(proxy, packet, expected_exit=2)
    assert conflict["error"]["code"] == "intent_conflict"
    assert len(events(proxy, "handoff")) == len(events(proxy, "send")) == 1


def test_another_native_caller_cannot_resolve_original_notification(proxy):
    proxy["scenario"]["notification"] = "disconnect"
    invoke(proxy, expected_exit=2)
    proxy["environment"].update(CODEX_THREAD_ID="foreign-fixture", CODEX_SESSION_ID="foreign-fixture")
    resolution = read_fixture("resolve-foreign-assertion.json")
    _, result = invoke(proxy, resolution, expected_exit=2)
    assert result["error"]["code"] == "caller_conflict"
    assert len(events(proxy, "handoff")) == len(events(proxy, "send")) == 1


@pytest.mark.parametrize("case", ["empty_evidence", "invalid_outcome"])
def test_resolution_requires_an_explicit_supported_decision_and_external_evidence(proxy, case):
    proxy["scenario"]["notification"] = "disconnect"
    invoke(proxy, expected_exit=2)
    resolution = read_fixture("resolve-independent-readback.json")
    resolution["input"]["evidence" if case == "empty_evidence" else "outcome"] = "" if case == "empty_evidence" else "retry_anyway"
    _, result = invoke(proxy, resolution, expected_exit=2)
    assert result["error"]["code"] == "invalid_input"
    assert len(events(proxy, "handoff")) == len(events(proxy, "send")) == 1


def test_concurrent_identical_calls_produce_one_notification(proxy):
    proxy["scenario"]["notification"] = "slow_success"
    write_json(proxy["settings_path"], proxy["settings"])
    write_json(proxy["root"] / "scenario.json", proxy["scenario"])
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: invoke(proxy, expected_exit=0, prepare=False)[1], range(2)))
    assert all(result["status"] == "delivered" for result in results)
    assert sorted(result["replayed"] for result in results) == [False, True]
    assert len(events(proxy, "handoff")) == len(events(proxy, "send")) == 1


def test_proxy_composes_real_task_result_route_and_ownership_owners(proxy, project):
    tools, context, packet, counter = start(project)
    before = source_state(tools)
    proxy["environment"].update(CODEX_THREAD_ID="handoff-author", CODEX_SESSION_ID="handoff-author")
    proxy["packet"]["input"]["handoff"] = packet
    proxy["settings"]["project"] = "demo"
    proxy["settings"]["launcher"] = [sys.executable, str(FIXTURES / "native_peer.py"),
                                         str(project["config_path"]), "handoff-author", str(proxy["root"])]
    _, result = invoke(proxy, expected_exit=0)
    assert result["status"] == "delivered"
    assert result["handoff"]["result"]["status"] == "handoff_completed"
    assert result["handoff"]["result"]["release"]["after"] == {
        "actor": "handoff-author", "task_id": None, "worktree_task_id": None}
    state = source_state(tools)
    assert (state["claimed_by"], state["status"], state["stage"]) == (None, "verified", "author")
    assert state["head"] == before["head"] and state["tree"] == before["tree"]
    assert count(counter) == "x"
    assert (Path(context["task_root"]) / "artifacts/result.txt").read_bytes() == b"Independent stage artifact.\n"
    assert len(events(proxy, "send")) == 1
