from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import ast
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

import pytest

from batch.helpers import request
from conftest import add_test, git, write_json
from harness.application.work import WorkTools
from harness.infrastructure.hook_transport import HookService
from hook_transport.helpers import definition, event, install, settings
from sprints.helpers import (
    draft,
    publish,
    publish_existing_contract,
    setup as sprint_setup,
    task as sprint_task,
)


SOURCE = Path(__file__).resolve().parents[2] / "src" / "harness"


def _instrumented_project_source(project):
    target = project["app"] / "src" / "harness"
    shutil.copytree(SOURCE, target)
    (target / "source_sentinel.txt").write_text("installation\n", encoding="utf-8")
    transport = target / "interfaces" / "hook_transport.py"
    tree = ast.parse(transport.read_text(encoding="utf-8"))
    inserted = 0

    class InstrumentWorkResult(ast.NodeTransformer):
        def visit_Assign(self, node):
            nonlocal inserted
            self.generic_visit(node)
            call = node.value
            if (
                isinstance(call, ast.Call)
                and isinstance(call.func, ast.Attribute)
                and isinstance(call.func.value, ast.Name)
                and call.func.value.id == "service"
                and call.func.attr == "work"
            ):
                inserted += 1
                probe = ast.parse(
                    "package_root = Path(__file__).resolve().parents[1]\n"
                    "result.update({'loaded_source': str(package_root), "
                    "'source_sentinel': (package_root / 'source_sentinel.txt').read_text(encoding='utf-8').strip()})"
                ).body
                return [node, *probe]
            return node

    tree = InstrumentWorkResult().visit(tree)
    assert inserted == 1
    transport.write_text(ast.unparse(ast.fix_missing_locations(tree)) + "\n", encoding="utf-8")
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
    path = Path(path)
    if path.is_file():
        return hashlib.sha256(path.read_bytes()).hexdigest()
    value = hashlib.sha256()
    for child in sorted(p for p in path.rglob("*") if p.is_file() and "__pycache__" not in p.parts):
        value.update(str(child.relative_to(path)).encode())
        value.update(child.read_bytes())
    return value.hexdigest()


def _result(context, report="Fixture stage verified."):
    value = deepcopy(context["result_template"])
    value["sections"]["report"] = report
    value["commit_message"] = "test: verify source routing fixture"
    return request("verify", {"result": value, "artifacts": []})


def _commit_source_probe(worktree, sentinel):
    package = Path(worktree) / "src" / "harness"
    (package / "source_sentinel.txt").write_text(sentinel + "\n", encoding="utf-8")
    git(Path(worktree), "add", "src/harness/source_sentinel.txt")
    git(Path(worktree), "commit", "-m", f"Set {sentinel} source sentinel")
    return package


def _remove_work_method(package):
    transport = package / "infrastructure" / "hook_transport.py"
    tree = ast.parse(transport.read_text(encoding="utf-8"))
    removed = 0
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "HookService":
            before = len(node.body)
            node.body = [
                item
                for item in node.body
                if not (isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name == "work")
            ]
            removed += before - len(node.body)
    assert removed == 1
    transport.write_text(ast.unparse(ast.fix_missing_locations(tree)) + "\n", encoding="utf-8")


def _inject_before_work(package, statement):
    transport = package / "interfaces" / "hook_transport.py"
    tree = ast.parse(transport.read_text(encoding="utf-8"))
    inserted = 0

    class InjectMutation(ast.NodeTransformer):
        def visit_Assign(self, node):
            nonlocal inserted
            self.generic_visit(node)
            call = node.value
            if (
                isinstance(call, ast.Call)
                and isinstance(call.func, ast.Attribute)
                and isinstance(call.func.value, ast.Name)
                and call.func.value.id == "service"
                and call.func.attr == "work"
            ):
                inserted += 1
                return [*ast.parse(statement).body, node]
            return node

    tree = InjectMutation().visit(tree)
    assert inserted == 1
    transport.write_text(ast.unparse(ast.fix_missing_locations(tree)) + "\n", encoding="utf-8")


