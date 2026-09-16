"""Regression coverage for the intentionally direct native Poise command."""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import shlex
import sys

from conftest import write_json
from poise.infrastructure.hook_transport import HookService

from .helpers import definition, event, install, settings


def _service_with_selected_interpreter(project, tmp_path):
    selected = tmp_path / "selected runtime" / "bin" / "python"
    selected.parent.mkdir(parents=True)
    selected.symlink_to(sys.executable)
    settings_path = settings(project, tmp_path)
    document = json.loads(settings_path.read_text(encoding="utf-8"))
    document["python"] = str(selected)
    write_json(settings_path, document)
    return HookService(settings_path), selected


def _native(service, installed):
    native = event()
    native["cwd"] = str(service.settings.root)
    return service.event(installed["definition_path"], native)


def test_managed_command_invokes_configured_interpreter_directly(project, tmp_path):
    service, selected = _service_with_selected_interpreter(project, tmp_path)
    binding = tmp_path / "binding.json"

    command = service.settings.command("hook-work", "--binding", str(binding))

    assert shlex.split(command) == [
        "env",
        f"PYTHONPATH={service.settings.raw['source_root']}",
        str(selected),
        "-B",
        "-m",
        "poise",
        "hook-work",
        "--settings",
        str(service.settings.path),
        "--binding",
        str(binding),
    ]
    assert "poise-runtime-guard" not in command
    assert "Recovery:" not in command


def test_native_event_creates_direct_launcher_and_recreates_deleted_binding(project, tmp_path):
    service, _ = _service_with_selected_interpreter(project, tmp_path)
    installed = install(service)
    _native(service, installed)
    binding = service.latest_binding("conversation", "primary")
    launcher = Path(binding["launcher"])
    command = launcher.read_text(encoding="utf-8")
    assert "exec env PYTHONPATH=" in command
    assert " -B -m poise hook-work " in command
    assert "poise-runtime-guard" not in command

    binding_root = Path(binding["binding_path"]).parent
    shutil.rmtree(binding_root)
    result = _native(service, installed)
    restored = service.latest_binding("conversation", "primary")

    assert Path(restored["binding_path"]).is_file()
    assert Path(restored["launcher"]).is_file()
    assert "poise-runtime-guard" not in Path(restored["launcher"]).read_text(encoding="utf-8")
    assert restored["session_id"] == binding["session_id"]
    assert restored["launcher"] in result["hookSpecificOutput"]["additionalContext"]


def test_install_emits_one_direct_group_per_declared_event(project, tmp_path):
    service, _ = _service_with_selected_interpreter(project, tmp_path)
    installed = install(service, definition())
    document = json.loads(service.settings.hooks_file.read_text(encoding="utf-8"))

    groups = [group for event_groups in document["hooks"].values() for group in event_groups]
    commands = [hook["command"] for group in groups for hook in group["hooks"]]

    assert installed["status"] == "installed"
    assert len(commands) == len(definition()["events"])
    assert all(command.startswith("env PYTHONPATH=") for command in commands)
    assert all(" -B -m poise hook " in command for command in commands)
    assert all("poise-runtime-guard" not in command for command in commands)
