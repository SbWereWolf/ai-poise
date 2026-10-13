"""Test-owned historical project setup, distinct from catalogue publication.

The old documents and receipt are arranged as historical persistence. Current
setup cannot generate an old-schema installation, and is not weakened to do so.
Only disposable pytest databases are written by this module.
"""
from copy import deepcopy
from contextlib import redirect_stderr, redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from types import SimpleNamespace

from tests.backups.helpers import backup_commands
from tests.conftest import write_json
from tests.projects.helpers import setup_case
from tests.migrations.stage_criteria_helpers import (
    historical_project, identity, publication_rows, snapshot,
)


ROOT = Path(__file__).resolve().parents[2]


def historical_project_owned(project):
    database, packet, catalogue_target = historical_project(
        project, include_newborn=True, create_backup=False,
    )
    root = project["root"]
    settings, blueprint, create = setup_case(project)
    source = json.loads(catalogue_target.read_text())
    stages = json.loads((ROOT / "fixtures/stage-criteria/project-migration-stages.json").read_text())
    config = json.loads(project["config_path"].read_text())
    config["paths"]["state"] = str(database.parent)
    config["runtime_resources"] = {"project_setup": str(settings), "hooks": []}
    bundle = root / create["destination"]
    project["config_path"] = bundle / "project.json"
    config["processes"] = {}
    documents = {}
    sources = {}
    for goal in ("development", "design"):
        document = deepcopy(source)
        document["goal_type"] = goal
        relative = f"config/project-processes/{goal}.json"
        config["processes"][goal] = relative
        write_json(bundle / relative, document)
        documents[goal] = document
        source_path = f"config/historical-sources/{goal}.json"
        write_json(root / source_path, document)
        sources[goal] = {"path": source_path, "digest": identity(document)}
    write_json(project["config_path"], config)
    project["cfg"] = config
    blueprint["config"] = deepcopy(config)
    blueprint["process_sources"] = sources
    blueprint_path = write_json(root / "config/project-blueprint.json", blueprint)
    setup_settings = json.loads(settings.read_text())
    setup_settings["lock"] = "project-setup.lock"
    setup_settings["templates"]["selected"]["digest"] = identity(blueprint)
    write_json(settings, setup_settings)
    create["request_id"] = "historical-project-setup"
    create["template"]["digest"] = identity(blueprint)
    create["edits"][0]["value"] = config["project"]
    create["probe_repository"] = False
    initial_documents = deepcopy(documents)
    initial_revision = identity({"config": config, "processes": documents})
    initial_bytes = {
        relative: (bundle / relative).read_bytes()
        for relative in ("project.json", *config["processes"].values())
    }
    receipt_path = bundle / "setup-receipt.json"
    write_json(receipt_path, {
        "schema": "project-receipt-1", "request_id": create["request_id"],
        "request_digest": identity(create), "template": create["template"],
        "files": {key: hashlib.sha256(value).hexdigest() for key, value in initial_bytes.items()},
        "result": {"status": "created", "project": config["project"], "revision": initial_revision,
                   "config_path": str(project["config_path"]), "process_count": 2,
                   "readiness": {"repository": "not_checked", "remote": "not_checked"}, "replayed": False},
    })
    registry = write_json(root / "state/project-registry.json", {
        "schema": "configured-project-registry-1",
        "projects": {config["project"]: {"config_path": str(project["config_path"].relative_to(root))}},
    })
    # A completed historical publication follows setup; setup hashes remain immutable.
    instruction = "Historical accepted project instruction."
    update = {
        "schema": "project-config-update-1", "request_id": "historical-project-update",
        "config_path": str(project["config_path"]), "expected_revision": initial_revision,
        "manifest_edits": [], "process_updates": [{"goal_type": "development",
            "expected_revision": identity(documents["development"]),
            "changes": [{"op": "patch_stage", "id": "tests", "set": {"instruction": instruction}}]}],
        "state_relocation": None, "probe_repository": False,
        "receipt_path": "operations/historical-project-update.json",
    }
    documents["development"]["stages"][0]["instruction"] = instruction
    write_json(bundle / config["processes"]["development"], documents["development"])
    revision = identity({"config": config, "processes": documents})
    history_receipt = root / update["receipt_path"]
    write_json(history_receipt, {
        "schema": "project-config-receipt-1", "request_id": update["request_id"],
        "request_digest": identity(update), "result": {
            "status": "updated", "changed": True, "prior_revision": initial_revision,
            "revision": revision, "config_path": str(project["config_path"]),
            "readiness": {"repository": "not_checked", "remote": "not_checked"},
            "receipt_path": str(history_receipt), "replayed": False},
    })
    packet["mapping"][0]["expected_revision"] = identity(documents["development"])
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE tasks SET status=?,claimed_by=? WHERE id=?", ("active", "historical-executor", "T2"))
        submission = connection.execute(
            "SELECT seq FROM submissions WHERE task_id=? AND stage=?", ("T2", "tests"),
        ).fetchone()[0]
    packet["mapping"][0]["stages"] = deepcopy(stages)
    packet["mapping"].insert(1, {
        "kind": "process_definition", "goal_type": "design",
        "expected_revision": identity(documents["design"]), "stages": deepcopy(stages),
    })
    for mapped in packet["mapping"][2:]:
        for stage, literal in zip(mapped["stages"], stages, strict=True):
            stage.update(deepcopy(literal))
            stage["criteria"] = {
                "requirements": ["Результат этапа доступен проверяющему."],
                "definition_of_ready": [], "definition_of_done": [],
            }
            if mapped["task_id"] == "T2" and stage["stage_id"] == "tests":
                stage["execution"] = {
                    "state": "started", "source_revision": None,
                    "source_refs": [{"kind": "submission", "id": submission}],
                }
    packet["request_id"] = "project-owned-stage-criteria"
    packet["backup_name"] = backup_commands(project).create()["name"]
    unrelated = root / "unrelated.txt"
    unrelated.write_text("Preserve the operator's unrelated working file.\n")
    case = {"project": project, "database": database, "packet": packet,
            "settings": settings, "config": project["config_path"], "bundle": bundle,
            "setup_receipt": receipt_path, "source_revision": revision,
            "process_paths": [bundle / config["processes"][goal] for goal in ("development", "design")],
            "catalogue_target": catalogue_target, "unrelated": unrelated,
            "mapping_stages": stages, "history_receipt": history_receipt,
            "owner_inputs": [settings, registry, blueprint_path, root / "criteria-catalogue.json",
                             root / "criteria-editor.json", *[root / item["path"] for item in sources.values()]],
            "setup_request": create, "update_request": update,
            "initial_documents": initial_documents, "initial_bytes": initial_bytes}
    assert_fixture_consistent(case)
    return case


