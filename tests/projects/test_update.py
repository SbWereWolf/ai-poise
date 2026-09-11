import json
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import pytest

from harness.common import digest, load_config
from harness.modules.foundation.errors import HarnessError
from harness.runtime import Harness
from harness.application.work import WorkTools
from tests.conftest import write_json
from .helpers import setup_case


def update_tools(settings_path):
    """Import inside a test call so the pre-implementation suite collects and runs RED."""
    from harness.composition import project_config_tools
    return project_config_tools(settings_path)


def installed_project(project):
    from harness.composition import project_tools
    settings, _, create = setup_case(project)
    created = project_tools(settings).apply(create)
    return settings, Path(created["config_path"]), created


def project_revision(config_path):
    _, config, processes = load_config(config_path)
    return digest({"config": config, "processes": processes})


def process_change(process, instruction="Task 0033 uses the public project update API."):
    return {
        "goal_type": "development",
        "expected_revision": digest(process),
        "changes": [
            {
                "op": "patch_stage",
                "id": "tests",
                "set": {"instruction": instruction},
            }
        ],
    }


def update_request(config_path, revision, *, request_id="project-update-1",
                   process_updates=None, manifest_edits=None, state_relocation=None):
    return {
        "schema": "project-config-update-1",
        "request_id": request_id,
        "config_path": str(config_path),
        "expected_revision": revision,
        "manifest_edits": [] if manifest_edits is None else manifest_edits,
        "process_updates": [] if process_updates is None else process_updates,
        "state_relocation": state_relocation,
        "probe_repository": False,
        "receipt_path": "operations/project-update-1.json",
    }


def read_process(config_path):
    _, _, processes = load_config(config_path)
    return processes["development"]


def test_process_update_publishes_complete_candidate_and_durable_receipt(project):
    settings, config_path, created = installed_project(project)
    request = update_request(
        config_path,
        created["revision"],
        process_updates=[process_change(read_process(config_path))],
    )

    result = update_tools(settings).apply(request)

    assert result["status"] == "updated"
    assert result["changed"] is True
    assert result["prior_revision"] == created["revision"]
    assert result["revision"] == project_revision(config_path)
    assert result["replayed"] is False
    assert read_process(config_path)["stages"][0]["instruction"].startswith("Task 0033")
    receipt = project["root"] / request["receipt_path"]
    saved = json.loads(receipt.read_text())
    assert saved["request_id"] == request["request_id"]
    assert saved["request_digest"] == digest(request)
    assert saved["result"]["revision"] == result["revision"]


def test_noop_is_explicit_and_does_not_create_a_content_revision(project):
    settings, config_path, created = installed_project(project)
    request = update_request(config_path, created["revision"])

    result = update_tools(settings).apply(request)

    assert result["changed"] is False
    assert result["prior_revision"] == result["revision"] == created["revision"]
    assert Path(result["receipt_path"]).is_file()


def test_exact_retry_replays_and_conflicting_request_id_cannot_overwrite(project):
    settings, config_path, created = installed_project(project)
    api = update_tools(settings)
    request = update_request(
        config_path,
        created["revision"],
        process_updates=[process_change(read_process(config_path))],
    )
    first = api.apply(request)

    replay = update_tools(settings).apply(request)

    assert replay["replayed"] is True
    assert replay["revision"] == first["revision"]
    conflicting = deepcopy(request)
    conflicting["process_updates"][0]["changes"][0]["set"]["instruction"] = "different"
    with pytest.raises(HarnessError, match="request|запрос"):
        api.apply(conflicting)
    assert project_revision(config_path) == first["revision"]


@pytest.mark.parametrize("case", ["stale", "bad_process", "escape_receipt", "unknown_field"])
def test_stale_or_invalid_candidate_never_partially_publishes(project, case):
    settings, config_path, created = installed_project(project)
    before_manifest = config_path.read_bytes()
    process_path = config_path.parent / "config/processes/development.json"
    before_process = process_path.read_bytes()
    request = update_request(
        config_path,
        created["revision"],
        process_updates=[process_change(read_process(config_path))],
    )
    if case == "stale":
        request["expected_revision"] = "0" * 64
    elif case == "bad_process":
        request["process_updates"][0]["changes"][0]["set"] = {"transitions": {"complete": "missing"}}
    elif case == "escape_receipt":
        request["receipt_path"] = "../outside.json"
    else:
        request["implicit_default"] = True

    with pytest.raises(HarnessError):
        update_tools(settings).apply(request)

    assert config_path.read_bytes() == before_manifest
    assert process_path.read_bytes() == before_process
    assert not (project["root"].parent / "outside.json").exists()


def test_external_file_change_is_not_adopted_as_a_known_revision(project):
    settings, config_path, created = installed_project(project)
    process_path = config_path.parent / "config/processes/development.json"
    external = json.loads(process_path.read_text())
    external["stages"][0]["instruction"] = "unmanaged external edit"
    write_json(process_path, external)
    request = update_request(config_path, project_revision(config_path))

    with pytest.raises(HarnessError, match="вне|external|known"):
        update_tools(settings).apply(request)

    assert json.loads(process_path.read_text()) == external


