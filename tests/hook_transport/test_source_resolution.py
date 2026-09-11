from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

import pytest

from batch.helpers import request
from conftest import git, write_json
from harness.application.work import WorkTools
from harness.infrastructure.hook_transport import HookService
from hook_transport.helpers import definition, event, install, settings
from sprints.helpers import draft, publish, setup as sprint_setup, task as sprint_task


SOURCE = Path(__file__).resolve().parents[2] / "src" / "harness"


def _instrumented_project_source(project):
    target = project["app"] / "src" / "harness"
    shutil.copytree(SOURCE, target)
    transport = target / "infrastructure" / "hook_transport.py"
    text = transport.read_text(encoding="utf-8")
    old = "return {**result,'capability_checks':checks,'hook_session':record['session_id']}"
    new = (
        "return {**result,'capability_checks':checks,'hook_session':record['session_id'],"
        "'loaded_source':str(Path(__file__).resolve().parents[1])}"
    )
    assert text.count(old) == 1
    transport.write_text(text.replace(old, new), encoding="utf-8")
    git(project["app"], "add", "src/harness")
    git(project["app"], "commit", "-m", "Add instrumented Harness source")
    return target.parent


def _service(project, tmp_path):
    installation_source = _instrumented_project_source(project)
    path = settings(project, tmp_path)
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["source_root"] = str(installation_source)
    raw["output_chars"] = 100_000
    write_json(path, raw)
    service = HookService(path)
    installed = install(service)
    return service, installed, installation_source


def _launcher(service, installed, session):
    native = event("SessionStart", session=session)
    native["cwd"] = str(service.settings.root)
    service.event(installed["definition_path"], native)
    binding = service.latest_binding(session, "primary")
    return Path(binding["launcher"]), binding


def _call(path, packet):
    result = subprocess.run(
        [str(path)],
        input=json.dumps(packet),
        text=True,
        capture_output=True,
        timeout=30,
    )
    payload = json.loads(result.stdout) if result.stdout else None
    if isinstance(payload, dict) and "response_path" in payload:
        payload = json.loads(Path(payload["response_path"]).read_text(encoding="utf-8"))
    return result, payload


def _bootstrap(task):
    return request(
        "bootstrap",
        {"task": task, "decision": None, "feedback": None, "rework_stage": None},
    )


def _digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def test_existing_task_bootstrap_and_current_continuation_load_registered_task_source(project, tmp_path):
    service, installed, installation = _service(project, tmp_path)
    launcher, binding = _launcher(service, installed, "task-session")
    prompt = event("UserPromptSubmit", session="task-session", turn="task-turn")
    prompt["cwd"] = str(service.settings.root)
    service.event(installed["definition_path"], prompt)

    started, context = _call(launcher, _bootstrap(project["task"]))
    assert started.returncode == 0, started.stdout + started.stderr
    task_source = Path(context["worktree"]) / "src" / "harness"
    assert Path(context["loaded_source"]) == task_source
    assert context["session"] == binding["session_id"]
    assert context["interaction"]["observed_messages_count"] == 1
    assert context["interaction"]["source"] == {
        "id": "codex-hook-main",
        "mode": "runtime_event",
    }
    assert task_source != installation / "harness"

    continued, current = _call(launcher, _bootstrap(None))
    assert continued.returncode == 0, continued.stdout + continued.stderr
    assert Path(current["loaded_source"]) == task_source
    assert current["session"] == binding["session_id"]


def test_taskless_and_new_task_creation_use_configured_installation_source(project, tmp_path):
    service, installed, installation = _service(project, tmp_path)
    launcher, _ = _launcher(service, installed, "creation-session")

    read, summary = _call(launcher, _bootstrap(None))
    assert read.returncode == 0, read.stdout + read.stderr
    assert summary["status"] == "read_only"
    assert Path(summary["loaded_source"]) == installation / "harness"

    contract = deepcopy(project["task"])
    contract["id"] = "NEW"
    created, context = _call(
        launcher,
        _bootstrap(contract),
    )
    assert created.returncode == 0, created.stdout + created.stderr
    assert context["task"] == "NEW"
    assert Path(context["loaded_source"]) == installation / "harness"