def assert_fixture_consistent(case):
    """Independent identities and layout; never produce expected migration output."""
    from poise.infrastructure.projects import ProjectSettings, FileProjectSetup
    settings = ProjectSettings(case["settings"])
    bundle = case["bundle"]
    assert bundle != settings.root and bundle.is_relative_to(settings.root)
    assert not settings.registry.is_relative_to(bundle)
    assert not settings.lock.is_relative_to(bundle)
    blueprint = json.loads(case["owner_inputs"][2].read_text())
    create = case["setup_request"]
    assert settings.raw["templates"]["selected"]["digest"] == create["template"]["digest"] == identity(blueprint)
    assert FileProjectSetup(settings)._registry()["projects"][case["project"]["cfg"]["project"]]["config_path"] == str(case["config"].relative_to(settings.root))
    config = json.loads(case["config"].read_text())
    assert config["runtime_resources"] == {"project_setup": str(case["settings"]), "hooks": []}
    assert settings.lock == settings.root / "project-setup.lock"
    assert not settings.lock.is_relative_to(case["database"].parent)
    configured = deepcopy(blueprint["config"])
    for edit in create["edits"]:
        parent = configured
        for key in edit["path"][:-1]:
            parent = parent[key]
        parent[edit["path"][-1]] = edit["value"]
    assert configured == config
    for goal, source in blueprint["process_sources"].items():
        document = json.loads((settings.root / source["path"]).read_text())
        assert source["digest"] == identity(document)
        assert document == case["initial_documents"][goal]
    setup = json.loads(case["setup_receipt"].read_text())
    assert setup["request_id"] == create["request_id"]
    assert setup["request_digest"] == identity(create)
    assert setup["template"] == create["template"]
    assert setup["files"] == {key: hashlib.sha256(value).hexdigest() for key, value in case["initial_bytes"].items()}
    assert setup["result"]["revision"] == identity({"config": config, "processes": case["initial_documents"]})
    update = case["update_request"]
    history = json.loads(case["history_receipt"].read_text())
    assert history["request_id"] == update["request_id"]
    assert history["request_digest"] == identity(update)
    assert history["result"]["prior_revision"] == update["expected_revision"] == setup["result"]["revision"]
    expected = deepcopy(case["initial_documents"])
    expected["development"]["stages"][0]["instruction"] = update["process_updates"][0]["changes"][0]["set"]["instruction"]
    actual = {goal: json.loads((bundle / path).read_text()) for goal, path in config["processes"].items()}
    assert actual == expected
    assert history["result"]["revision"] == case["source_revision"] == identity({"config": config, "processes": actual})
    assert all("purpose" not in stage and "criteria_authority" not in stage for doc in actual.values() for stage in doc["stages"])


