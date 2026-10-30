"""The obsolete global artifact lock is inert data, not a runtime input."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from conftest import WorkPoise, write_json
from poise.application.work import WorkTools
from poise.common import load_config
from tests.projects.test_update import installed_project, update_request, update_tools
from .helpers import configure, request, text_artifact
from .test_independent_material_operations import publish, start_task


def test_artifact_lock_option_is_optional_unknown_data(project):
    configure(project)
    project["cfg"]["batch"].pop("artifact_lock", None)
    write_json(project["config_path"], project["cfg"])
    tools, _ = start_task(project, "T1", "no-artifact-lock")
    assert publish(tools)["status"] == "artifacts_created"
    assert not (tools.runtime.state / "artifact.lock").exists()


def test_obsolete_artifact_lock_value_survives_managed_editor(project):
    # Unknown raw values need not have the old known-option type. A dict cannot
    # accidentally resolve as a file path, so a successful operation proves it inert.
    project["cfg"]["batch"]["artifact_lock"] = {"operator-note": "obsolete"}
    settings, path, created = installed_project(project)
    before = json.loads(path.read_text())
    assert before["batch"]["artifact_lock"] == {"operator-note": "obsolete"}
    packet = update_request(path, created["revision"], manifest_edits=[
        {"path": ["git", "remote"], "value": "edited"},
    ])
    update_tools(settings).apply(packet)
    expected = deepcopy(before)
    expected["git"]["remote"] = "edited"
    assert json.loads(path.read_text()) == expected
    assert load_config(path)[1] == expected
    client = WorkTools(WorkPoise(path, "unknown-lock-observer"))
    assert client.invoke(request("artifacts", {"items": [
        text_artifact(scope="runtime", path="independent.txt", text="B material\n"),
    ]}))["status"] == "artifacts_created"


@pytest.mark.parametrize("relative", [
    "config/batch.example.json", "config/project.example.json",
    "config/project-templates/common.json",
])
def test_sanitized_examples_offer_no_artifact_lock(relative):
    data = json.loads((Path(__file__).resolve().parents[2] / relative).read_text())
    if relative == "config/batch.example.json":
        batch = data
    elif relative.startswith("config/project-templates/"):
        batch = data["config"]["batch"]
    else:
        batch = data["batch"]
    assert "artifact_lock" not in batch
