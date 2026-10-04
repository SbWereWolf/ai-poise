"""Execute the canonical operator examples against controlled checkout storage."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
MANUAL = ROOT / "docs/configuration/local-assets.md"
ERRORS = json.loads(
    (Path(__file__).parent / "fixtures/error-contracts.json").read_text(encoding="utf-8")
)


def block(text: str, name: str) -> str:
    found = re.findall(rf"```bash {re.escape(name)}\n(.*?)\n```", text, re.DOTALL)
    assert len(found) == 1
    return found[0]


def check_documentation(checkout: Path, changed: str) -> tuple[subprocess.CompletedProcess[str], dict]:
    completed = subprocess.run(
        [sys.executable, str(ROOT / "tools/check_documentation.py"),
         "--checkout", str(checkout), "--changed", changed],
        cwd=ROOT, text=True, capture_output=True, check=False,
    )
    assert completed.stdout, completed.stderr
    return completed, json.loads(completed.stdout)


def run(command: str, env: dict[str, str]) -> tuple[subprocess.CompletedProcess[str], dict]:
    completed = subprocess.run(
        ["bash", "-e", "-u", "-c", command], cwd=ROOT,
        env={**os.environ, **env}, text=True, capture_output=True, check=False,
    )
    assert completed.stdout, completed.stderr
    return completed, json.loads(completed.stdout)


def fixture(tmp_path: Path, erp: Path) -> tuple[Path, Path, Path, Path]:
    tmp_path.mkdir()
    target = tmp_path / "checkout"
    target.mkdir()
    subprocess.run(["git", "init", "-q", str(target)], check=True)
    (target / "AGENTS.md").write_text("Fixture heading\n", encoding="utf-8")
    source = tmp_path / "snapshot"
    source.mkdir()
    paths = (
        "ai-assistant/agent/document-intake-policy.json",
        "ai-assistant/agent/document-intake-registry-policy.json",
        "ai-assistant/agent/output-policy.json",
    )
    sources = [
        erp / "ai-assistant/agent/document-intake-policy.example.json",
        erp / paths[1], erp / paths[2],
    ]
    assets = []
    for relative, original in zip(paths, sources):
        data = original.read_bytes()
        destination = source / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
        assets.append({
            "path": relative, "source_path": relative,
            "sha256": hashlib.sha256(data).hexdigest(),
            "kind": "working" if relative == paths[0] else "tracked",
            "mode": 0o600, "repair": None,
        })
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({
        "schema": "local-assets-1", "project": "ERP",
        "target_root": str(target), "assets": assets,
    }), encoding="utf-8")
    request = tmp_path / "request.json"
    request.write_text(json.dumps({
        "schema": "local-assets-restore-1", "project": "ERP",
        "target_root": str(target), "source_root": str(source),
    }), encoding="utf-8")
    return target, source, manifest, request


def test_documented_restore_reader_and_missing_snapshot_examples_execute(tmp_path: Path) -> None:
    erp_source = os.environ.get("ERP_SOURCE_ROOT")
    if erp_source is None:
        pytest.fail("ERP_SOURCE_ROOT must explicitly select the owning reader and tracked policies")
    erp = Path(erp_source)
    text = MANUAL.read_text(encoding="utf-8")
    restore_command = block(text, "restore-example")
    reader_command = block(text, "reader-check-example")
    assert "start-dev repair" in text
    assert "Poise Task DB" in text
    assert "missing_working_source" in text
    assert "Supply the matching snapshot asset; owner repair is unavailable." in text
    checked_docs, link_report = check_documentation(ROOT, "docs/configuration/local-assets.md")
    assert checked_docs.returncode == 0, checked_docs.stderr + checked_docs.stdout
    assert link_report["status"] == "valid"
    assert link_report["errors"] == []
    assert link_report["read_only"] is True
    assert link_report["network_access"] is False

    target, source, manifest, request = fixture(tmp_path / "complete", erp)
    env = {
        "POISE_PYTHON": sys.executable, "MANIFEST": str(manifest),
        "REQUEST": str(request), "TARGET_ROOT": str(target),
        "ERP_READER": str(erp / "ai-assistant/agent/content-read.py"),
        "PYTHONPATH": str(ROOT / "src"),
    }
    completed, reply = run(restore_command, env)
    assert completed.returncode == 0, completed.stderr
    assert reply["status"] == "restored"
    assert {item["status"] for item in reply["assets"]} == {"created"}
    checked, reader = run(reader_command, env)
    assert checked.returncode == 0, checked.stderr
    assert reader["status"] == "content"
    assert reader["path"] == "AGENTS.md"
    assert reader["range"] == "lines:1-1"
    assert reader["content"] == "Fixture heading\n"

    missing_target, missing_source, missing_manifest, missing_request = fixture(
        tmp_path / "missing", erp
    )
    intake = missing_source / "ai-assistant/agent/document-intake-policy.json"
    intake.unlink()
    incomplete_env = {
        **env, "MANIFEST": str(missing_manifest), "REQUEST": str(missing_request),
        "TARGET_ROOT": str(missing_target),
    }
    incomplete, error = run(restore_command, incomplete_env)
    assert incomplete.returncode == 3
    assert error == {
        **ERRORS["missing_working_source"]["reply"],
        "asset": "ai-assistant/agent/document-intake-policy.json",
    }
    assert not (missing_target / "ai-assistant/agent/output-policy.json").exists()
