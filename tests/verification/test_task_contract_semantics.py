"""Task 0114: schema-explicit empty obligations and baseline guards."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory

import pytest


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from poise.application.work import WorkTools
from poise.infrastructure.clock import SystemClock
from poise.modules.foundation.errors import DomainError, PoiseError
from poise.modules.tasks.definition import validate_creation
from poise.runtime import Poise


EXECUTABLE_OBLIGATIONS = {
    "0001": [
        *(f"requirements[{index}]" for index in range(10)),
        "definition_of_done[0]",
        "definition_of_done[2]",
        "definition_of_done[3]",
    ],
    "0002": [
        *(f"requirements[{index}]" for index in (1, 2, 3, 4, 5, 6, 8)),
        "definition_of_done[0]",
    ],
}


def _process(goal_type: str) -> dict:
    return json.loads(
        (ROOT / "config" / "catalogue" / "process-templates" / f"{goal_type}.json")
        .read_text(encoding="utf-8")
    )


def _stage_contracts(process: dict, task: dict) -> list[dict]:
    requirements = process["content_contract"]["requirements"] + task["content_contract"]["requirements"]
    return [
        {
            "stage_id": stage["id"],
            "allowed_paths": list(stage["allowed_paths"]),
            "entry_requirements": [
                item["id"]
                for item in requirements
                if item["phase"] == "pre" and stage["id"] in item["stages"]
            ],
            "exit_requirements": [
                item["id"]
                for item in requirements
                if item["phase"] == "post" and stage["id"] in item["stages"]
            ],
        }
        for stage in process["stages"]
    ]


def _baseline_guard(task: dict) -> dict:
    selected = deepcopy(task)
    baseline = next(method for method in selected["methods"] if method["id"] == "BASELINE")
    baseline["verification_plan"] = {
        "responsibility": "Guard the pre-existing repository baseline before produced changes.",
        "change_surface": [],
        "red_stages": [],
        "green_stages": ["baseline"],
        "red_failure": None,
    }
    selected["checks"]["baseline"] = ["BASELINE"]
    selected["checks"]["test_inspection"] = []
    return selected


def _git_repo(path: Path) -> None:
    path.mkdir()
    subprocess.run(["git", "init", "-b", "main", str(path)], check=True, capture_output=True)
    (path / "README.md").write_text("seed test\n", encoding="utf-8")
    for relative in ("tests/projects", "tests/hook_transport", "tests/runtime_services"):
        target = path / relative
        target.mkdir(parents=True)
        (target / "test_seed.py").write_text("def test_seed(): pass\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(path), "add", "."], check=True)
    subprocess.run(
        [
            "git", "-C", str(path), "-c", "user.name=Task 0114",
            "-c", "user.email=task-0114@example.invalid", "commit", "-m", "baseline",
        ],
        check=True,
        capture_output=True,
    )


def _project(root: Path, name: str) -> Path:
    blueprint = json.loads(
        (ROOT / "config/project-templates/wsl-poise.json").read_text(encoding="utf-8")
    )
    home = root / name / "config"
    home.mkdir(parents=True)
    config = blueprint["config"]
    config["project"] = name
    repository = root / name / "repository"
    _git_repo(repository)
    config["git"].update(
        repository=str(repository),
        base_ref="main",
        remote="origin",
        author_name="Task 0114",
        author_email="task-0114@example.invalid",
        push_required=False,
    )
    config["paths"]["state"] = str((root / name / "state").resolve())
    config["task_ids"] = {
        "namespace": {"minimum": 1, "maximum": 9999},
        "width": 4,
        "progression": {"first": 1, "step": 1},
    }
    for goal_type, relative in config["processes"].items():
        source = ROOT / blueprint["process_sources"][goal_type]["path"]
        target = home / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.read_bytes())
    path = home / "project.json"
    path.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def _task(task_id: str) -> dict:
    return json.loads(
        (ROOT / "delivery/task-definitions/harness" / f"{task_id}.json").read_text(
            encoding="utf-8"
        )
    )


def _automatic(task: dict, request_id: str) -> dict:
    selected = deepcopy(task)
    selected.pop("id")
    return {
        "operation": "bootstrap",
        "input": {
            "task": {"request_id": request_id, "task": selected},
            "decision": None,
            "feedback": None,
            "rework_stage": None,
        },
        "messages": [],
    }


def _accept(call, expected: str) -> dict:
    try:
        return call()
    except (DomainError, PoiseError) as error:
        if expected not in str(error):
            raise
        raise AssertionError(str(error)) from error


def _reject(call, expected: str) -> None:
    try:
        call()
    except (DomainError, PoiseError) as error:
        assert expected in str(error), str(error)
        return
    raise AssertionError("Invalid produced-result method with an empty surface was accepted")


def case_baseline_guard_domain(_root: Path) -> None:
    process = _process("development")
    task = _baseline_guard(_task("0001"))
    task["executable_obligations"] = EXECUTABLE_OBLIGATIONS["0001"]
    task["stage_contracts"] = _stage_contracts(process, task)
    validated = _accept(
        lambda: validate_creation(task, process, []),
        "change_surface",
    )
    baseline = next(method for method in validated["contract"]["methods"] if method["id"] == "BASELINE")
    assert baseline["verification_plan"]["change_surface"] == []
    assert validated["contract"]["checks"]["baseline"] == ["BASELINE"]

    produced = deepcopy(task)
    produced_baseline = next(
        method for method in produced["methods"] if method["id"] == "BASELINE"
    )
    produced_baseline["verification_plan"]["green_stages"] = ["implementation"]
    produced["checks"]["baseline"] = []
    produced["checks"]["implementation"] = ["BASELINE"]
    _reject(
        lambda: validate_creation(produced, process, []),
        "route entry",
    )


def case_public_creation_and_seed(root: Path) -> None:
    documentation = _task("0003")
    documentation_process = _process("documentation")
    documentation["sprint_id"] = None
    documentation["stage_contracts"] = _stage_contracts(documentation_process, documentation)
    assert "executable_obligations" not in documentation
    documentation_tools = WorkTools(
        Poise(_project(root, "documentation"), "task-0114-doc", SystemClock())
    )
    created = documentation_tools.invoke(_automatic(documentation, "task-0114-documentation"))
    assert created["status"] == "active"
    registry = documentation_tools.invoke(
        {
            "operation": "show",
            "input": {"queries": [{"id": "registry", "kind": "verification_registry"}]},
            "messages": [],
        }
    )["results"][0]["value"]
    assert registry["executable_obligations"] == []

    seed_config = _project(root, "seed")
    seeded = subprocess.run(
        [sys.executable, str(ROOT / "tools/seed_wsl_tasks.py"), "--poise-config", str(seed_config)],
        env={"PYTHONPATH": str(ROOT / "src")},
        capture_output=True,
        text=True,
    )
    assert seeded.returncode == 0, seeded.stdout + seeded.stderr
    assert json.loads(seeded.stdout)["status"] == "seeded"

    inspector = Poise(seed_config, "task-0114-seed-inspector", SystemClock())
    documentation_record = inspector.task_queries.record("0003")
    documentation_registry = inspector.task_queries.verification_registry("0003")
    assert "executable_obligations" not in documentation_record["contract"]
    assert documentation_registry["executable_obligations"] == []
    for task_id in ("0001", "0002"):
        record = inspector.task_queries.record(task_id)
        registry = inspector.task_queries.verification_registry(task_id)
        baseline = next(
            item["method"]
            for item in registry["current"]
            if item["method"]["id"] == "BASELINE"
        )
        assert record["contract"]["checks"]["baseline"] == ["BASELINE"]
        assert baseline["verification_plan"]["change_surface"] == []
        assert baseline["verification_plan"]["green_stages"] == ["baseline"]
        assert registry["executable_obligations"] == EXECUTABLE_OBLIGATIONS[task_id]

    development = _baseline_guard(_task("0001"))
    development_process = _process("development")
    development["sprint_id"] = None
    development["executable_obligations"] = EXECUTABLE_OBLIGATIONS["0001"]
    development["stage_contracts"] = _stage_contracts(development_process, development)
    development_tools = WorkTools(
        Poise(_project(root, "development"), "task-0114-dev", SystemClock())
    )
    created = development_tools.invoke(_automatic(development, "task-0114-development"))
    assert created["status"] == "active"
    public_registry = development_tools.invoke(
        {
            "operation": "show",
            "input": {"queries": [{"id": "registry", "kind": "verification_registry"}]},
            "messages": [],
        }
    )["results"][0]["value"]
    assert public_registry["executable_obligations"] == EXECUTABLE_OBLIGATIONS["0001"]


def test_baseline_guard_domain_contract(tmp_path: Path) -> None:
    case_baseline_guard_domain(tmp_path)


def test_public_creation_and_seed_publication(tmp_path: Path) -> None:
    case_public_creation_and_seed(tmp_path)


CASES = (case_baseline_guard_domain, case_public_creation_and_seed)


if __name__ == "__main__":
    if sys.argv[1:] != ["regression"]:
        raise SystemExit("usage: test_task_contract_semantics.py regression")
    failures = 0
    with TemporaryDirectory(prefix="task-0114-") as temporary:
        root = Path(temporary)
        for index, case in enumerate(CASES):
            try:
                case(root / str(index))
            except AssertionError:
                failures += 1
    if failures:
        print(f"FAILED {failures}/{len(CASES)}")
        raise SystemExit(1)
    print(f"PASSED {len(CASES)}/{len(CASES)}")
