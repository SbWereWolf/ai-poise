"""Disposable current native producers; no historical process synthesis."""
import hashlib
import json
from pathlib import Path

from poise.composition import project_config_tools, project_tools
from poise.infrastructure.project_config import FileProjectConfigUpdate
from poise.infrastructure.projects import ProjectSettings
from tests.conftest import git, write_json
from tests.projects.helpers import setup_case
from tests.projects.test_update import workflow_change, read_workflow, update_request


def native_receipt(project, producer, enabled):
    settings, _, request = setup_case(project)
    request["probe_repository"] = enabled if producer == "setup" else False
    created = project_tools(settings).apply(request)
    assert created["status"] == "created"
    config = Path(created["config_path"])
    setup_receipt = config.parent / "setup-receipt.json"
    if producer == "setup":
        result = created
        receipt = setup_receipt
    else:
        packet = update_request(
            config, created["revision"], probe_repository=enabled,
            workflow_updates=[workflow_change(read_workflow(config), "Verified receipt source.")],
        )
        result = project_config_tools(settings).apply(packet)
        assert result["status"] == "updated"
        assert result["changed"] is True
        receipt = Path(result["receipt_path"])
    raw = receipt.read_bytes()
    return {
        "owner": FileProjectConfigUpdate(ProjectSettings(settings)),
        "config": config, "receipt": receipt, "raw": raw,
        "document": json.loads(raw), "result": result,
        "revision": result["revision"], "setup_receipt": setup_receipt,
        "commit": git(project["app"], "rev-parse", "HEAD").strip(),
        "root": project["root"],
    }


def assert_readiness(readiness, enabled, commit):
    if enabled:
        assert set(readiness) == {"repository", "remote", "base_revision"}
        assert readiness["repository"] == "verified"
        assert readiness["remote"] == "not_required"
        assert readiness["base_revision"] == commit
    else:
        assert set(readiness) == {"repository", "remote"}
        assert readiness["repository"] == "not_checked"
        assert readiness["remote"] == "not_checked"


def files(case):
    """Observe every disposable project file, including any unexpected effects."""
    return {
        str(p.relative_to(case["root"])): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in case["root"].rglob("*") if p.is_file()
    }


def read_receipt(case, producer):
    owner = case["owner"]
    if producer == "setup":
        owner.stage_criteria_binding(case["config"])
    else:
        owner._stage_criteria_update_receipt(
            json.loads(case["receipt"].read_bytes()), case["receipt"],
        )
    return owner.stage_criteria_source(case["config"], case["revision"])


def negative_copy(case, change):
    """Retain authentic positive bytes; modify only this test's negative copy."""
    original = case["receipt"].with_suffix(".original")
    original.write_bytes(case["raw"])
    document = json.loads(case["raw"])
    change(document)
    write_json(case["receipt"], document)
    assert original.read_bytes() == case["raw"]
    return original