def test_existing_task_bootstrap_loads_registered_task_source_without_rebinding(project, tmp_path):
    sprint_setup(project)
    service, installed, installation = _service(project, tmp_path)
    launcher, binding = _launcher(service, installed, "task-session")
    runtime = service.bound_runtime(binding["binding_path"])
    publish_existing_contract(runtime, sprint_task(project, "T1"), "S")
    prompt = event("UserPromptSubmit", session="task-session", turn="task-turn")
    prompt["cwd"] = str(service.settings.root)
    service.event(installed["definition_path"], prompt)

    started, context = _call(launcher, _bootstrap({"id": "T1"}))
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


@pytest.mark.parametrize("state", ["active", "verified", "accepted"])
def test_assigned_session_continuation_loads_current_task_source_in_each_resumable_state(
    project, tmp_path, state
):
    service, installed, _ = _service(project, tmp_path)
    launcher, binding = _launcher(service, installed, "continuation-" + state)
    started, context = _call(launcher, _bootstrap(project["task"]))
    assert started.returncode == 0, started.stdout + started.stderr
    task_source = Path(context["worktree"]) / "src" / "harness"

    if state in {"verified", "accepted"}:
        add_test(context["worktree"])
        checked, report = _call(launcher, _result(context))
        assert checked.returncode == 0, checked.stdout + checked.stderr
        assert report["status"] == "verified"
    if state == "accepted":
        accepted_call, report = _call(launcher, request("accept", {}))
        assert accepted_call.returncode == 0, accepted_call.stdout + accepted_call.stderr
        assert report["status"] == "accepted"

    continued, current = _call(launcher, _bootstrap(None))
    assert continued.returncode == 0, continued.stdout + continued.stderr
    assert current["status"] == state
    assert Path(current["loaded_source"]) == task_source
    assert current["session"] == binding["session_id"]


def test_taskless_and_new_task_creation_use_configured_installation_source(project, tmp_path):
    project["cfg"]["task_ids"] = {
        "namespace": {"minimum": 1, "maximum": 99},
        "width": 4,
        "progression": {"first": 1, "step": 1},
    }
    service, installed, installation = _service(project, tmp_path)
    launcher, _ = _launcher(service, installed, "creation-session")

    read, summary = _call(launcher, _bootstrap(None))
    assert read.returncode == 0, read.stdout + read.stderr
    assert summary["status"] == "read_only"
    assert Path(summary["loaded_source"]) == installation / "harness"

    contract = deepcopy(project["task"])
    del contract["id"]
    created, context = _call(
        launcher,
        _bootstrap({"request_id": "automatic-new-task", "task": contract}),
    )
    assert created.returncode == 0, created.stdout + created.stderr
    assert context["task"] == "0001"
    assert context["allocation"] == {
        "request_id": "automatic-new-task",
        "task_id": "0001",
        "replayed": False,
    }
    assert Path(context["loaded_source"]) == installation / "harness"


