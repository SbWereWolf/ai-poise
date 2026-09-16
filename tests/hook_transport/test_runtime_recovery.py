"""Regression coverage for native launcher/runtime loss and recovery."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import shutil
import shlex
import subprocess
import sys

from batch.helpers import request
from conftest import write_json
from poise.infrastructure.hook_transport import HookService

from .helpers import definition, event, install, settings


ROOT = Path(__file__).resolve().parents[2]
RECOVERY = ROOT / "src/poise/recover_runtime.sh"


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


def _bootstrap_packet(task):
    return request(
        "bootstrap",
        {"task": task, "decision": None, "feedback": None, "rework_stage": None},
    )


def _full_payload(stdout):
    payload = json.loads(stdout)
    response_path = payload.get("response_path")
    if response_path is not None:
        return json.loads(Path(response_path).read_text(encoding="utf-8"))
    return payload


def test_missing_interpreter_rejects_with_exact_recovery_command_before_state_mutation(
    project, tmp_path
):
    service, selected = _service_with_selected_interpreter(project, tmp_path)
    installed = install(service)
    _native(service, installed)
    binding = service.latest_binding("conversation", "primary")
    launcher = Path(binding["launcher"])
    started = subprocess.run(
        [str(launcher)],
        input=json.dumps(_bootstrap_packet(project["task"])),
        text=True,
        capture_output=True,
        timeout=15,
    )
    assert started.returncode == 0, started.stderr
    task_id = json.loads(started.stdout)["task"]
    before = deepcopy(service.bound_runtime(binding["binding_path"]).task_queries.record(task_id))

    selected.unlink()
    rejected = subprocess.run(
        [str(launcher)],
        input=json.dumps(_bootstrap_packet(None)),
        text=True,
        capture_output=True,
        timeout=15,
    )

    expected = [
        f"Configured Poise interpreter is unavailable: {selected}",
        (
            f"Recovery: bash {RECOVERY} --settings {service.settings.path} "
            "--python /absolute/path/to/compatible/python"
        ),
    ]
    assert rejected.returncode == 126
    assert rejected.stdout == ""
    assert rejected.stderr.splitlines() == expected
    after = service.bound_runtime(binding["binding_path"]).task_queries.record(task_id)
    assert after == before


def test_native_event_recreates_deleted_binding_and_launcher(project, tmp_path):
    service, _ = _service_with_selected_interpreter(project, tmp_path)
    installed = install(service)
    _native(service, installed)
    binding = service.latest_binding("conversation", "primary")
    binding_root = Path(binding["binding_path"]).parent
    shutil.rmtree(binding_root)

    result = _native(service, installed)
    restored = service.latest_binding("conversation", "primary")

    assert Path(restored["binding_path"]).is_file()
    assert Path(restored["launcher"]).is_file()
    assert restored["session_id"] == binding["session_id"]
    assert restored["launcher"] in result["hookSpecificOutput"]["additionalContext"]


def test_recovery_script_has_explicit_safe_interface():
    syntax = subprocess.run(
        ["bash", "-n", str(RECOVERY)], text=True, capture_output=True, timeout=15
    )
    assert syntax.returncode == 0, syntax.stderr
    help_result = subprocess.run(
        ["bash", str(RECOVERY), "--help"],
        text=True,
        capture_output=True,
        timeout=15,
    )
    assert help_result.returncode == 0, help_result.stderr
    assert "--settings ABSOLUTE_PATH --python ABSOLUTE_PATH" in help_result.stdout
    assert "does not modify the Task database" in help_result.stdout


def test_exact_recovery_command_restores_same_launcher_session_and_task(project, tmp_path):
    service, selected = _service_with_selected_interpreter(project, tmp_path)
    installed = install(service)
    _native(service, installed)
    binding = service.latest_binding("conversation", "primary")
    launcher = Path(binding["launcher"])
    started = subprocess.run(
        [str(launcher)],
        input=json.dumps(_bootstrap_packet(project["task"])),
        text=True,
        capture_output=True,
        timeout=15,
    )
    assert started.returncode == 0, started.stderr
    started_payload = _full_payload(started.stdout)
    task_id = started_payload["task"]
    before = deepcopy(service.bound_runtime(binding["binding_path"]).task_queries.record(task_id))

    shutil.rmtree(selected.parents[1])
    rejected = subprocess.run(
        [str(launcher)],
        input=json.dumps(_bootstrap_packet(None)),
        text=True,
        capture_output=True,
        timeout=15,
    )
    assert rejected.returncode == 126
    recovery_line = rejected.stderr.splitlines()[1]
    assert recovery_line.startswith("Recovery: ")
    recovery_command = recovery_line.removeprefix("Recovery: ").replace(
        "/absolute/path/to/compatible/python", sys.executable
    )

    recovered = subprocess.run(
        shlex.split(recovery_command),
        text=True,
        capture_output=True,
        timeout=60,
        cwd=ROOT,
    )
    assert recovered.returncode == 0, recovered.stderr
    assert selected.is_file()

    resumed = subprocess.run(
        [str(launcher)],
        input=json.dumps(_bootstrap_packet(None)),
        text=True,
        capture_output=True,
        timeout=15,
    )
    assert resumed.returncode == 0, resumed.stderr
    resumed_payload = _full_payload(resumed.stdout)
    assert resumed_payload["session"] == started_payload["session"]
    assert resumed_payload["hook_session"] == started_payload["hook_session"]
    assert service.latest_binding("conversation", "primary")["session_id"] == binding["session_id"]
    after = service.bound_runtime(binding["binding_path"]).task_queries.record(task_id)
    assert after == before
