"""Checkout-local asset restoration from an explicit, immutable source snapshot."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def asset(path: str, data: bytes, *, kind: str = "working", mode: int = 0o600) -> dict:
    return {
        "path": path,
        "source_path": path,
        "sha256": digest(data),
        "kind": kind,
        "mode": mode,
    }


def case(tmp_path: Path, entries: list[tuple[str, bytes, str]]) -> tuple[Path, Path, Path, dict]:
    target = tmp_path / "target"
    target.mkdir()
    subprocess.run(["git", "init", "-q", str(target)], check=True)
    source = tmp_path / "snapshot"
    source.mkdir()
    assets = []
    for relative, data, kind in entries:
        path = source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        assets.append(asset(relative, data, kind=kind))
    manifest = tmp_path / "assets.json"
    manifest.write_text(
        json.dumps({
            "schema": "local-assets-1",
            "project": "ERP",
            "target_root": str(target),
            "assets": assets,
        }),
        encoding="utf-8",
    )
    request = {
        "schema": "local-assets-restore-1",
        "project": "ERP",
        "target_root": str(target),
        "source_root": str(source),
    }
    return target, source, manifest, request


def restore(manifest: Path, request: dict) -> tuple[subprocess.CompletedProcess[str], dict]:
    completed = subprocess.run(
        [sys.executable, "-m", "poise", "local-assets", "--manifest", str(manifest)],
        input=json.dumps(request),
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.stdout, completed.stderr
    return completed, json.loads(completed.stdout)


def test_restore_is_exact_and_repeat_preserves_bytes_and_mtime(tmp_path: Path) -> None:
    intake = "ai-assistant/agent/document-intake-policy.json"
    output = "ai-assistant/agent/output-policy.json"
    entries = [(intake, b'{"policy":"custom"}\n', "working"),
               (output, b'{"policy":"tracked"}\n', "tracked")]
    target, source, manifest, request = case(tmp_path, entries)
    unrelated = target / "unrelated.txt"
    unrelated.write_bytes(b"retain me")
    source_before = {path: (source / path).read_bytes() for path, _, _ in entries}

    first, first_reply = restore(manifest, request)
    assert first.returncode == 0, first.stderr + first.stdout
    assert first_reply["status"] == "restored"
    assert {item["path"]: item["status"] for item in first_reply["assets"]} == {
        intake: "created", output: "created"
    }
    for path, data, _ in entries:
        assert (target / path).read_bytes() == data
        assert (target / path).stat().st_mode & 0o777 == 0o600
        assert (source / path).read_bytes() == source_before[path]
    times = {path: (target / path).stat().st_mtime_ns for path, _, _ in entries}

    second, second_reply = restore(manifest, request)
    assert second.returncode == 0, second.stderr + second.stdout
    assert second_reply["status"] == "unchanged"
    assert {item["path"]: item["status"] for item in second_reply["assets"]} == {
        intake: "preserved", output: "preserved"
    }
    assert {path: (target / path).stat().st_mtime_ns for path, _, _ in entries} == times
    assert unrelated.read_bytes() == b"retain me"


def test_existing_custom_value_is_preserved(tmp_path: Path) -> None:
    relative = "ai-assistant/agent/document-intake-policy.json"
    target, source, manifest, request = case(tmp_path, [(relative, b"supplied", "working")])
    existing = target / relative
    existing.parent.mkdir(parents=True)
    existing.write_bytes(b"local custom bytes")
    before = existing.stat().st_mtime_ns

    completed, reply = restore(manifest, request)
    assert completed.returncode == 0, completed.stderr + completed.stdout
    assert reply["status"] == "unchanged"
    assert reply["assets"] == [{"path": relative, "status": "preserved"}]
    assert existing.read_bytes() == b"local custom bytes"
    assert existing.stat().st_mtime_ns == before
    assert (source / relative).read_bytes() == b"supplied"


@pytest.mark.parametrize("missing_kind", ["working", "example"])
def test_missing_declared_source_rejects_without_partial_write(tmp_path: Path, missing_kind: str) -> None:
    first = "ai-assistant/agent/output-policy.json"
    missing = "ai-assistant/agent/document-intake-policy.json"
    target, source, manifest, request = case(
        tmp_path, [(first, b"tracked", "tracked"), (missing, b"required", missing_kind)]
    )
    (source / missing).unlink()

    completed, reply = restore(manifest, request)
    assert completed.returncode != 0
    assert reply["status"] == "rejected"
    assert reply["asset"] == missing
    assert "source" in reply["reason"].lower()
    assert reply["recovery"]
    assert not (target / first).exists()
    assert not (target / missing).exists()


def test_identity_and_path_rejections_do_not_write(tmp_path: Path) -> None:
    relative = "ai-assistant/agent/document-intake-policy.json"
    target, source, manifest, request = case(tmp_path, [(relative, b"required", "working")])
    mismatch = {**request, "project": "other"}
    completed, reply = restore(manifest, mismatch)
    assert completed.returncode != 0
    assert reply["status"] == "rejected"
    assert not (target / relative).exists()

    outside = tmp_path / "outside"
    outside.mkdir()
    bad_root = {**request, "target_root": str(outside)}
    completed, reply = restore(manifest, bad_root)
    assert completed.returncode != 0
    assert reply["status"] == "rejected"
    assert not (target / relative).exists()

    document = json.loads(manifest.read_text(encoding="utf-8"))
    document["assets"][0]["path"] = "../escape.json"
    manifest.write_text(json.dumps(document), encoding="utf-8")
    completed, reply = restore(manifest, request)
    assert completed.returncode != 0
    assert reply["status"] == "rejected"
    assert not (target / relative).exists()
    assert not (tmp_path / "escape.json").exists()
    assert (source / relative).read_bytes() == b"required"


def test_source_digest_and_destination_symlink_rejected(tmp_path: Path) -> None:
    relative = "ai-assistant/agent/document-intake-policy.json"
    target, source, manifest, request = case(tmp_path, [(relative, b"required", "working")])
    (source / relative).write_bytes(b"changed after declaration")
    completed, reply = restore(manifest, request)
    assert completed.returncode != 0
    assert reply["status"] == "rejected"
    assert not (target / relative).exists()

    (source / relative).write_bytes(b"required")
    outside = tmp_path / "outside.json"
    outside.write_bytes(b"untouched")
    link = target / relative
    link.parent.mkdir(parents=True)
    link.symlink_to(outside)
    completed, reply = restore(manifest, request)
    assert completed.returncode != 0
    assert reply["status"] == "rejected"
    assert outside.read_bytes() == b"untouched"


def test_restored_policy_makes_owning_reader_usable(tmp_path: Path) -> None:
    erp = os.environ.get("ERP_SOURCE_ROOT")
    if erp is None:
        pytest.skip("ERP_SOURCE_ROOT selects the owning reader and tracked policies")
    erp_root = Path(erp)
    reader = erp_root / "ai-assistant/agent/content-read.py"
    policy_paths = (
        "ai-assistant/agent/document-intake-policy.json",
        "ai-assistant/agent/document-intake-registry-policy.json",
        "ai-assistant/agent/output-policy.json",
    )
    intake_example = erp_root / "ai-assistant/agent/document-intake-policy.example.json"
    sources = [intake_example.read_bytes()]
    sources.extend((erp_root / path).read_bytes() for path in policy_paths[1:])
    target, _, manifest, request = case(
        tmp_path, list(zip(policy_paths, sources, ("working", "tracked", "tracked")))
    )
    (target / "AGENTS.md").write_text("Fixture heading\n", encoding="utf-8")
    before = subprocess.run(
        [sys.executable, str(reader), "--repository-root", str(target), "--path", "AGENTS.md",
         "--lines", "1:1"],
        text=True, capture_output=True, check=False,
    )
    assert before.returncode == 3
    assert str(target / policy_paths[0]) in before.stderr

    completed, reply = restore(manifest, request)
    assert completed.returncode == 0, completed.stderr + completed.stdout
    assert reply["status"] == "restored"
    after = subprocess.run(
        [sys.executable, str(reader), "--repository-root", str(target), "--path", "AGENTS.md",
         "--lines", "1:1"],
        text=True, capture_output=True, check=False,
    )
    assert after.returncode == 0, after.stderr + after.stdout
    assert "Fixture heading" in after.stdout
