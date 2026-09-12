from __future__ import annotations

from copy import deepcopy
import os
from pathlib import Path
import subprocess
import sys

from poise.application.work import WorkTools
from conftest import WorkPoise as Poise

from conftest import git, write_json


def request(operation, inputs):
    return {"operation": operation, "input": inputs, "messages": []}


def prepare_completed_task(
    project,
    change,
    *,
    task_id="T1",
    accept=True,
    methods=(),
    checks=(),
):
    methods = [deepcopy(method) for method in methods]
    for method in methods:
        method.setdefault("verification_plan", {
            "responsibility": "Verify the source result before integration.",
            "change_surface": ["src/**"],
            "red_stages": [],
            "green_stages": ["implementation"],
            "red_failure": None,
        })
    cfg = deepcopy(project["cfg"])
    cfg["automatic_checks"] = []
    cfg["git"]["push_required"] = False
    write_json(project["config_path"], cfg)
    process = {
        "route": {"entry": "implementation"},
        "goal_type": "development",
        "stages": [{
            "id": "implementation",
            "instruction": "Create the accepted result.",
            "read_only": False,
            "allowed_paths": ["src/**"],
            "normalization": "strip",
            "sections": {"report": "Record the result."},
            "required_sections": ["report"],
            "artifact_requirements": [],
            "handler": "produce",
            "transitions": {"complete": None},
            "rework_targets": ["implementation"],
        }],
        "benefit": {"git_categories": ["code"], "sections": []},
        "content_contract": {"sections": [], "routes": [], "requirements": []},
    }
    write_json(project["root"] / "config/processes/development.json", process)
    task = {
        "id": task_id,
        "sprint_id": None,
        "goal_type": "development",
        "goal": "Create an accepted source commit.",
        "requirements": ["The source commit is preserved until integration."],
        "definition_of_done": ["The completed result can be integrated."],
        "methods": list(methods),
        "method_inputs": [{
            "method_id": method["id"],
            "repository_inputs": [],
            "future_outputs": [],
            "reference_profile": {
                "runner": "python",
                "parser": "inline-no-path-arguments",
                "version": 1,
            },
        } for method in methods],
        "artifact_requirements": [],
        "checks": {"implementation": list(checks)},
        "evidence_plan": {
            "implementation": {"subject_methods": {}, "arguments": [], "review_arguments": []}
        },
        "content_contract": {"sections": [], "routes": [], "requirements": []},
    }
    runtime = Poise(project["config_path"], "worker")
    tools = WorkTools(runtime)
    context = tools.invoke(request("bootstrap", {
        "task": task,
        "decision": None,
        "feedback": None,
        "rework_stage": None,
    }))
    source_worktree = Path(context["worktree"])
    change(source_worktree)
    result = deepcopy(context["result_template"])
    result["sections"]["report"] = "Accepted source result."
    result["commit_message"] = "feat: accepted source result"
    verified = tools.invoke(request("verify", {"result": result, "artifacts": []}))
    assert verified["status"] == "verified"
    if accept:
        completed = tools.invoke(request("accept", {}))
        assert completed["status"] == "completed"
    return tools, source_worktree, verified["commit"]


def integration_input(
    project, source_commit, request_id="integrate-1", resolutions=(), task_id="T1"
):
    return {
        "request_id": request_id,
        "task_id": task_id,
        "expected_source_commit": source_commit,
        "expected_target_commit": git(project["app"], "rev-parse", "refs/heads/main"),
        "authorization": "The user accepted the completed task result.",
        "resolutions": list(resolutions),
    }


def optional_ref(root: Path, ref: str) -> str | None:
    result = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "--verify", "--quiet", ref],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode in (0, 1)
    return result.stdout.strip() if result.returncode == 0 else None


def integration_guard_method() -> dict:
    return {
        "id": "INTEGRATION_GUARD",
        "argv": [
            sys.executable,
            "-c",
            (
                "import pathlib,subprocess;"
                "print('cwd='+str(pathlib.Path.cwd()));"
                "print('branch='+subprocess.check_output("
                "['git','symbolic-ref','--short','HEAD'],text=True).strip());"
                "print('head='+subprocess.check_output("
                "['git','rev-parse','HEAD'],text=True).strip())"
            ),
        ],
        "cwd": ".",
        "environment": {},
        "source_under_test": {
            "kind": "repository",
            "bindings": [{"kind": "cwd", "path": "."}],
        },
        "expected_exit_code": 0,
        "stdout_contains": ["cwd=", "branch=", "head="],
        "stderr_contains": [],
    }


def source_change(tree: Path) -> None:
    (tree / "src" / "feature.py").write_text("VALUE = 1\n")


def conflicting_source_change(tree: Path) -> None:
    (tree / "src" / "double.py").write_text("VALUE = 'source'\n")


def advance_ref_with_same_tree(root: Path, parent: str, message: str) -> str:
    tree = git(root, "rev-parse", f"{parent}^{{tree}}")
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "Drift fixture",
        "GIT_AUTHOR_EMAIL": "drift@example.invalid",
        "GIT_COMMITTER_NAME": "Drift fixture",
        "GIT_COMMITTER_EMAIL": "drift@example.invalid",
    }
    commit = subprocess.check_output(
        ["git", "-C", str(root), "commit-tree", tree, "-p", parent, "-m", message],
        text=True,
        env=env,
    ).strip()
    git(root, "update-ref", "refs/heads/main", commit, parent)
    return commit
