"""Regression coverage for the intentionally direct native Poise command."""

from __future__ import annotations

import json
import os
from pathlib import Path
import py_compile
import shutil
import shlex
import subprocess
import sys

import pytest
from poise.common import PoiseError, digest, file_digest
from batch.helpers import request

from conftest import write_json
from poise.infrastructure.hook_transport import HookService

from .helpers import definition, event, install, settings


def test_reconfiguration_reads_only_active_binding_projections(project,tmp_path):
    service=HookService(settings(project,tmp_path))
    installed=install(service)
    records={}
    for session in ('active-session','ended-session','unknown-session'):
        native=event(session=session);native['cwd']=str(project['app'])
        if session=='unknown-session':
            service._bind(installed['definition_path'],native)
        else:
            service.event(installed['definition_path'],native)
        records[session]=service.latest_binding(session,'primary')
    service.registry.record_event(records['ended-session']['session_id'],'SessionEnd','ended-turn',None)
    for session in ('ended-session','unknown-session'):
        Path(records[session]['binding_path']).unlink()
        Path(records[session]['launcher']).unlink()
    with service.registry.read_transaction() as db:
        before=[tuple(row) for row in db.execute('SELECT * FROM bindings ORDER BY id')]
        events_before=[tuple(row) for row in db.execute('SELECT * FROM hook_events ORDER BY id')]
    raw={**service.settings.raw,'capability_summary':{
        **service.settings.raw['capability_summary'],
        'header_template':'Updated readiness={ready}; checked_at={checked_at}; workspace={workspace}; snapshot={snapshot_path}'}}
    receipt=service.reconfigure({'request_id':'active-bindings-only',
        'expected_settings_digest':digest(service.settings.raw),
        'expected_revision':service.revision(),'target_revision':service.revision(),
        'settings':raw,'definition':json.loads(Path(installed['definition_path']).read_text())})
    assert receipt['status']=='installed'
    assert receipt['binding_count']==1
    current=HookService(service.settings.path)
    with current.registry.read_transaction() as db:
        after={row[0]:tuple(row) for row in db.execute('SELECT * FROM bindings ORDER BY id')}
        assert [tuple(row) for row in db.execute('SELECT * FROM hook_events ORDER BY id')][:len(events_before)]==events_before
    for row in before:
        if row[0]!=records['active-session']['session_id']:
            assert after[row[0]]==row
    assert after[records['active-session']['session_id']]!=next(row for row in before if row[0]==records['active-session']['session_id'])
    assert json.loads(service.settings.path.read_text())==raw
    for session in ('ended-session','unknown-session'):
        assert not Path(records[session]['launcher']).exists()
        assert not Path(records[session]['binding_path']).exists()


@pytest.mark.parametrize('changed',['none','resumed','record','missing'])
def test_explicit_ended_binding_release_revalidates_selected_episode(project,tmp_path,changed):
    service=HookService(settings(project,tmp_path));installed=install(service)
    native=event(session='selected-ended-session');native['cwd']=str(project['app'])
    service.event(installed['definition_path'],native)
    binding=service.latest_binding('selected-ended-session','primary')
    end=service.registry.record_event(binding['session_id'],'SessionEnd','ended-turn',None)
    selected=dict(binding)
    if changed=='resumed':
        service.event(installed['definition_path'],native)
    elif changed=='record':
        selected['settings_digest']='different-selected-record'
    elif changed=='missing':
        Path(binding['binding_path']).unlink()
        Path(binding['launcher']).unlink()
    receipt=service.release_ended_binding(selected,end)
    if changed in ('none','missing'):
        assert receipt['status']=='released'
        assert not Path(binding['launcher']).exists()
        assert not Path(binding['binding_path']).exists()
        assert not Path(binding['binding_path']).parent.exists()
        assert service.registry.liveness(binding['session_id']).value=='dead'
        assert service.release_ended_binding(selected,end)['status']=='already_released'
    else:
        assert receipt['status']=='skipped'
        assert Path(binding['launcher']).is_file()
        assert Path(binding['binding_path']).is_file()
        assert service.registry.get(binding['session_id'])[0]==binding


def test_explicit_release_rejects_foreign_launcher_without_losing_owned_state(project,tmp_path):
    service=HookService(settings(project,tmp_path));installed=install(service)
    native=event(session='foreign-edited-session');native['cwd']=str(project['app'])
    service.event(installed['definition_path'],native)
    binding=service.latest_binding('foreign-edited-session','primary')
    end=service.registry.record_event(binding['session_id'],'SessionEnd','ended-turn',None)
    launcher=Path(binding['launcher']);launcher.write_text('# user-owned replacement\n')
    paths=[Path(binding['binding_path']),launcher,launcher.parent/'capabilities.json']
    before={str(path):path.read_bytes() for path in paths}
    with pytest.raises(PoiseError,match='[Ff]oreign launcher'):
        service.release_ended_binding(binding,end)
    assert {str(path):path.read_bytes() for path in paths}==before
    assert service.registry.get(binding['session_id'])[0]==binding


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


