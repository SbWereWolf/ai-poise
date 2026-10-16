"""Observe actual external effects before and after the handler precondition."""

import json
from pathlib import Path

from test_bootstrap_commands import calls, installation, invoke, packet


FIXTURES = Path(__file__).with_name("fixtures")


def test_missing_verification_setting_precedes_mutation(installation):
    installation["tool"]["scenario"] = "repair-success"
    del installation["command"]["handler"]["options"]["verification_command"]
    value = packet(invoke(installation), 4)
    actual_calls = [call["argv"][0] for call in calls(installation)]
    state = Path(installation["tool"]["state"])
    actual_state = state.read_text() if state.exists() else None
    assert (actual_calls, actual_state) == (["run"], None), "PRECHECK-BEFORE-REPAIR"
    assert value["status"] == "error"
    assert "verification_command" in value["reason"]
    assert value["actions"] == []
    assert value["observations"] == json.loads(
        (FIXTURES / "maintenance_missing_verification_observations.json").read_text())


def test_complete_options_preserve_repair_result(installation):
    installation["tool"]["scenario"] = "repair-success"
    value = packet(invoke(installation), 0)
    assert [call["argv"][0] for call in calls(installation)] == ["run", "repair", "verify"]
    assert Path(installation["tool"]["state"]).read_text() == "corrected"
    assert value["status"] == "ready"
    assert value["actions"] == ["repair", "verify"]
    assert value["observations"] == json.loads(
        (FIXTURES / "maintenance_repair_success_observations.json").read_text())
