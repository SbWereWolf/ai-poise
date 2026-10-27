"""Versioned sanitized examples and local working-file delivery."""

import json
from pathlib import Path
import subprocess
from string import Template

ROOT = Path(__file__).resolve().parents[2]


def test_populated_command_example_exposes_selected_tool_and_handler():
    path = ROOT / "config/bootstrap.example.json"
    assert path.is_file(), "COMMAND-EXAMPLE: versioned populated example is absent"
    value = json.loads(path.read_text(encoding="utf-8"))
    assert value["handler"][
               "callable"] == "apps.bootstrap.environment_result:handle"
    assert value["handler"]["options"]["schema"] is None
    assert {"infra", "deps", "check", "run", "repair", "verify"} <= set(value["commands"])
    for command in value["commands"].values():
        assert isinstance(command, list) and command
        assert all(
            isinstance(argument, str) and argument for argument in
            command)
        assert "${tool_config}" in command
        assert "--config" in command
        assert "--parameters" in command
        assert "--base-dir" in command
        assert "--catalog-dir" not in command
    assert all(isinstance(value["parameters"][name], str) and
               value["parameters"][name]
               for name in ("python", "tool_config"))


def test_external_tool_config_has_a_versioned_example():
    path = ROOT / "config/environment-maintenance.example.json"
    assert path.is_file(), "TOOL-EXAMPLE: versioned external settings example is absent"
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    assert isinstance(value.get("python"), str) and value[
        "python"], "TOOL-EXAMPLE: explicit interpreter value required"
    selected_python = Template(value["python"]).substitute(
        PYTHON_EXECUTABLE="/srv/poise/bootstrap-venv/bin/python")
    assert Path(
        selected_python).is_absolute(), "TOOL-EXAMPLE: explicit absolute interpreter path required"


def test_working_configs_are_ignored_and_only_examples_are_versioned():
    paths = ["config/bootstrap.json",
             "config/environment-maintenance.json"]
    result = subprocess.run(
        ["git", "check-ignore", "--no-index", *paths],
        cwd=ROOT, capture_output=True, text=True, check=False)
    assert result.returncode == 0, "WORKING-CONFIG: working paths are not ignored"
    assert result.stdout.splitlines() == paths
    tracked = subprocess.check_output(
        ["git", "ls-files", "--", *paths], cwd=ROOT, text=True)
    assert tracked == ""