@pytest.mark.parametrize(
    "damage",
    [
        "missing_worktree",
        "escaped_symlink",
        "intermediate_symlink",
        "wrong_branch",
        "wrong_git_root",
        "missing_package",
        "missing_work_method",
    ],
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
    elif damage == "intermediate_symlink":
        source = worktree / "src"
        outside = tmp_path / "unregistered-source"
        shutil.move(source, outside)
        source.symlink_to(outside, target_is_directory=True)
    elif damage == "wrong_branch":
        git(worktree, "branch", "-m", "unexpected-native-source")
    elif damage == "wrong_git_root":
        shutil.rmtree(worktree)
        worktree.mkdir()
        git(worktree, "init", "-b", "tasks/T1")
        git(worktree, "config", "user.name", "Fixture")
        git(worktree, "config", "user.email", "fixture@example.invalid")
        shutil.copytree(installation / "harness", worktree / "src" / "harness")
        git(worktree, "add", ".")
        git(worktree, "commit", "-m", "Create unrelated repository")
    elif damage == "missing_package":
        shutil.rmtree(worktree / "src" / "harness")
    else:
        _remove_work_method(worktree / "src" / "harness")

    attempted, payload = _call(launcher, _bootstrap(None))
    assert attempted.returncode == service.settings.raw["exit_codes"]["rejected"]
    assert payload["status"] == "rejected"
    assert any(word in payload["reason"].lower() for word in ("source", "worktree", "symlink", "branch"))
    assert payload.get("loaded_source") != str(installation / "harness")


def test_existing_task_rejects_an_unregistered_lookalike_worktree(project, tmp_path):
    service, installed, installation = _service(project, tmp_path)
    launcher, binding = _launcher(service, installed, "unregistered-task")
    runtime = service.bound_runtime(binding["binding_path"])
    lookalike = runtime.state / runtime.paths["worktrees"] / "GHOST" / "src" / "harness"
    shutil.copytree(installation / "harness", lookalike)

    attempted, payload = _call(launcher, _bootstrap({"id": "GHOST"}))

    assert attempted.returncode == service.settings.raw["exit_codes"]["rejected"]
    assert payload["status"] == "rejected"
    assert "task" in payload["reason"].lower()
    assert payload.get("loaded_source") != str(lookalike)


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
    _commit_source_probe(context_a["worktree"], "task-T1")
    _commit_source_probe(context_b["worktree"], "task-T2")

    protected = [
        service.settings.path,
        service.settings.hooks_file,
        installed["definition_path"],
        binding_a["binding_path"],
        binding_b["binding_path"],
        launcher_a,
        launcher_b,
        expected_a,
        expected_b,
    ]
    before = {str(path): _digest(path) for path in protected}
    with ThreadPoolExecutor(max_workers=2) as pool:
        future_a = pool.submit(_call, launcher_a, _bootstrap(None))
        future_b = pool.submit(_call, launcher_b, _bootstrap(None))
        (run_a, result_a), (run_b, result_b) = future_a.result(), future_b.result()

    assert run_a.returncode == run_b.returncode == 0
    assert Path(result_a["loaded_source"]) == expected_a
    assert Path(result_b["loaded_source"]) == expected_b
    assert result_a["source_sentinel"] == "task-T1"
    assert result_b["source_sentinel"] == "task-T2"
    assert Path(result_a["loaded_source"]) != installation / "harness"
    assert Path(result_b["loaded_source"]) != installation / "harness"
    assert {str(path): _digest(path) for path in protected} == before


@pytest.mark.parametrize("mutation", ["binding", "source", "git"])
def test_child_revalidates_bound_source_facts_after_parent_selection(project, tmp_path, mutation):
    service, installed, installation = _service(project, tmp_path)
    launcher, binding = _launcher(service, installed, "toctou-" + mutation)
    started, context = _call(launcher, _bootstrap(project["task"]))
    assert started.returncode == 0, started.stdout + started.stderr
    worktree = Path(context["worktree"])
    package = worktree / "src" / "harness"
    statements = {
        "binding": "Path(args.binding).write_text('{}', encoding='utf-8')",
        "source": "(Path(__file__).resolve().parents[1] / '__main__.py').unlink()",
        "git": (
            "__import__('subprocess').check_call(["
            "'git', '-C', str(Path(__file__).resolve().parents[3]), "
            "'branch', '-m', 'toctou-mutated'])"
        ),
    }
    _inject_before_work(package, statements[mutation])
    git(worktree, "add", "src/harness/interfaces/hook_transport.py")
    git(worktree, "commit", "-m", f"Inject {mutation} TOCTOU fixture")

    attempted, payload = _call(launcher, _bootstrap(None))

    assert attempted.returncode == service.settings.raw["exit_codes"]["rejected"]
    assert payload["status"] == "rejected"
    assert any(word in payload["reason"].lower() for word in ("binding", "source", "worktree", "branch"))
    assert payload.get("loaded_source") != str(installation / "harness")


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


@pytest.mark.parametrize("action", ["cancel_tasks", "force_close"])
def test_sprint_cancellation_remains_independent_of_live_configuration_hash(project, tmp_path, action):
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
    value = {
        "action": action,
        "sprint_id": "S",
        "request_id": action + "-after-config-change",
        "reason": "Explicit sprint cancellation regression",
    }
    if action == "cancel_tasks":
        value.update(tasks=["A"], mode="single")
    packet = request("sprint", value)
    cancelled, result = _call(launcher, packet)
    assert cancelled.returncode == 0, cancelled.stdout + cancelled.stderr
    if action == "cancel_tasks":
        assert result["tasks"][0]["status"] == "cancelled"
    else:
        assert result["status"] == "cancelled"
    assert Path(result["loaded_source"]) == installation / "harness"