def test_active_task_keeps_process_snapshot_and_new_task_uses_updated_process(project):
    settings, config_path, created = installed_project(project)
    active = WorkTools(Harness(config_path, "active-owner"))
    first = active.invoke({
        "operation": "bootstrap",
        "input": {"task": project["task"], "decision": None, "feedback": None, "rework_stage": None},
        "messages": [],
    })
    changed_instruction = "Updated process instruction for newly accepted work."
    request = update_request(
        config_path,
        created["revision"],
        process_updates=[process_change(read_process(config_path), changed_instruction)],
    )
    update_tools(settings).apply(request)

    same_task = active.invoke({
        "operation": "bootstrap",
        "input": {"task": None, "decision": None, "feedback": None, "rework_stage": None},
        "messages": [],
    })
    new_task = deepcopy(project["task"])
    new_task["id"] = "T2"
    fresh = WorkTools(Harness(config_path, "new-owner")).invoke({
        "operation": "bootstrap",
        "input": {"task": new_task, "decision": None, "feedback": None, "rework_stage": None},
        "messages": [],
    })

    assert same_task["instruction"] == first["instruction"]
    assert fresh["instruction"] == changed_instruction


def test_manifest_change_is_rejected_while_task_work_is_active(project):
    settings, config_path, created = installed_project(project)
    WorkTools(Harness(config_path, "active-owner")).invoke({
        "operation": "bootstrap",
        "input": {"task": project["task"], "decision": None, "feedback": None, "rework_stage": None},
        "messages": [],
    })
    request = update_request(
        config_path,
        created["revision"],
        manifest_edits=[{"path": ["git", "push_required"], "value": False}],
    )

    with pytest.raises(HarnessError, match="active|актив"):
        update_tools(settings).apply(request)

    assert json.loads(config_path.read_text())["git"]["push_required"] is True


def relocation(config_path, destination, source_disposition="delete_after_publish"):
    root, config, _ = load_config(config_path)
    source = Path(config["paths"]["state"])
    if not source.is_absolute():
        source = root / source
    return {
        "expected_source": str(source.resolve()),
        "destination": str(destination.resolve()),
        "source_disposition": source_disposition,
    }


def test_state_relocation_preserves_nonempty_state_and_switches_config(project):
    settings, config_path, created = installed_project(project)
    move = relocation(config_path, project["root"].parent / "relocated-state")
    source = Path(move["expected_source"])
    source.mkdir(parents=True)
    (source / "precious.txt").write_text("preserve me")
    request = update_request(config_path, created["revision"], state_relocation=move)

    result = update_tools(settings).apply(request)

    assert result["state_relocation"]["status"] == "completed"
    assert (Path(move["destination"]) / "precious.txt").read_text() == "preserve me"
    assert not source.exists()
    assert Path(json.loads(config_path.read_text())["paths"]["state"]) == Path(move["destination"])


def test_state_relocation_rejects_occupied_destination_and_active_work(project):
    settings, config_path, created = installed_project(project)
    destination = project["root"].parent / "occupied-state"
    destination.mkdir()
    (destination / "foreign.txt").write_text("do not replace")
    request = update_request(config_path, created["revision"], state_relocation=relocation(config_path, destination))

    with pytest.raises(HarnessError, match="destination|назначения|пуст"):
        update_tools(settings).apply(request)
    assert (destination / "foreign.txt").read_text() == "do not replace"

    empty_destination = project["root"].parent / "empty-relocation"
    WorkTools(Harness(config_path, "active-owner")).invoke({
        "operation": "bootstrap",
        "input": {"task": project["task"], "decision": None, "feedback": None, "rework_stage": None},
        "messages": [],
    })
    active_request = update_request(
        config_path,
        created["revision"],
        request_id="active-relocation",
        state_relocation=relocation(config_path, empty_destination),
    )
    active_request["receipt_path"] = "operations/active-relocation.json"
    with pytest.raises(HarnessError, match="active|quiescent|актив"):
        update_tools(settings).apply(active_request)
    assert not empty_destination.exists()


@pytest.mark.parametrize("failure_point", ["copy", "publish", "switch", "cleanup"])
def test_interrupted_relocation_recovers_on_exact_retry(project, monkeypatch, failure_point):
    import harness.infrastructure.project_config as infrastructure

    settings, config_path, created = installed_project(project)
    move = relocation(config_path, project["root"].parent / f"relocated-{failure_point}")
    source = Path(move["expected_source"])
    source.mkdir(parents=True)
    (source / "precious.txt").write_text("preserve me")
    request = update_request(config_path, created["revision"], state_relocation=move)
    seam = {
        "copy": "copy_state_tree",
        "publish": "publish_state_tree",
        "switch": "switch_project_config",
        "cleanup": "remove_source_state",
    }[failure_point]
    original = getattr(infrastructure, seam)

    def interrupt(*args, **kwargs):
        if failure_point in {"publish", "switch", "cleanup"}:
            original(*args, **kwargs)
        raise OSError(f"interrupted during {failure_point}")

    with monkeypatch.context() as patch:
        patch.setattr(infrastructure, seam, interrupt)
        with pytest.raises(HarnessError, match="retry|повтор"):
            update_tools(settings).apply(request)

    recovered = update_tools(settings).apply(request)

    assert recovered["replayed"] is True
    assert recovered["state_relocation"]["status"] == "completed"
    assert (Path(move["destination"]) / "precious.txt").read_text() == "preserve me"
    assert not source.exists()


def test_project_config_cli_accepts_one_bounded_packet(project):
    settings, config_path, created = installed_project(project)
    request = update_request(config_path, created["revision"])

    completed = subprocess.run(
        [sys.executable, "-m", "harness", "project-config", "--settings", str(settings)],
        input=json.dumps(request),
        text=True,
        capture_output=True,
    )

    assert completed.returncode == 0, completed.stderr + completed.stdout
    assert json.loads(completed.stdout)["status"] == "updated"


def test_project_update_application_boundary_depends_on_ports_not_io():
    root = Path(__file__).resolve().parents[2] / "src/harness"
    path = root / "application/project_config.py"
    text = path.read_text()

    assert "modules.projects" in text
    assert "infrastructure" not in text
    assert not any(token in text for token in ("sqlite3", "subprocess", "os.rename", "Path("))
