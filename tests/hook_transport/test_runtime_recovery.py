"""Regression coverage for the intentionally direct native Poise command."""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import shlex
import sys

import pytest
from poise.common import PoiseError
from batch.helpers import request

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


def _guarded_command(service, binding_path):
    recovery = str(Path(service.settings.raw["source_root"]) / "poise/recover_runtime.sh")
    recovery_command = shlex.join(
        [
            "bash",
            recovery,
            "--settings",
            str(service.settings.path),
            "--python",
            "/absolute/path/to/compatible/python",
        ]
    )
    guard = (
        "interpreter=$1; recovery_command=$2; source_root=$3; shift 3; "
        'if [ ! -x "$interpreter" ]; then '
        'printf "%s\\n" "Configured Poise interpreter is unavailable: $interpreter" >&2; '
        'printf "%s\\n" "Recovery: $recovery_command" >&2; '
        "exit 126; fi; "
        'exec env "PYTHONPATH=$source_root" "$interpreter" "$@"'
    )
    return shlex.join(
        [
            "/bin/sh",
            "-c",
            guard,
            "poise-runtime-guard",
            service.settings.raw["python"],
            recovery_command,
            service.settings.raw["source_root"],
            "-B",
            "-m",
            "poise",
            "hook-work",
            "--settings",
            str(service.settings.path),
            "--binding",
            str(binding_path),
        ]
    )


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


def test_exact_guarded_launcher_is_migrated_without_changing_session(project, tmp_path):
    service, _ = _service_with_selected_interpreter(project, tmp_path)
    installed = install(service)
    _native(service, installed)
    binding = service.latest_binding("conversation", "primary")
    launcher = Path(binding["launcher"])
    launcher.write_text(
        f"#!/bin/sh\nexec {_guarded_command(service, binding['binding_path'])}\n",
        encoding="utf-8",
    )
    launcher.chmod(service.settings.raw["executable_mode"])

    _native(service, installed)
    migrated = service.latest_binding("conversation", "primary")
    command = launcher.read_text(encoding="utf-8")

    assert migrated["session_id"] == binding["session_id"]
    assert "exec env PYTHONPATH=" in command
    assert " -B -m poise hook-work " in command
    assert "poise-runtime-guard" not in command


def _break_executable(path, missing):
    path.unlink()
    if not missing:
        path.write_text("not executable\n", encoding="utf-8")
        path.chmod(0o600)


def _assert_environment_failure(exc, path, missing):
    message = str(exc.value)
    assert str(path) in message
    assert "environment/configuration" in message
    assert ("missing" if missing else "not executable") in message


@pytest.mark.parametrize("missing", [True, False])
def test_settings_report_exact_unavailable_python(project, tmp_path, missing):
    service, selected = _service_with_selected_interpreter(project, tmp_path)
    _break_executable(selected, missing)
    with pytest.raises(PoiseError) as exc:
        HookService(service.settings.path)
    _assert_environment_failure(exc, selected, missing)
    assert not service.settings.hooks_file.exists()


@pytest.mark.parametrize("operation", ["install", "native", "work"])
def test_cached_service_rechecks_python_before_mutation(project, tmp_path, monkeypatch, operation):
    import poise.infrastructure.hook_transport as transport
    service, selected = _service_with_selected_interpreter(project, tmp_path)
    installed = install(service)
    _native(service, installed)
    binding = service.latest_binding("conversation", "primary")
    before = service.settings.hooks_file.read_bytes()
    selected.unlink()
    def forbidden(*args, **kwargs):
        pytest.fail("runtime/Task establishment must not precede runtime preflight")
    monkeypatch.setattr(transport, "establish_poise", forbidden)
    with pytest.raises(PoiseError) as exc:
        if operation == "install":
            install(service, request_id="second", revision=service.revision())
        elif operation == "native":
            _native(service, installed)
        else:
            service.work(binding["binding_path"], request("bootstrap", {
                "task": None, "decision": None, "feedback": None, "rework_stage": None}))
    _assert_environment_failure(exc, selected, True)
    assert service.settings.hooks_file.read_bytes() == before
    assert service.latest_binding("conversation", "primary") == binding


@pytest.mark.parametrize("missing", [True, False])
def test_bootstrap_reports_exact_bad_launcher_before_task_work(project, tmp_path, monkeypatch, missing):
    import poise.infrastructure.hook_transport as transport
    service, _ = _service_with_selected_interpreter(project, tmp_path)
    installed = install(service)
    _native(service, installed)
    binding = service.latest_binding("conversation", "primary")
    launcher = Path(binding["launcher"])
    _break_executable(launcher, missing)
    def forbidden(*args, **kwargs):
        pytest.fail("runtime/Task establishment must not precede launcher preflight")
    monkeypatch.setattr(transport, "establish_poise", forbidden)
    with pytest.raises(PoiseError) as exc:
        service.work(binding["binding_path"], request("bootstrap", {
            "task": None, "decision": None, "feedback": None, "rework_stage": None}))
    _assert_environment_failure(exc, launcher, missing)
    assert service.latest_binding("conversation", "primary") == binding


