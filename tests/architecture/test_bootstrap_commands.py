"""Acceptance contracts for configured Bootstrap execution and real handlers.

All expectations are test-owned. The public CLI is the only product entrypoint;
the external process and replaceable plugins are independent fixture producers.
"""

import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).with_name("fixtures")


@pytest.fixture
def installation(tmp_path):
    root = tmp_path / "Poise рабочая копия"
    root.mkdir()
    shutil.copy2(ROOT / "bootstrap", root / "bootstrap")
    shutil.copytree(ROOT / "apps/bootstrap", root / "apps/bootstrap")
    shutil.copy2(FIXTURES / "maintenance_handler.py", root / "fixture_handlers.py")
    config_dir = root / "config"
    config_dir.mkdir()
    command = json.loads((FIXTURES / "maintenance_command.example.json").read_text())
    tool = json.loads((FIXTURES / "maintenance_tool.example.json").read_text())
    tool.update(log=str(root / "calls.jsonl"), state=str(root / "state.txt"))
    tool_path = config_dir / "tool working.json"
    config_path = config_dir / "bootstrap working.json"
    command["parameters"].update(python=sys.executable,
                                  script=str(FIXTURES / "maintenance_command.py"),
                                  tool_config=str(tool_path))
    return {"root": root, "command": command, "tool": tool,
            "tool_path": tool_path, "config_path": config_path}


def invoke(installation, operation="infra", *arguments):
    installation["tool_path"].write_text(json.dumps(installation["tool"], ensure_ascii=False))
    installation["config_path"].write_text(json.dumps(installation["command"], ensure_ascii=False))
    return subprocess.run(
        [sys.executable, "-I", "-B", str(installation["root"] / "bootstrap"),
         operation, "--config", str(installation["config_path"]),
         "--catalog-dir", "каталог с пробелами", *arguments],
        cwd=installation["root"], capture_output=True, check=False,
    )


def packet(result, expected_code):
    assert result.returncode == expected_code, (
        f"CONFIGURED-EXECUTION: expected {expected_code}, got {result.returncode}; "
        f"stdout={result.stdout!r}; stderr={result.stderr!r}"
    )
    assert result.stdout.startswith(b"{"), "CONFIGURED-EXECUTION: structured result missing"
    return json.loads(result.stdout)


def calls(installation):
    path = Path(installation["tool"]["log"])
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def actions(installation):
    return [call["argv"][0] for call in calls(installation)]


def test_configured_command_receives_ordered_parameters_and_tool_settings(installation):
    value = packet(invoke(installation, "infra", "--values", "first.json",
                          "--set", "x=Ж", "--values", "second file.json"), 0)
    assert value["status"] == "ready"
    actual = calls(installation)
    assert len(actual) == 1
    expected = json.loads((FIXTURES / "maintenance_argv_expected.json").read_text())
    expected[2] = str(installation["tool_path"])
    assert actual[0]["argv"] == expected
    assert actual[0]["marker"] == "tool-settings-reached"
    assert actual[0]["cwd"] == str(installation["root"])


@pytest.mark.parametrize("operation,external", [("infra", "run"), ("deps", "run"), ("check", "check")])
def test_public_operations_use_selected_templates(installation, operation, external):
    assert packet(invoke(installation, operation), 0)["status"] == "ready"
    assert actions(installation) == [external]


def test_configuration_replaces_external_command_without_source_edit(installation):
    installation["command"]["commands"]["infra"][4] = "chosen-action"
    assert packet(invoke(installation), 0)["status"] == "ready"
    assert actions(installation) == ["chosen-action"]


def test_handler_receives_full_streams_and_real_exit(installation):
    installation["tool"]["scenario"] = "unknown"
    installation["command"]["handler"] = {
        "callable": "fixture_handlers:first", "options": {"marker": "configured-plugin"}}
    value = packet(invoke(installation), 7)
    assert value == json.loads((FIXTURES / "maintenance_first_expected.json").read_text())
    assert actions(installation) == ["run"]


def test_configuration_swaps_handlers_through_same_interface(installation):
    installation["command"]["handler"] = {
        "callable": "fixture_handlers:first", "options": {"marker": "one"}}
    assert packet(invoke(installation), 7)["status"] == "first-handler"
    installation["command"]["handler"] = {
        "callable": "fixture_handlers:second", "options": {"marker": "two"}}
    value = packet(invoke(installation), 0)
    assert value["status"] == "second-handler"
    assert value["marker"] == "two"
    assert actions(installation) == ["run", "run"]


def test_product_handler_retains_complete_large_output(installation):
    installation["tool"]["scenario"] = "large"
    value = packet(invoke(installation), 0)
    assert value["status"] == "ready"
    observation = value["observations"][0]
    payload = json.loads(observation["stdout"])
    assert payload["details"] == (FIXTURES / "maintenance_large_expected.txt").read_text().rstrip("\n")
    assert observation["stderr"] == "external diagnostic: run\n"
    assert observation["exit_code"] == 0