def _service_with_copied_source(project, tmp_path, source_name):
    settings_path = settings(project, tmp_path)
    document = json.loads(settings_path.read_text(encoding="utf-8"))
    selected_source = tmp_path / source_name
    shutil.copytree(Path(document["source_root"]) / "poise", selected_source / "poise",
                    ignore=shutil.ignore_patterns("__pycache__"))
    document["source_root"] = str(selected_source)
    write_json(settings_path, document)
    return HookService(settings_path), selected_source


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
    fixture_code_root = Path(__file__).resolve().parents[2]
    expected_python_path = os.pathsep.join((str(fixture_code_root / "src"), str(fixture_code_root)))

    command = service.settings.command("hook-work", "--binding", str(binding))

    arguments = shlex.split(command)
    assert arguments[arguments.index("env"):] == [
        "env",
        f"PYTHONPATH={expected_python_path}",
        str(selected),
        "-P",
        "-B",
        "-X",
        "pycache_prefix=" + os.devnull,
        "-m",
        "poise",
        "hook-work",
        "--settings",
        str(service.settings.path),
        "--binding",
        str(binding),
    ]
    assert "poise-runtime-guard" not in command


@pytest.mark.parametrize("route", ["launcher", "hook"])
@pytest.mark.parametrize("problem", ["package", "init", "main", "path_separator"])
def test_invalid_selected_source_has_configured_rejection_and_recovery(project, tmp_path, route, problem):
    source_name = "selected source" if problem != "path_separator" else "selected" + os.pathsep + "source"
    service, selected_source = _service_with_copied_source(project, tmp_path, source_name)
    document = json.loads(service.settings.path.read_text(encoding="utf-8"))
    document["exit_codes"]["rejected"] = 17
    write_json(service.settings.path, document)
    service = HookService(service.settings.path)
    if route == "launcher":
        launcher = tmp_path / "work.sh"
        launcher.write_bytes(service.settings.launcher_bytes(tmp_path / "unused-binding.json"))
        command = ["/bin/sh", str(launcher), "--help"]
    else:
        command = shlex.split(service.settings.command("hook")) + ["--help"]
    if problem == "package":
        (selected_source / "poise").rename(tmp_path / "removed-source")
    elif problem == "init":
        (selected_source / "poise/__init__.py").unlink()
    elif problem == "main":
        (selected_source / "poise/__main__.py").unlink()
    else:
        assert (selected_source / "poise/__init__.py").is_file()
        assert (selected_source / "poise/__main__.py").is_file()
    empty_cwd = tmp_path / "empty-cwd"
    empty_cwd.mkdir()
    result = subprocess.run(command, cwd=empty_cwd, capture_output=True, text=True, timeout=15)
    assert result.returncode == 17
    assert result.stdout == ""
    assert result.stderr == (
        "Configured Poise source must be one directory containing poise/__init__.py and poise/__main__.py: "
        + str(selected_source) + "\nRecovery: restore that source directory or correct source_root in "
        + str(service.settings.path) + ", then retry.\n"
    )


@pytest.mark.parametrize("route", ["launcher", "hook"])
def test_configured_source_wins_over_real_source_in_current_directory(project, tmp_path, route):
    service = HookService(settings(project, tmp_path))
    selected_source = Path(service.settings.raw["source_root"]) / "poise"
    shadow_cwd = tmp_path / "shadow-cwd"
    shutil.copytree(selected_source, shadow_cwd / "poise", ignore=shutil.ignore_patterns("__pycache__"))
    shadow_entry = shadow_cwd / "poise/__main__.py"
    text = shadow_entry.read_text(encoding="utf-8")
    markers = [
        "bound=sub.add_parser('hook-work',help='One work packet using a generated session binding')",
        "hook=sub.add_parser('hook',help='Codex native command-hook event (adapter, not a task API)')",
    ]
    for marker in markers:
        assert text.count(marker) == 1
        text = text.replace(marker, marker[:-1] + ",description='CWD_SOURCE_SENTINEL')")
    shadow_entry.write_text(text, encoding="utf-8")
    if route == "launcher":
        launcher = tmp_path / "work.sh"
        launcher.write_bytes(service.settings.launcher_bytes(tmp_path / "unused-binding.json"))
        command = ["/bin/sh", str(launcher), "--help"]
    else:
        command = shlex.split(service.settings.command("hook")) + ["--help"]
    empty_cwd = tmp_path / "empty-cwd"
    empty_cwd.mkdir()
    reference = subprocess.run(command, cwd=empty_cwd, capture_output=True, text=True, timeout=15)
    assert reference.returncode == 0, reference.stderr
    assert "usage:" in reference.stdout
    assert "CWD_SOURCE_SENTINEL" not in reference.stdout
    actual = subprocess.run(command, cwd=shadow_cwd, capture_output=True, text=True, timeout=15)
    assert actual.returncode == 0, actual.stderr
    assert actual.stderr == ""
    assert actual.stdout == reference.stdout
    assert "CWD_SOURCE_SENTINEL" not in actual.stdout