@pytest.mark.parametrize("missing", [True, False])
def test_runtime_setup_rejects_bad_python_without_writes(project, tmp_path, missing):
    from poise.infrastructure.hook_transport import setup_runtime
    from .test_setup import prepared
    path, packet = prepared(project, tmp_path)
    selected = tmp_path / "unavailable selected python"
    selected.write_text("not executable\n", encoding="utf-8")
    selected.chmod(0o600)
    if missing:
        selected.unlink()
    packet["settings"]["python"] = str(selected)
    with pytest.raises(PoiseError) as exc:
        setup_runtime(path, packet)
    _assert_environment_failure(exc, selected, missing)
    assert not path.exists()
    assert not (project["root"] / ".codex/hooks.json").exists()


def test_operator_restore_reuses_original_binding_and_direct_launcher(project, tmp_path):
    service, selected = _service_with_selected_interpreter(project, tmp_path)
    installed = install(service)
    _native(service, installed)
    binding = service.latest_binding("conversation", "primary")
    launcher = Path(binding["launcher"])
    selected.unlink()
    with pytest.raises(PoiseError, match="environment/configuration"):
        service.bound_runtime(binding["binding_path"])
    # Ordinary restoration of the explicitly selected environment, not a fallback.
    selected.symlink_to(sys.executable)
    launcher.unlink()
    _native(service, installed)
    restored = service.latest_binding("conversation", "primary")
    assert restored == binding
    assert " -B -m poise hook-work " in launcher.read_text()
    assert "poise-runtime-guard" not in launcher.read_text()
    result = service.work(binding["binding_path"], request("bootstrap", {
        "task": None, "decision": None, "feedback": None, "rework_stage": None}))
    assert result["status"] == "read_only"


def test_fresh_native_launcher_bootstraps_and_verifies_without_injected_environment(project, tmp_path):
    import os
    import subprocess
    service, _ = _service_with_selected_interpreter(project, tmp_path)
    installed = install(service)
    _native(service, installed)
    binding = service.latest_binding("conversation", "primary")
    environment = {k: v for k, v in os.environ.items()
                   if k not in ("PYTHONPATH", "POISE_CALLER_BINDING", "CODEX_THREAD_ID", "CODEX_SESSION_ID")}
    packets = [request("bootstrap", {"task": None, "decision": None, "feedback": None, "rework_stage": None}),
               request("verify", {"result": None, "artifacts": []})]
    for packet, status in zip(packets, ("read_only", "read_only_verified")):
        output = subprocess.run([binding["launcher"]], input=json.dumps(packet),
                                text=True, capture_output=True, env=environment,
                                cwd=project["root"], timeout=20)
        assert output.returncode == 0, output.stderr
        assert json.loads(output.stdout)["status"] == status
    assert service.latest_binding("conversation", "primary") == binding
    assert service.bound_runtime(binding["binding_path"]).session == binding["session_id"]


def test_native_event_repairs_only_exact_owned_launcher_mode(project, tmp_path):
    service, _ = _service_with_selected_interpreter(project, tmp_path)
    installed = install(service)
    _native(service, installed)
    binding = service.latest_binding("conversation", "primary")
    path = Path(binding["launcher"])
    original = path.read_bytes()
    path.chmod(0o600)
    _native(service, installed)
    assert path.stat().st_mode & 0o777 == service.settings.raw["executable_mode"]
    assert path.read_bytes() == original
    assert service.latest_binding("conversation", "primary") == binding
    path.write_text("#!/bin/sh\necho foreign code\n")
    with pytest.raises(PoiseError, match="changed externally"):
        _native(service, installed)
    assert path.read_text() == "#!/bin/sh\necho foreign code\n"


def test_cached_runtime_reports_exact_disappeared_entry_point_before_install(project, tmp_path):
    selected_source = tmp_path / "installed source"
    entry = selected_source / "poise" / "__main__.py"
    entry.parent.mkdir(parents=True)
    entry.write_text("# fixture entry point\n")
    path = settings(project, tmp_path)
    raw = json.loads(path.read_text()); raw["source_root"] = str(selected_source)
    write_json(path, raw)
    service = HookService(path)
    entry.unlink()
    with pytest.raises(PoiseError) as exc:
        install(service)
    assert str(entry) in str(exc.value)
    assert "environment/configuration" in str(exc.value)
    assert not service.settings.hooks_file.exists()
