"""Current real producers and separately authored historical persistence.

Historical setup/update/bootstrap documents are test-owned literals, not receipts
emitted by a modern producer for old-schema processes. Only the later migration
and replay execute that old input through the real public migration owner.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

from poise.composition import project_config_tools, project_tools
from poise.infrastructure.project_config import FileProjectConfigUpdate
from poise.infrastructure.projects import ProjectSettings
from tests.conftest import write_json
from tests.projects.helpers import setup_case
from tests.projects.test_update import workflow_change, read_workflow, update_request
from .project_criteria_helpers import historical_project_owned
from .stage_criteria_helpers import identity


ROOT = Path(__file__).resolve().parents[2]
DATA = json.loads((ROOT / "fixtures/stage-criteria/project-bootstrap-source.json").read_text())


def literal_receipt(config, before, after, request, *, replayed=False):
    document = deepcopy(DATA["receipt"])
    document.update(request_id=request["request_id"], request_digest=identity(request))
    document["result"].update(
        request_id=request["request_id"], config_path=str(config),
        prior_revision=before, before_revision=before,
        revision=after, after_revision=after,
        replayed=replayed,
    )
    return document


def native_bootstrap(project, *, prior_update=False, request_id="native-bootstrap-source",
                     durable_retry=False, monkeypatch=None):
    arranged = deepcopy(project)
    arranged["cfg"].pop("runtime_resources", None)
    settings, _, create = setup_case(arranged)
    created = project_tools(settings).apply(create)
    config = Path(created["config_path"])
    before = created["revision"]
    if prior_update:
        update = update_request(config, before, workflow_updates=[
            workflow_change(read_workflow(config), "Native instruction before association.")])
        update["receipt_path"] = "operations/10-before-bootstrap.json"
        before = project_config_tools(settings).apply(update)["revision"]
        assert before != created["revision"]
    request = {
        "schema": "project-runtime-resources-bootstrap-1",
        "request_id": request_id, "config_path": str(config),
        "expected_revision": before,
        "runtime_resources": {"project_setup": str(settings), "hooks": []},
        "receipt_path": "operations/00-bootstrap.json",
    }
    before_config = json.loads(config.read_text())
    documents = {goal: json.loads((config.parent / path).read_text())
                 for goal, path in before_config["workflows"].items()}
    workflow_bytes = {path: (config.parent / path).read_bytes()
                     for path in before_config["workflows"].values()}
    assert "runtime_resources" not in before_config
    assert before == identity({"config": before_config, "workflows": documents})
    receipt = settings.parent / request["receipt_path"]
    pending = receipt.with_name(receipt.name + ".pending")
    if durable_retry:
        from poise.infrastructure import project_config
        from poise.common import PoiseError
        assert monkeypatch is not None
        original = project_config.atomic_write
        writes = []
        def interrupt(path, *args, **kwargs):
            if Path(path) == receipt:
                writes.append(str(path))
                raise OSError("Test interruption before first durable bootstrap receipt")
            return original(path, *args, **kwargs)
        with monkeypatch.context() as scoped:
            scoped.setattr(project_config, "atomic_write", interrupt)
            try:
                project_config_tools(settings).apply(request)
            except PoiseError as failure:
                assert "Bootstrap publication interrupted" in str(failure)
            else:
                raise AssertionError("Native first receipt boundary was not interrupted")
        assert writes == [str(receipt)]
        assert pending.is_file() and not receipt.exists()
        assert json.loads(config.read_text()) == {**before_config, "runtime_resources": request["runtime_resources"]}
        durable_intent = json.loads(pending.read_text())
    result = project_config_tools(settings).apply(request)
    after_config = json.loads(config.read_text())
    assert after_config == {**before_config, "runtime_resources": request["runtime_resources"]}
    assert all((config.parent / path).read_bytes() == content for path, content in workflow_bytes.items())
    expected_revision = identity({"config": after_config, "workflows": documents})
    assert result["revision"] == result["after_revision"] == expected_revision
    assert result["prior_revision"] == result["before_revision"] == before
    raw = receipt.read_bytes()
    assert json.loads(raw) == literal_receipt(config, before, result["revision"], request,
                                             replayed=durable_retry)
    if durable_retry:
        assert result["replayed"] is True and not pending.exists()
        assert result["after_revision"] == durable_intent["after_revision"]
    assert result["before_revision"] != result["after_revision"]
    return {"owner": FileProjectConfigUpdate(ProjectSettings(settings)),
            "settings": settings, "config": config, "receipt": receipt,
            "raw": raw, "revision": result["revision"], "prior": before,
            "setup_revision": created["revision"], "request": request,
            "root": settings.parent}


def historical_bootstrap(project):
    case = historical_project_owned(project)
    config = json.loads(case["config"].read_text())
    before_config = deepcopy(config)
    resources = before_config.pop("runtime_resources")
    documents = {goal: json.loads((case["bundle"] / path).read_text())
                 for goal, path in config["workflows"].items()}
    initial_revision = identity({"config": before_config, "workflows": case["initial_documents"]})
    prior = identity({"config": before_config, "workflows": documents})
    after = identity({"config": config, "workflows": documents})
    assert after == case["source_revision"] and prior != after
    # Rebuild only synthetic historical setup/update records into a coherent
    # pre-association chain. Genuine current producer bytes never enter this case.
    blueprint_path = case["owner_inputs"][2]
    blueprint = json.loads(blueprint_path.read_text())
    blueprint["config"] = deepcopy(before_config)
    write_json(blueprint_path, blueprint)
    settings = json.loads(case["settings"].read_text())
    settings["templates"]["selected"]["digest"] = identity(blueprint)
    write_json(case["settings"], settings)
    create = case["setup_request"]
    create["template"]["digest"] = identity(blueprint)
    setup = json.loads(case["setup_receipt"].read_text())
    setup.update(request_digest=identity(create), template=deepcopy(create["template"]))
    initial_bytes = deepcopy(case["initial_bytes"])
    initial_bytes["project.json"] = (json.dumps(before_config, ensure_ascii=False, indent=2) + "\n").encode()
    setup["files"] = {key: hashlib.sha256(value).hexdigest() for key, value in initial_bytes.items()}
    setup["result"]["revision"] = initial_revision
    write_json(case["setup_receipt"], setup)
    update = case["update_request"]
    update["expected_revision"] = initial_revision
    history = json.loads(case["history_receipt"].read_text())
    history["request_digest"] = identity(update)
    history["result"].update(prior_revision=initial_revision, revision=prior)
    write_json(case["history_receipt"], history)
    request = {"schema": "project-runtime-resources-bootstrap-1",
               "request_id": "historical-bootstrap", "config_path": str(case["config"]),
               "expected_revision": prior, "runtime_resources": resources,
               "receipt_path": "operations/00-historical-bootstrap.json"}
    receipt = case["settings"].parent / request["receipt_path"]
    write_json(receipt, literal_receipt(case["config"], prior, after, request))
    assert config == {**before_config, "runtime_resources": resources}
    assert "runtime_resources" not in blueprint["config"]
    assert setup["request_digest"] == identity(create)
    assert setup["template"]["digest"] == identity(blueprint)
    assert history["request_digest"] == identity(update)
    assert history["result"]["prior_revision"] == setup["result"]["revision"]
    assert history["result"]["revision"] == request["expected_revision"]
    configured = deepcopy(blueprint["config"])
    for edit in create["edits"]:
        parent = configured
        for key in edit["path"][:-1]:
            parent = parent[key]
        parent[edit["path"][-1]] = edit["value"]
    assert configured == before_config
    assert setup["files"] == {key: hashlib.sha256(value).hexdigest() for key, value in initial_bytes.items()}
    assert setup["result"]["revision"] == identity({"config": before_config, "workflows": case["initial_documents"]})
    for goal, source in blueprint["workflow_sources"].items():
        document = json.loads((case["settings"].parent / source["path"]).read_text())
        assert source["digest"] == identity(document)
        assert document == case["initial_documents"][goal]
    assert all("purpose" not in stage and "criteria_authority" not in stage
               for doc in documents.values() for stage in doc["stages"])
    assert all((case["bundle"] / path).read_bytes() == content
               for path, content in case["initial_bytes"].items() if path != "project.json"
               and path != config["workflows"]["development"])
    case.update(owner=FileProjectConfigUpdate(ProjectSettings(case["settings"])),
                receipt=receipt, raw=receipt.read_bytes(), revision=after,
                prior=prior, root=case["project"]["root"],
                bootstrap_request=request, before_config=before_config)
    return case


def source_publication(case, selected):
    return {"source": selected, "config_path": str(case["config"]),
            "revision": case["revision"]}