def test_recognized_failure_runs_one_repair_and_verification(installation):
    installation["tool"]["scenario"] = "repair-success"
    value = packet(invoke(installation), 0)
    assert value["status"] == "ready"
    assert actions(installation) == ["run", "repair", "verify"]
    assert [item["stage"] for item in value["observations"]] == ["initial", "repair", "verification"]
    assert [item["exit_code"] for item in value["observations"]] == [2, 0, 0]
    assert value["actions"] == ["repair", "verify"]
    assert Path(installation["tool"]["state"]).read_text() == "corrected"


@pytest.mark.parametrize("scenario,expected_codes", [
    ("repair-failure", [2, 4, 2]), ("verify-failure", [2, 0, 2]),
])
def test_second_error_returns_both_diagnostics_without_second_repair(installation, scenario, expected_codes):
    installation["tool"]["scenario"] = scenario
    value = packet(invoke(installation), 4)
    assert value["status"] == "error"
    assert actions(installation) == ["run", "repair", "verify"]
    assert value["actions"] == ["repair", "verify"]
    assert [item["exit_code"] for item in value["observations"]] == expected_codes
    assert [item["stderr"] for item in value["observations"]] == json.loads(
        (FIXTURES / "maintenance_stderr_expected.json").read_text())
    assert all(item["stdout"] for item in value["observations"])


def test_new_invocation_has_independent_single_repair(installation):
    installation["tool"]["scenario"] = "verify-failure"
    assert packet(invoke(installation), 4)["status"] == "error"
    assert packet(invoke(installation), 4)["status"] == "error"
    assert actions(installation) == ["run", "repair", "verify", "run", "repair", "verify"]


@pytest.mark.parametrize("scenario", [
    "unknown", "malformed", "empty", "contradictory", "unavailable", "failed", "foreign-schema",
])
def test_unrepairable_or_unknown_result_reaches_agent_without_mutation(installation, scenario):
    installation["tool"]["scenario"] = scenario
    value = packet(invoke(installation), 4)
    assert value["status"] == "error"
    assert value["reason"]
    assert value["actions"] == []
    assert len(value["observations"]) == 1
    assert actions(installation) == ["run"]
    if scenario == "unknown":
        assert value["observations"][0] == json.loads(
            (FIXTURES / "maintenance_unknown_expected.json").read_text())
    if scenario == "contradictory":
        assert value["observations"][0]["exit_code"] == 7
    if scenario == "empty":
        assert value["observations"][0]["stdout"] == ""


@pytest.mark.parametrize("operation,arguments", [("check", ()), ("infra", ("--dry-run",))])
def test_read_only_operation_does_not_request_repair(installation, operation, arguments):
    installation["tool"]["scenario"] = "repair-success"
    value = packet(invoke(installation, operation, *arguments), 4)
    assert value["status"] == "error"
    assert value["actions"] == []
    assert len(calls(installation)) == 1
    assert not Path(installation["tool"]["state"]).exists()


def test_missing_executable_reports_available_diagnostics(installation):
    installation["command"]["parameters"]["python"] = str(installation["root"] / "missing-python")
    value = packet(invoke(installation), 4)
    assert value["status"] == "error"
    assert value["reason"]
    assert str(installation["root"] / "missing-python") in json.dumps(value)
    assert calls(installation) == []


def test_missing_required_placeholder_is_explicit_before_process(installation):
    del installation["command"]["parameters"]["tool_config"]
    value = packet(invoke(installation), 4)
    assert value["status"] == "error"
    assert "tool_config" in json.dumps(value)
    assert calls(installation) == []


def test_missing_handler_is_explicit_before_process(installation):
    installation["command"]["handler"]["callable"] = "absent_plugin:handle"
    value = packet(invoke(installation), 4)
    assert value["status"] == "error"
    assert "absent_plugin" in json.dumps(value)
    assert calls(installation) == []


def test_signaled_process_retains_partial_diagnostics(installation):
    installation["tool"]["scenario"] = "signal"
    value = packet(invoke(installation), 4)
    assert value["status"] == "error"
    assert value["actions"] == []
    assert value["observations"][0]["exit_code"] == -15
    assert value["observations"][0]["stdout"] == "partial external stdout\n"
    assert value["observations"][0]["stderr"] == "partial external stderr\n"
    assert actions(installation) == ["run"]


def test_configuration_controls_permission_to_repair(installation):
    installation["tool"]["scenario"] = "repair-success"
    installation["command"]["handler"]["options"]["repair_operations"] = []
    value = packet(invoke(installation), 4)
    assert value["status"] == "error"
    assert value["actions"] == []
    assert actions(installation) == ["run"]
    assert not Path(installation["tool"]["state"]).exists()
