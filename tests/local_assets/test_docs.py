"""Execute the canonical operator examples against controlled checkout storage."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import unquote

import pytest


ROOT = Path(__file__).resolve().parents[2]
MANUAL = ROOT / "docs/configuration/local-assets.md"


def block(text: str, name: str) -> str:
    found = re.findall(rf"```bash {re.escape(name)}\n(.*?)\n```", text, re.DOTALL)
    assert len(found) == 1
    return found[0]


def local_link_target(link: str, manual: Path = MANUAL) -> tuple[Path, str]:
    path_part, _, fragment = unquote(link).partition("#")
    target = manual if not path_part else (manual.parent / path_part).resolve()
    return target, fragment


def markdown_anchors(text: str) -> set[str]:
    prose = []
    fence = None
    for line in text.splitlines():
        stripped = line.lstrip()
        marker = stripped[:3]
        if fence is None and marker in ("```", "~~~"):
            fence = marker
            continue
        if fence is not None and stripped.startswith(fence):
            fence = None
            continue
        if fence is None:
            prose.append(line)
    prose_text = "\n".join(prose)
    anchors = set(re.findall(r'<a\s+id="([^"]+)"', prose_text))
    counts: dict[str, int] = {}
    for heading in re.findall(r"^#{1,6}\s+(.+?)\s*$", prose_text, re.MULTILINE):
        plain = re.sub(r"<[^>]*>", "", heading)
        plain = re.sub(r"[`*_]", "", plain)
        slug = re.sub(r"[^\w\- ]", "", plain.lower()).replace(" ", "-")
        suffix = counts.get(slug, 0)
        counts[slug] = suffix + 1
        anchors.add(slug if suffix == 0 else f"{slug}-{suffix}")
    return anchors


def assert_local_link(link: str, manual: Path = MANUAL) -> None:
    target, fragment = local_link_target(link, manual)
    assert target.is_file(), link
    if fragment:
        assert fragment in markdown_anchors(target.read_text(encoding="utf-8")), link


def test_fenced_shell_comment_is_not_a_markdown_anchor(tmp_path: Path) -> None:
    manual = tmp_path / "sample.md"
    manual.write_text(
        "# Real heading\n```bash\n# Comment only\ntrue\n```\n",
        encoding="utf-8",
    )
    assert_local_link("#real-heading", manual)
    with pytest.raises(AssertionError):
        assert_local_link("#comment-only", manual)


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
        pytest.skip("ERP_SOURCE_ROOT selects the owning reader and tracked policies")
    erp = Path(erp_source)
    text = MANUAL.read_text(encoding="utf-8")
    restore_command = block(text, "restore-example")
    reader_command = block(text, "reader-check-example")
    assert "start-dev repair" in text
    assert "Poise Task DB" in text
    assert "missing_working_source" in text
    assert "Supply the matching snapshot asset; owner repair is unavailable." in text
    for link in re.findall(r"\[[^\]]+\]\(([^)]+)\)", text):
        if link.startswith(("https://", "http://", "#")):
            if link.startswith("#"):
                assert_local_link(link)
            continue
        assert_local_link(link)
    with pytest.raises(AssertionError):
        assert_local_link("#definitely-nonexistent-heading")
    with pytest.raises(AssertionError):
        assert_local_link("project-setup.md#definitely-nonexistent-heading")

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
        "status": "rejected", "code": "missing_working_source",
        "asset": "ai-assistant/agent/document-intake-policy.json",
        "reason": "Declared working source asset is missing.",
        "recovery": "Supply the matching snapshot asset; owner repair is unavailable.",
    }
    assert not (missing_target / "ai-assistant/agent/output-policy.json").exists()
