"""A01 real public dispatcher: order, full input, stop, failure and replay."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import shlex

import pytest

from poise.common import PoiseError
from tests.hook_transport.architecture_support import Chain, FIXTURES, literal, write


def two_handlers(project, tmp_path, marker):
    chain = Chain(project, tmp_path, marker)
    path = chain.configuration("ordered", [chain.command("one"), chain.command("two")])
    chain.install([path])
    return chain


def test_configuration_and_handler_order(project, tmp_path):
    marker = "ARCH_FIX_CHAIN_CONFIGURATION"
    chain = Chain(project, tmp_path, marker)
    unknown = literal("unknown-config-options.json")
    two, one = chain.command("two"), chain.command("one")
    two["options"].update(unknown["nested"]["handler_options"])
    first = chain.configuration("z-first", [two, one])
    second = chain.configuration("a-second", [chain.command("three")], fixture="second-config.json")
    value = json.loads(Path(first).read_text())
    value.update(unknown["root"])
    write(Path(first), value)
    chain.definition.update(unknown["root"])
    chain.install([first, second])
    result = chain.invoke()
    chain.assert_order(["two", "one", "three"])
    assert result == literal("expected.json")["response"], marker
    assert json.loads(Path(first).read_text()) == value, marker
    installed = json.loads(Path(chain.installation["definition_path"]).read_text())
    assert installed["future_operator_note"] == unknown["root"]["future_operator_note"], marker
    assert installed["routes"]["SessionStart"] == [first, second], marker
    previous = chain.service.revision()
    changed = deepcopy(value)
    changed.update(literal("unknown-config-options-changed.json")["root"])
    write(Path(first), changed)
    current = chain.service.revision()
    assert current != previous, marker
    before = chain.service.settings.hooks_file.read_bytes()
    with pytest.raises(PoiseError):
        chain.service.install({"request_id": "unknown-stale", "expected_revision": previous,
                               "definition": deepcopy(chain.definition)})
    assert chain.service.settings.hooks_file.read_bytes() == before, marker
    packet = {"request_id": "unknown-roundtrip", "expected_revision": current,
              "definition": deepcopy(chain.definition)}
    published = chain.service.install(packet)
    assert json.loads(Path(first).read_text()) == changed, marker
    assert chain.service.install(packet)["revision"] == published["revision"], marker


def test_full_raw_event_is_preserved(project, tmp_path):
    marker = "ARCH_FIX_FULL_EVENT"
    chain = two_handlers(project, tmp_path, marker)
    payload = literal("events.json")
    chain.invoke(payload)
    inputs = chain.assert_order(["one", "two"])
    for value in inputs:
        assert value["event"]["tool_response"] == payload["tool_response"], marker
        assert len(value["event"]["tool_response"]["stdout"]) > 4096, marker
        assert value["event_receipt"]["event"] == "SessionStart", marker


def test_sync_response_flows_to_next_handler(project, tmp_path):
    marker = "ARCH_FIX_SYNC_FLOW"
    chain = two_handlers(project, tmp_path, marker)
    result = chain.invoke()
    inputs = chain.assert_order(["one", "two"])
    assert next(p for p in inputs if p["configuration"]["handler_id"] == "two")[
        "response"] == {"systemMessage": "seed|one"}, marker
    assert result == {"systemMessage": "seed|one|two"}, marker


def test_sync_stop_prevents_later_calls(project, tmp_path):
    marker = "ARCH_FIX_SYNC_STOP"
    chain = Chain(project, tmp_path, marker)
    path = chain.configuration("stop-chain", [chain.command("stop", action="stop"),
        chain.command("later"), chain.command("async-later", mode="async")])
    chain.install([path])
    assert chain.invoke() == {"systemMessage": "seed|stop"}, marker
    chain.assert_order(["stop"])
    _, receipt = chain.delivery()
    assert receipt["response"] == {"systemMessage": "seed|stop"}, marker


def test_sync_error_stops_with_saved_result(project, tmp_path):
    marker = "ARCH_FIX_SYNC_ERROR"
    chain = Chain(project, tmp_path, marker)
    for failure in ("nonzero", "invalid-json"):
        parent = tmp_path / failure
        parent.mkdir()
        chain.parent, chain.effects = parent, parent / "handler-effects"
        chain.session = "architecture-sync-" + failure
        path = chain.configuration("failure-chain-" + failure, [chain.command("one"),
            chain.command("broken", failure=failure), chain.command("later")])
        chain.install([path])
        chain.invoke()
        chain.assert_order(["one", "broken"])
        _, receipt = chain.delivery()
        assert receipt["status"] == "failed", marker
        assert receipt["response"] == {"systemMessage": "seed|one"}, marker
        assert "broken" in json.dumps(receipt, ensure_ascii=False), marker
        # Real capture must retain the actual failure, not only a generic status.
        captures = [p.read_bytes() for p in chain.service.settings.observations.rglob("*")
                    if p.is_file()]
        witness = b"ACTUAL_WORKER_FAILURE" if failure == "nonzero" else b"INVALID_HANDLER_JSON"
        assert any(witness in value for value in captures), marker
        chain.finish()


def test_each_position_invoked_once(project, tmp_path):
    marker = "ARCH_FIX_SINGLE_INVOCATION"
    chain = Chain(project, tmp_path, marker)
    path = chain.configuration("repeated", [chain.command("one")])
    chain.install([path, path], event_name="PostToolUse")
    chain.invoke()
    counter = tmp_path / "user-tool-counter"
    argv = [sys.executable, "-B", str(FIXTURES / "user_tool.py"), str(counter)]
    actual = subprocess.run(argv, check=True, capture_output=True)
    chain.invoke({"tool_name": "Bash", "tool_use_id": "architecture-tool-1", "turn_id": "architecture-turn",
                  "tool_input": {"command": shlex.join(argv)},
                  "tool_response": {"stdout": actual.stdout.decode(),
                                    "stderr": actual.stderr.decode(), "exit_code": actual.returncode}},
                 event_name="PostToolUse")
    inputs = chain.assert_order(["one", "one"])
    assert inputs[0]["invocation_id"] != inputs[1]["invocation_id"], marker
    assert inputs[0]["event_receipt"] == inputs[1]["event_receipt"], marker
    assert counter.read_bytes() == b"executed\n", marker


def test_completed_invocation_replay_does_not_execute(project, tmp_path):
    marker = "ARCH_FIX_REPLAY"
    chain = two_handlers(project, tmp_path, marker)
    first = chain.invoke()
    chain.assert_order(["one", "two"])
    path, receipt = chain.delivery()
    assert receipt["status"] == "completed", marker
    before = {str(p): p.read_bytes() for p in chain.effects.iterdir()}
    assert chain.service.retry_lifecycle(chain.installation["definition_path"], path) == first, marker
    assert {str(p): p.read_bytes() for p in chain.effects.iterdir()} == before, marker
    chain.assert_order(["one", "two"])


def test_invalid_configuration_has_no_effects(project, tmp_path):
    marker = "ARCH_FIX_CONFIG_PREFLIGHT"
    # Admit a healthy chain first. Unsupported schema must never count as denial proof.
    healthy = two_handlers(project, tmp_path, marker)
    healthy.invoke()
    healthy.assert_order(["one", "two"])
    effects_before = {str(p): p.read_bytes() for p in healthy.effects.iterdir()}
    from tests.hook_transport.helpers import event
    malformed = event(session="architecture-fixture")
    malformed.update(cwd=str(healthy.service.settings.root), agent_id="foreign-subagent")
    with pytest.raises(PoiseError):
        healthy.service.event(healthy.installation["definition_path"], malformed)
    assert {str(p): p.read_bytes() for p in healthy.effects.iterdir()} == effects_before, marker
    healthy.finish()
    negative = literal("strict-packet-negative.json")
    for problem in ("unknown-handler", "invalid-mode", "missing-config", "extra-api"):
        parent = tmp_path / problem
        parent.mkdir()
        chain = healthy
        chain.parent, chain.effects = parent, parent / "handler-effects"
        handler = chain.command("never")
        if problem == "unknown-handler":
            handler["implementation"] = {"kind": "builtin", "name": negative["unknown_handler"]}
        if problem == "invalid-mode":
            handler["mode"] = negative["unknown_mode"]
            handler["execution"]["launch_seconds"] = 5
        path = chain.configuration("invalid", [handler])
        if problem == "missing-config":
            Path(path).unlink()
        chain.definition["routes"]["SessionStart"] = [path]
        packet = {"request_id": "invalid-install-" + problem,
                  "expected_revision": chain.service.revision(),
                  "definition": deepcopy(chain.definition)}
        if problem == "extra-api":
            packet["extra_api_member"] = negative["extra_api_member"]
        before = chain.service.settings.hooks_file.read_bytes() if chain.service.settings.hooks_file.exists() else None
        with pytest.raises(PoiseError) as refused:
            chain.service.install(packet)
        reason = str(refused.value).lower()
        assert "revision changed" not in reason, (marker, problem, reason)
        if problem == "unknown-handler":
            assert "unregistered" in reason and ("handler" in reason or "implementation" in reason), (marker, reason)
        elif problem == "invalid-mode":
            assert "mode" in reason and "unsupported" in reason, (marker, reason)
        elif problem == "missing-config":
            assert str(Path(path)).lower() in reason, (marker, reason)
        else:
            assert "extra_api_member" in reason, (marker, reason)
        assert not chain.effects.exists(), marker
        after = chain.service.settings.hooks_file.read_bytes() if chain.service.settings.hooks_file.exists() else None
        assert after == before, marker
