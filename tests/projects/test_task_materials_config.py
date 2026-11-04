"""CD-01: named optional path publication through the real configuration owner."""
import json
from copy import deepcopy
from pathlib import Path

import pytest

from poise.modules.foundation.errors import PoiseError, VersionConflict
from .test_update import installed_project, update_request, update_tools


CONTRACT = json.loads(
    (Path(__file__).parent / "fixtures/task_materials_config.json").read_text()
)


def observed_files(config_path):
    """Capture complete manifest/workflow bytes without a production serializer."""
    manifest = json.loads(config_path.read_text())
    return {
        path: path.read_bytes()
        for path in [config_path, *(
            config_path.parent / name for name in manifest["workflows"].values()
        )]
    }


def assert_no_receipt(project, packet):
    receipt = project["root"] / packet["receipt_path"]
    assert not receipt.exists()
    assert not receipt.with_name(receipt.name + ".pending").exists()


def test_named_material_path_publication_replay_and_revision_contract(project):
    settings, config_path, created = installed_project(project)
    before = observed_files(config_path)
    original = json.loads(config_path.read_text())
    assert "task_materials" not in original["paths"]
    expected = deepcopy(original)
    expected["paths"]["task_materials"] = "user-materials"
    packet = update_request(
        config_path, created["revision"], request_id="materials-first",
        manifest_edits=[{"path": CONTRACT["path"], "value": CONTRACT["value"]}],
    )

    result = update_tools(settings).apply(packet)

    assert result["status"] == "updated"
    assert result["changed"] is True
    assert result["replayed"] is False
    assert result["prior_revision"] == created["revision"]
    assert result["revision"] != created["revision"]
    assert result["config_path"] == str(config_path)
    assert json.loads(config_path.read_text()) == expected
    assert {p: p.read_bytes() for p in before if p != config_path} == {
        p: data for p, data in before.items() if p != config_path
    }
    receipt_path = project["root"] / packet["receipt_path"]
    assert Path(result["receipt_path"]) == receipt_path
    receipt_bytes = receipt_path.read_bytes()
    receipt = json.loads(receipt_bytes)
    assert receipt["schema"] == "project-config-receipt-1"
    assert receipt["request_id"] == "materials-first"
    assert receipt["result"] == result
    assert not receipt_path.with_name(receipt_path.name + ".pending").exists()
    published = observed_files(config_path)

    # New composed owner must replay before rejecting the now-stale original revision.
    assert update_tools(settings).apply(deepcopy(packet)) == {**result, "replayed": True}
    assert observed_files(config_path) == published
    assert receipt_path.read_bytes() == receipt_bytes

    conflict = deepcopy(packet)
    conflict["manifest_edits"][0]["value"] = "other-materials"
    with pytest.raises(VersionConflict, match="Different request"):
        update_tools(settings).apply(conflict)
    assert observed_files(config_path) == published
    assert receipt_path.read_bytes() == receipt_bytes

    stale = deepcopy(packet)
    stale["request_id"] = "materials-stale"
    stale["receipt_path"] = "operations/materials-stale.json"
    with pytest.raises(VersionConflict, match="expected known revision"):
        update_tools(settings).apply(stale)
    assert observed_files(config_path) == published
    assert receipt_path.read_bytes() == receipt_bytes
    assert_no_receipt(project, stale)


@pytest.mark.parametrize("case", CONTRACT["invalid"], ids=lambda case: case["id"])
def test_invalid_known_material_path_is_attributed_without_publication(project, case):
    settings, config_path, created = installed_project(project)
    before = observed_files(config_path)
    packet = update_request(
        config_path, created["revision"],
        manifest_edits=[{"path": CONTRACT["path"], "value": case["value"]}],
    )

    with pytest.raises(PoiseError, match=r"paths\.task_materials:.*непустой строковый путь без NUL"):
        update_tools(settings).apply(packet)

    assert observed_files(config_path) == before
    assert_no_receipt(project, packet)


@pytest.mark.parametrize("case", CONTRACT["protected"], ids=lambda case: case["id"])
def test_guard_other_paths_remain_protected_without_publication(project, case):
    settings, config_path, created = installed_project(project)
    before = observed_files(config_path)
    packet = update_request(
        config_path, created["revision"],
        manifest_edits=[{"path": case["path"], "value": case["value"]}],
    )

    with pytest.raises(PoiseError, match="Manifest field paths has a dedicated owner"):
        update_tools(settings).apply(packet)

    assert observed_files(config_path) == before
    assert_no_receipt(project, packet)


def test_guard_absent_material_path_keeps_existing_noop_operation(project):
    settings, config_path, created = installed_project(project)
    before = observed_files(config_path)
    assert "task_materials" not in json.loads(config_path.read_text())["paths"]

    result = update_tools(settings).apply(update_request(config_path, created["revision"]))

    assert result["status"] == "updated"
    assert result["changed"] is False
    assert result["revision"] == created["revision"]
    assert observed_files(config_path) == before