def run_migration(case, *, fresh=False, patch=None, select_project_owner=True):
    arguments = ["task-process-migrate", "--config", str(case["config"])]
    if select_project_owner:
        arguments.extend(["--project-settings", str(case["settings"])])
    raw = json.dumps(case["packet"], ensure_ascii=False)
    if fresh:
        env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
        result = subprocess.run([sys.executable, "-B", "-m", "poise", *arguments],
                                input=raw, text=True, capture_output=True, cwd=ROOT, env=env)
        code, out, err = result.returncode, result.stdout, result.stderr
    else:
        from poise.__main__ import main
        out, err = io.StringIO(), io.StringIO()
        with patch.context() as scoped, redirect_stdout(out), redirect_stderr(err):
            scoped.setattr(sys, "argv", ["poise", *arguments])
            scoped.setattr(sys, "stdin", SimpleNamespace(buffer=io.BytesIO(raw.encode())))
            try:
                code = main()
            except SystemExit as exc:
                code = exc.code
        out, err = out.getvalue(), err.getvalue()
    value = json.loads(out) if out.strip() else {}
    return code, value, err


def fresh_work_read(case):
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
    return subprocess.run(
        [sys.executable, "-B", "-m", "tests.migrations.project_criteria_read_probe", str(case["config"])],
        text=True, capture_output=True, cwd=ROOT, env=env,
    )


def fresh_locked_migration(case):
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
    return subprocess.run(
        [sys.executable, "-B", "-m", "tests.migrations.project_criteria_read_probe",
         "migration-locks", str(case["config"]), str(case["settings"])],
        input=json.dumps(case["packet"], ensure_ascii=False),
        text=True, capture_output=True, cwd=ROOT, env=env,
    )


def publication_files(case):
    roots = (case["project"]["root"] / "operations", case["bundle"] / "operations")
    return {str(path): path.read_bytes() for root in roots if root.exists()
            for path in sorted(root.iterdir()) if path.is_file()}


def observe(case):
    return {
        "task": snapshot(case["database"]),
        "owner_inputs": {str(path): path.read_bytes() if path.exists() else None for path in case["owner_inputs"]},
        "publication": publication_files(case),
        "source": {str(path): path.read_bytes() for path in case["process_paths"]},
        "config": case["config"].read_bytes(),
        "setup_receipt": case["setup_receipt"].read_bytes() if case["setup_receipt"].exists() else None,
        "catalogue": publication_rows(case["project"]),
        "catalogue_bytes": case["catalogue_target"].read_bytes(),
        "unrelated": case["unrelated"].read_bytes(),
    }


def project_receipts(case):
    found = []
    for path in case["project"]["root"].rglob("*.json"):
        value = json.loads(path.read_text())
        if path != case["history_receipt"] and isinstance(value, dict) and value.get("schema") == "project-config-receipt-1":
            found.append((path, value))
    return found
