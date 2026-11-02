"""Public consumer contracts; independent commands, literal argv and real files."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).with_name("fixtures")


@pytest.fixture
def installation(tmp_path):
    def create(name="Poise рабочая установка", context="base_dir"):
        root = tmp_path / name
        root.mkdir()
        shutil.copy2(ROOT / "bootstrap", root / "bootstrap")
        shutil.copytree(ROOT / "apps/bootstrap", root / "apps/bootstrap",
                        ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copy2(FIXTURES / "maintenance_handler.py", root / "fixture_handlers.py")
        (root / "config").mkdir()
        selected = root / "рабочий контекст"
        selected.mkdir()
        config = root / "config/bootstrap.json"
        log = root / "calls.jsonl"
        value = json.loads((FIXTURES / "bootstrap_entry.example.json").read_text())
        value["parameters"].update(python=sys.executable,
                                    probe=str(FIXTURES / "bootstrap_entry_probe.py"),
                                    marker=name, log=str(log))
        value["parameters"][context] = str(selected)
        for command in value["commands"].values():
            command[5] = "${" + context + "}"
        config.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        return {"root": root, "config": config, "log": log,
                "selected": selected, "marker": name, "value": value}
    return create


def invoke(item, operation="check", *arguments, cwd=None):
    return subprocess.run(
        [sys.executable, "-I", "-B", str(item["root"] / "bootstrap"),
         operation, *arguments], cwd=cwd or item["root"], capture_output=True, check=False)


def packet(result, expected=0):
    assert result.returncode == expected, (
        f"PERSISTENT-ENTRY: expected exit {expected}, got {result.returncode}; "
        f"stdout={result.stdout!r}; stderr={result.stderr!r}")
    return json.loads(result.stdout)


def calls(item):
    return [json.loads(line) for line in item["log"].read_text().splitlines()]


def expected_argv(item, operation="check", *forwarded):
    return [operation, str(item["selected"]), item["marker"],
            str(item["log"]), "ready", *forwarded]


@pytest.mark.parametrize("context", ["base_dir", "catalog_dir"])
@pytest.mark.parametrize("operation", ["infra", "deps", "check", "run"])
def test_short_operations_load_saved_context(installation, context, operation):
    item = installation(context=context)
    before = item["config"].read_bytes()
    value = packet(invoke(item, operation))
    assert value["status"] == "second-handler"
    assert value["marker"] == "selected-handler"
    assert value["operation"] == operation
    assert calls(item) == [{"argv": expected_argv(item, operation),
                            "cwd": str(item["selected"] if context == "base_dir"
                                       else item["root"])}]
    assert item["config"].read_bytes() == before


@pytest.mark.parametrize("context", ["base_dir", "catalog_dir"])
def test_other_context_override_restores_saved_call(installation, tmp_path, context):
    item = installation(context=context)
    caller = tmp_path / "caller"
    caller.mkdir()
    other = tmp_path / "other context"
    other.mkdir()
    before = item["config"].read_bytes()
    same_flag = "--" + context.replace("_", "-")
    other_flag = "--catalog-dir" if context == "base_dir" else "--base-dir"
    for arguments in [(), (same_flag, str(other), "--values", "first.json",
                            "--set", "x=Ж", "--values", "second.json", "--dry-run"), (),
                       (other_flag, str(other), "--values", "first.json",
                            "--set", "x=Ж", "--values", "second.json", "--dry-run"), ()]:
        assert packet(invoke(item, "check", *arguments, cwd=caller))["status"] == "second-handler"
    ordinary_cwd = item["selected"] if context == "base_dir" else caller
    override_cwd = caller if context == "base_dir" else other
    assert calls(item) == [
        {"argv": expected_argv(item), "cwd": str(ordinary_cwd)},
        {"argv": ["check", str(other), item["marker"], str(item["log"]), "ready",
                  "--values", "first.json", "--set", "x=Ж", "--values", "second.json", "--dry-run"],
         "cwd": str(other if context == "base_dir" else caller)},
        {"argv": expected_argv(item), "cwd": str(ordinary_cwd)},
        {"argv": expected_argv(item, "check", "--values", "first.json", "--set",
                               "x=Ж", "--values", "second.json", "--dry-run"),
         "cwd": str(override_cwd)},
        {"argv": expected_argv(item), "cwd": str(ordinary_cwd)},
    ]
    assert item["config"].read_bytes() == before


def test_selected_profile_and_installations_survive_other_cwd_and_temp_cleanup(installation, tmp_path):
    first, second = installation("first"), installation("second")
    before = [item["config"].read_bytes() for item in (first, second)]
    temporary = first["root"] / ".runtime"
    temporary.mkdir()
    (temporary / "unused").write_text("temporary material")
    shutil.rmtree(temporary)
    caller = tmp_path / "unrelated caller"
    caller.mkdir()
    for item, arguments in [(first, ()), (first, ("--config", str(second["config"]))),
                             (first, ()), (second, ())]:
        assert packet(invoke(item, "check", *arguments, cwd=caller))["status"] == "second-handler"
    assert calls(first) == [{"argv": expected_argv(first), "cwd": str(first["selected"])}] * 2
    assert calls(second) == [{"argv": expected_argv(second), "cwd": str(second["selected"])}] * 2
    assert [item["config"].read_bytes() for item in (first, second)] == before


def test_local_entry_is_directly_executable(installation):
    item = installation()
    assert os.access(item["root"] / "bootstrap", os.X_OK), "PERSISTENT-ENTRY: bootstrap must be executable"
    result = subprocess.run(["./bootstrap", "check"], cwd=item["root"],
                            capture_output=True, check=False)
    assert packet(result)["status"] == "second-handler"
    assert calls(item) == [{"argv": expected_argv(item), "cwd": str(item["selected"])}]


def test_short_check_preserves_unknown_external_diagnostics(installation):
    item = installation()
    value = item["value"]
    value["parameters"]["scenario"] = "unknown"
    value["handler"] = json.loads((FIXTURES / "maintenance_command.example.json").read_text())["handler"]
    item["config"].write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    result = packet(invoke(item), 4)
    assert result["status"] == "error"
    assert result["actions"] == []
    assert len(result["observations"]) == 1
    assert result["observations"][0]["stdout"] == "unknown external result\n"
    assert result["observations"][0]["stderr"] == "external diagnostic\n"
    assert result["observations"][0]["exit_code"] == 9
    assert len(calls(item)) == 1


def test_missing_saved_config_reports_path_before_external_effect(installation):
    item = installation()
    item["config"].unlink()
    result = packet(invoke(item), 4)
    assert result["status"] == "error"
    assert str(item["config"]) in result["reason"]
    assert not item["log"].exists()