@pytest.mark.parametrize("route", ["launcher", "hook"])
def test_next_invocation_reads_edited_source_with_valid_old_timestamp_bytecode(project, tmp_path, route):
    service = HookService(settings(project, tmp_path))
    selected_source = tmp_path / "selected-source"
    shutil.copytree(Path(service.settings.raw["source_root"]) / "poise", selected_source / "poise",
                    ignore=shutil.ignore_patterns("__pycache__"))
    service.settings.raw["source_root"] = str(selected_source)
    entry = selected_source / "poise/__main__.py"
    source = entry.read_text(encoding="utf-8")
    for marker in (
        "bound=sub.add_parser('hook-work',help='One work packet using a generated session binding')",
        "hook=sub.add_parser('hook',help='Codex native command-hook event (adapter, not a task API)')",
    ):
        assert source.count(marker) == 1
        source = source.replace(marker, marker[:-1] + ",description='SOURCE_CACHED')")
    entry.write_text(source, encoding="utf-8")
    if route == "launcher":
        launcher = tmp_path / "work.sh"
        launcher.write_bytes(service.settings.launcher_bytes(tmp_path / "unused-binding.json"))
        command = ["/bin/sh", str(launcher), "--help"]
    else:
        command = shlex.split(service.settings.command("hook")) + ["--help"]
    reference = subprocess.run(command, cwd=tmp_path, capture_output=True, text=True, timeout=15)
    assert reference.returncode == 0, reference.stderr
    assert "SOURCE_CACHED" in reference.stdout
    py_compile.compile(str(entry), doraise=True,
                       invalidation_mode=py_compile.PycInvalidationMode.TIMESTAMP)
    cached_stat = entry.stat()
    edited = source.replace("SOURCE_CACHED", "SOURCE_EDITED")
    assert len(edited.encode("utf-8")) == len(source.encode("utf-8"))
    entry.write_text(edited, encoding="utf-8")
    os.utime(entry, ns=(cached_stat.st_atime_ns, cached_stat.st_mtime_ns))
    actual = subprocess.run(command, cwd=tmp_path, capture_output=True, text=True, timeout=15)
    assert actual.returncode == 0, actual.stderr
    assert actual.stderr == ""
    assert "SOURCE_EDITED" in actual.stdout
    assert "SOURCE_CACHED" not in actual.stdout