@pytest.mark.parametrize(
    "damage",
    ["missing_worktree", "escaped_symlink", "wrong_branch", "missing_entrypoint"],
)
def test_invalid_registered_task_source_is_rejected_without_fallback(project, tmp_path, damage):
    service, installed, installation = _service(project, tmp_path)
    launcher, _ = _launcher(service, installed, "invalid-" + damage)
    started, context = _call(launcher, _bootstrap(project["task"]))
    assert started.returncode == 0, started.stdout + started.stderr
    worktree = Path(context["worktree"])

    if damage == "missing_worktree":
        shutil.rmtree(worktree)
    elif damage == "escaped_symlink":
        package = worktree / "src" / "harness"
        outside = tmp_path / "unregistered-harness"
        shutil.move(package, outside)
        package.symlink_to(outside, target_is_directory=True)
    elif damage == "wrong_branch":
        git(worktree, "branch", "-m", "unexpected-native-source")
    else:
        (worktree / "src" / "harness" / "interfaces" / "hook_transport.py").unlink()

    attempted, payload = _call(launcher, _bootstrap(None))
    assert attempted.returncode == service.settings.raw["exit_codes"]["rejected"]
    assert payload["status"] == "rejected"
    assert any(word in payload["reason"].lower() for word in ("source", "worktree", "symlink", "branch"))
    assert payload.get("loaded_source") != str(installation / "harness")


def test_two_bindings_load_different_task_sources_without_shared_mutation(project, tmp_path):
    service, installed, installation = _service(project, tmp_path)
    launcher_a, binding_a = _launcher(service, installed, "parallel-a")
    launcher_b, binding_b = _launcher(service, installed, "parallel-b")
    task_b = deepcopy(project["task"])
    task_b["id"] = "T2"

    first_a, context_a = _call(launcher_a, _bootstrap(project["task"]))
    first_b, context_b = _call(launcher_b, _bootstrap(task_b))
    assert first_a.returncode == first_b.returncode == 0
    expected_a = Path(context_a["worktree"]) / "src" / "harness"
    expected_b = Path(context_b["worktree"]) / "src" / "harness"
    assert expected_a != expected_b

    protected = [
        service.settings.path,
        service.settings.hooks_file,
        installed["definition_path"],
        binding_a["binding_path"],
        binding_b["binding_path"],
        launcher_a,
        launcher_b,
    ]
    before = {str(path): _digest(path) for path in protected}
    with ThreadPoolExecutor(max_workers=2) as pool:
        future_a = pool.submit(_call, launcher_a, _bootstrap(None))
        future_b = pool.submit(_call, launcher_b, _bootstrap(None))
        (run_a, result_a), (run_b, result_b) = future_a.result(), future_b.result()

    assert run_a.returncode == run_b.returncode == 0
    assert Path(result_a["loaded_source"]) == expected_a
    assert Path(result_b["loaded_source"]) == expected_b
    assert Path(result_a["loaded_source"]) != installation / "harness"
    assert Path(result_b["loaded_source"]) != installation / "harness"
    assert {str(path): _digest(path) for path in protected} == before


def test_read_only_and_task_cancel_recover_after_configuration_change(project, tmp_path):
    service, installed, installation = _service(project, tmp_path)
    launcher, _ = _launcher(service, installed, "recovery-session")
    started, context = _call(launcher, _bootstrap(project["task"]))
    assert started.returncode == 0, started.stdout + started.stderr

    config = json.loads(Path(project["config_path"]).read_text(encoding="utf-8"))
    config["limits"]["preview_chars"] += 1
    write_json(project["config_path"], config)
    shown, view = _call(
        launcher,
        request("show", {"queries": [{"id": "content", "kind": "content"}]}),
    )
    assert shown.returncode == 0, shown.stdout + shown.stderr
    assert view["status"] == "read_only"
    assert Path(view["loaded_source"]) == installation / "harness"

    cancelled, result = _call(
        launcher,
        request("cancel", {"reason": "Explicit test cancellation after config change"}),
    )
    assert cancelled.returncode == 0, cancelled.stdout + cancelled.stderr
    assert result["status"] == "cancelled"
    assert Path(result["loaded_source"]) == installation / "harness"


def test_sprint_cancellation_remains_independent_of_live_configuration_hash(project, tmp_path):
    sprint_setup(project)
    service, installed, installation = _service(project, tmp_path)
    launcher, binding = _launcher(service, installed, "sprint-recovery")
    runtime = service.bound_runtime(binding["binding_path"])
    tools = WorkTools(runtime)
    planned = draft(tools, [sprint_task(project, "A")])
    publish(tools, planned["revision"])

    config = json.loads(Path(project["config_path"]).read_text(encoding="utf-8"))
    config["limits"]["preview_chars"] += 1
    write_json(project["config_path"], config)
    packet = request(
        "sprint",
        {
            "action": "cancel_tasks",
            "sprint_id": "S",
            "request_id": "cancel-after-config-change",
            "tasks": ["A"],
            "mode": "single",
            "reason": "Explicit sprint cancellation regression",
        },
    )
    cancelled, result = _call(launcher, packet)
    assert cancelled.returncode == 0, cancelled.stdout + cancelled.stderr
    assert result["tasks"][0]["status"] == "cancelled"
    assert Path(result["loaded_source"]) == installation / "harness"