@pytest.mark.parametrize("route", ["native", "reconfigure"])
@pytest.mark.parametrize("foreign_edit", [False, True])
@pytest.mark.parametrize("issued_form", ["bytecode_only", "selected_source"])
def test_previous_forwarded_launcher_upgrade_preserves_binding_and_foreign_content(project, tmp_path, route, foreign_edit, issued_form):
    service, selected_source = _service_with_copied_source(project, tmp_path, "selected-source")
    foreign = {"matcher": "startup", "hooks": [{"type": "command", "command": "echo foreign"}]}
    write_json(service.settings.hooks_file, {"hooks": {"SessionStart": [foreign]}})
    installed = install(service, revision=service.revision())
    _native(service, installed)
    binding = service.latest_binding("conversation", "primary")
    launcher = Path(binding["launcher"])
    flags = ["-B"] if issued_form == "bytecode_only" else ["-P", "-B", "-X", "pycache_prefix=" + os.devnull]
    historical = ["env", "PYTHONPATH=" + service.settings.raw["source_root"],
                  service.settings.raw["python"], *flags, "-m", "poise", "hook-work",
                  "--settings", str(service.settings.path), "--binding", binding["binding_path"]]
    previous = ('#!/bin/sh\nexec ' + shlex.join(historical) + ' "$@"\n').encode()
    launcher.write_bytes(previous + (b"# foreign edit\n" if foreign_edit else b""))
    before_launcher = launcher.read_bytes()
    before_binding = Path(binding["binding_path"]).read_bytes()
    before_hooks = service.settings.hooks_file.read_bytes()
    if route == "native":
        def invoke():
            return _native(service, installed)
        expected_error = "Launcher was changed externally"
    else:
        raw = json.loads(service.settings.path.read_text(encoding="utf-8"))
        packet = {"request_id": "upgrade-previous-forwarded-launcher",
                  "expected_settings_digest": digest(raw),
                  "expected_revision": file_digest(service.settings.hooks_file),
                  "target_revision": file_digest(service.settings.hooks_file),
                  "settings": raw,
                  "definition": json.loads(Path(installed["definition_path"]).read_text(encoding="utf-8"))}
        def invoke():
            return service.reconfigure(packet)
        expected_error = "Foreign launcher content before reconfiguration"
    if foreign_edit:
        with pytest.raises(PoiseError, match=expected_error):
            invoke()
        assert launcher.read_bytes() == before_launcher
        assert Path(binding["binding_path"]).read_bytes() == before_binding
        assert service.settings.hooks_file.read_bytes() == before_hooks
    else:
        invoke()
        arguments = shlex.split(launcher.read_text(encoding="utf-8").splitlines()[1])
        assert arguments[arguments.index("env"):] == [
            "env", "PYTHONPATH=" + os.pathsep.join((str(selected_source), str(selected_source.parent))),
            service.settings.raw["python"],
            "-P", "-B", "-X", "pycache_prefix=" + os.devnull, "-m", "poise", "hook-work",
            "--settings", str(service.settings.path), "--binding", binding["binding_path"], "$@",
        ]
        assert service.latest_binding("conversation", "primary") == binding
        (selected_source / "poise").rename(tmp_path / "removed-source")
        result = subprocess.run(["/bin/sh", str(launcher), "--help"], cwd=tmp_path,
                                capture_output=True, text=True, timeout=15)
        assert result.returncode == 2
        assert result.stdout == ""
        assert "Recovery: restore that source directory or correct source_root in " in result.stderr
    document = json.loads(service.settings.hooks_file.read_text(encoding="utf-8"))
    assert foreign in document["hooks"]["SessionStart"]


def test_native_event_creates_direct_launcher_and_recreates_deleted_binding(project, tmp_path):
    service, _ = _service_with_selected_interpreter(project, tmp_path)
    installed = install(service)
    _native(service, installed)
    binding = service.latest_binding("conversation", "primary")
    launcher = Path(binding["launcher"])
    command = launcher.read_text(encoding="utf-8")
    arguments = shlex.split(command)
    source = Path(service.settings.raw["source_root"])
    assert arguments[arguments.index("env") + 1] == "PYTHONPATH=" + os.pathsep.join((str(source), str(source.parent)))
    assert (" -P -B -X pycache_prefix=" + os.devnull + " -m poise hook-work ") in command
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
    for command in commands:
        arguments = shlex.split(command)
        source = Path(service.settings.raw["source_root"])
        assert arguments[arguments.index("env") + 1] == "PYTHONPATH=" + os.pathsep.join((str(source), str(source.parent)))
    assert all((" -P -B -X pycache_prefix=" + os.devnull + " -m poise hook ") in command for command in commands)
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
    arguments = shlex.split(command)
    source = Path(service.settings.raw["source_root"])
    assert arguments[arguments.index("env") + 1] == "PYTHONPATH=" + os.pathsep.join((str(source), str(source.parent)))
    assert (" -P -B -X pycache_prefix=" + os.devnull + " -m poise hook-work ") in command
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
    assert (" -P -B -X pycache_prefix=" + os.devnull + " -m poise hook-work ") in launcher.read_text()
    assert "poise-runtime-guard" not in launcher.read_text()
    result = service.work(binding["binding_path"], request("bootstrap", {
        "task": None, "decision": None, "feedback": None, "rework_stage": None}))
    assert result["status"] == "read_only"


def test_fresh_native_launcher_bootstraps_and_verifies_with_observed_host_identity(project, tmp_path):
    import os
    import subprocess
    service, _ = _service_with_selected_interpreter(project, tmp_path)
    installed = install(service)
    _native(service, installed)
    binding = service.latest_binding("conversation", "primary")
    environment = {k: v for k, v in os.environ.items()
                   if k not in ("PYTHONPATH", "POISE_CALLER_BINDING", "CODEX_THREAD_ID", "CODEX_SESSION_ID")}
    environment["CODEX_THREAD_ID"] = "conversation"  # host fixture, not a launcher override
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


@pytest.fixture(autouse=True)
def primary_native_host(monkeypatch):
    """Primary-hook fixtures run as the conversation explicitly named in their events."""
    monkeypatch.delenv('CODEX_SESSION_ID', raising=False)
    monkeypatch.delenv('POISE_CALLER_BINDING', raising=False)
    monkeypatch.setenv('CODEX_THREAD_ID', 'conversation')
