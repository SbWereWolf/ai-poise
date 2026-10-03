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


def asset(
    path: str, data: bytes, *, kind: str = "working", mode: int = 0o600,
    repair: str | None = None,
) -> dict:
    return {
        "path": path,
        "source_path": path,
        "sha256": digest(data),
        "kind": kind,
        "mode": mode,
        "repair": repair,
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


ERRORS = {
    "missing_working_source": (
        3, "Declared working source asset is missing.",
        "Supply the matching snapshot asset; owner repair is unavailable.",
    ),
    "missing_repairable_source": (
        3, "Declared working source asset is missing.",
        "Supply the matching snapshot asset or run the declared owner repair, then retry.",
    ),
    "missing_example_source": (
        3, "Declared example source asset is missing.",
        "Supply the declared example in the source snapshot and retry.",
    ),
    "project_mismatch": (
        2, "Project identity does not match the asset declaration.",
        "Select the declared project ID and retry.",
    ),
    "target_root_mismatch": (
        2, "Target root does not match the asset declaration.",
        "Select the declared target Git checkout root and retry.",
    ),
    "unsafe_asset_path": (
        2, "Asset path is not a safe relative path.",
        "Correct the asset declaration path and retry.",
    ),
    "unsafe_source_path": (
        2, "Source path is not a safe relative path.",
        "Correct the source declaration path and retry.",
    ),
    "unsafe_source_root": (
        2, "Source root is not a safe existing directory.",
        "Select a real source snapshot directory and retry.",
    ),
    "unsafe_symlink": (
        2, "Asset path contains a symlink.",
        "Replace the symlinked path with a real path and retry.",
    ),
    "source_digest_mismatch": (
        2, "Source asset digest does not match the declaration.",
        "Restore the declared source bytes or correct the declaration through its owner.",
    ),
}


def assert_rejected(
    completed: subprocess.CompletedProcess[str], reply: dict, *,
    code: str, asset_path: str | None, secret: str = "SYNTHETIC_SECRET_DO_NOT_EXPOSE",
) -> None:
    expected_exit, reason, recovery = ERRORS[code]
    assert completed.returncode == expected_exit
    assert completed.stderr == ""
    assert reply == {
        "status": "rejected", "code": code, "asset": asset_path,
        "reason": reason, "recovery": recovery,
    }
    assert secret not in completed.stdout
    assert secret not in completed.stderr


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
    assert set(first_reply) == {"status", "project", "target_root", "assets"}
    assert (first_reply["status"], first_reply["project"], first_reply["target_root"]) == (
        "restored", "ERP", str(target)
    )
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
    assert set(second_reply) == {"status", "project", "target_root", "assets"}
    assert (second_reply["status"], second_reply["project"], second_reply["target_root"]) == (
        "unchanged", "ERP", str(target)
    )
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
    assert (reply["status"], reply["project"], reply["target_root"]) == (
        "unchanged", "ERP", str(target)
    )
    assert reply["assets"] == [{"path": relative, "status": "preserved"}]
    assert existing.read_bytes() == b"local custom bytes"
    assert existing.stat().st_mtime_ns == before
    assert (source / relative).read_bytes() == b"supplied"


def test_declared_erp_inventory_restores_each_asset_role_without_replacing_values(tmp_path: Path) -> None:
    inventory = [
        ("ai-assistant/agent/document-intake-policy.example.json", "versioned_example"),
        ("ai-assistant/agent/document-intake-policy.json", "working"),
        ("ai-assistant/agent/document-intake-registry-policy.json", "tracked"),
        ("ai-assistant/agent/output-policy.json", "tracked"),
        ("ai-assistant/agent/workflow-backend.settings.example.json", "untracked_example"),
        ("ai-assistant/agent/workflow-backend.settings.json", "working"),
        ("platform-infrastructure/config/dev/local-dev-host-runtime.settings.example.json", "versioned_example"),
        ("platform-infrastructure/config/dev/local-dev-host-runtime.settings.json", "working"),
        ("platform-infrastructure/config/dev/local-development.settings.example.json", "versioned_example"),
        ("platform-infrastructure/config/dev/local-development.settings.json", "working"),
        ("platform-infrastructure/config/dev/local-development.example.env", "versioned_example"),
        ("platform-infrastructure/config/dev/local-development.env", "working"),
        (".codex/config.example.toml", "versioned_example"),
        (".codex/config.toml", "working"),
    ]
    entries = [(path, f"supplied:{kind}:{path}\n".encode(), kind) for path, kind in inventory]
    target, source, manifest, request = case(tmp_path, entries)
    preserved_paths = {
        "ai-assistant/agent/document-intake-policy.json",
        "ai-assistant/agent/workflow-backend.settings.json",
        "platform-infrastructure/config/dev/local-development.env",
        ".codex/config.toml",
    }
    for path in preserved_paths:
        destination = target / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(f"custom:{path}:SYNTHETIC_SECRET_DO_NOT_EXPOSE\n".encode())
    unrelated = target / "unrelated.txt"
    unrelated.write_bytes(b"untouched")
    source_bytes = {path: (source / path).read_bytes() for path, _ in inventory}

    completed, reply = restore(manifest, request)
    assert completed.returncode == 0, completed.stderr + completed.stdout
    assert set(reply) == {"status", "project", "target_root", "assets"}
    assert (reply["status"], reply["project"], reply["target_root"]) == (
        "restored", "ERP", str(target)
    )
    assert {item["path"]: item["status"] for item in reply["assets"]} == {
        path: "preserved" if path in preserved_paths else "created" for path, _ in inventory
    }
    for path, kind in inventory:
        actual = (target / path).read_bytes()
        if path in preserved_paths:
            assert actual == f"custom:{path}:SYNTHETIC_SECRET_DO_NOT_EXPOSE\n".encode()
        else:
            assert actual == source_bytes[path]
        assert (source / path).read_bytes() == source_bytes[path]
        assert kind in {"versioned_example", "untracked_example", "tracked", "working"}
    assert unrelated.read_bytes() == b"untouched"
    assert "SYNTHETIC_SECRET_DO_NOT_EXPOSE" not in completed.stdout + completed.stderr


def test_missing_working_source_reports_declared_owner_repair_without_using_it(tmp_path: Path) -> None:
    relative = "platform-infrastructure/config/dev/local-development.env"
    target, source, manifest, request = case(tmp_path, [(relative, b"secret", "working")])
    document = json.loads(manifest.read_text(encoding="utf-8"))
    document["assets"][0]["repair"] = "bash ./start-dev repair"
    manifest.write_text(json.dumps(document), encoding="utf-8")
    (source / relative).unlink()

    completed, reply = restore(manifest, request)
    assert_rejected(completed, reply, code="missing_repairable_source", asset_path=relative)
    assert not (target / relative).exists()


@pytest.mark.parametrize("missing_kind", ["working", "versioned_example", "untracked_example"])
def test_missing_declared_source_rejects_without_partial_write(tmp_path: Path, missing_kind: str) -> None:
    first = "ai-assistant/agent/output-policy.json"
    missing = "ai-assistant/agent/document-intake-policy.json"
    target, source, manifest, request = case(
        tmp_path, [(first, b"SYNTHETIC_SECRET_DO_NOT_EXPOSE", "tracked"),
                   (missing, b"required", missing_kind)]
    )
    (source / missing).unlink()

    completed, reply = restore(manifest, request)
    assert_rejected(
        completed, reply,
        code="missing_working_source" if missing_kind == "working" else "missing_example_source",
        asset_path=missing,
    )
    assert not (target / first).exists()
    assert not (target / missing).exists()


def test_identity_and_path_rejections_do_not_write(tmp_path: Path) -> None:
    relative = "ai-assistant/agent/document-intake-policy.json"
    target, source, manifest, request = case(tmp_path, [(relative, b"required", "working")])
    mismatch = {**request, "project": "other"}
    completed, reply = restore(manifest, mismatch)
    assert_rejected(completed, reply, code="project_mismatch", asset_path=None)
    assert not (target / relative).exists()

    outside = tmp_path / "outside"
    outside.mkdir()
    bad_root = {**request, "target_root": str(outside)}
    completed, reply = restore(manifest, bad_root)
    assert_rejected(completed, reply, code="target_root_mismatch", asset_path=None)
    assert not (target / relative).exists()
    assert list(outside.iterdir()) == []

    document = json.loads(manifest.read_text(encoding="utf-8"))
    document["assets"][0]["path"] = "../escape.json"
    manifest.write_text(json.dumps(document), encoding="utf-8")
    completed, reply = restore(manifest, request)
    assert_rejected(completed, reply, code="unsafe_asset_path", asset_path="../escape.json")
    assert not (target / relative).exists()
    assert not (tmp_path / "escape.json").exists()
    assert (source / relative).read_bytes() == b"required"


def test_source_digest_and_destination_symlink_rejected(tmp_path: Path) -> None:
    relative = "ai-assistant/agent/document-intake-policy.json"
    target, source, manifest, request = case(tmp_path, [(relative, b"required", "working")])
    (source / relative).write_bytes(b"SYNTHETIC_SECRET_DO_NOT_EXPOSE")
    completed, reply = restore(manifest, request)
    assert_rejected(completed, reply, code="source_digest_mismatch", asset_path=relative)
    assert not (target / relative).exists()

    (source / relative).write_bytes(b"required")
    outside = tmp_path / "outside.json"
    outside.write_bytes(b"untouched")
    link = target / relative
    link.parent.mkdir(parents=True)
    link.symlink_to(outside)
    completed, reply = restore(manifest, request)
    assert_rejected(completed, reply, code="unsafe_symlink", asset_path=relative)
    assert outside.read_bytes() == b"untouched"


@pytest.mark.parametrize(
    ("problem", "error_code"),
    [
        ("destination_absolute", "unsafe_asset_path"),
        ("source_traversal", "unsafe_source_path"),
        ("source_absolute", "unsafe_source_path"),
        ("source_root_symlink", "unsafe_source_root"),
        ("source_ancestor_symlink", "unsafe_symlink"),
        ("destination_ancestor_symlink", "unsafe_symlink"),
        ("digest", "source_digest_mismatch"),
    ],
)
def test_invalid_later_asset_preflight_writes_nothing(
    tmp_path: Path, problem: str, error_code: str,
) -> None:
    first = "ai-assistant/agent/output-policy.json"
    second = "unsafe/document-intake-policy.json"
    target, source, manifest, request = case(
        tmp_path, [(first, b"valid-first", "tracked"), (second, b"required-second", "working")]
    )
    target_sentinel = target / "unrelated.txt"
    target_sentinel.write_bytes(b"target untouched")
    source_sentinel = source / "unrelated.txt"
    source_sentinel.write_bytes(b"source untouched")
    document = json.loads(manifest.read_text(encoding="utf-8"))
    outside = tmp_path / "outside"
    outside.mkdir()
    outside_sentinel = outside / "unrelated.txt"
    outside_sentinel.write_bytes(b"outside untouched")
    declared_path = second
    if problem == "destination_absolute":
        declared_path = str(outside / "escaped.json")
        document["assets"][1]["path"] = declared_path
    elif problem == "source_traversal":
        document["assets"][1]["source_path"] = "../outside/escaped.json"
    elif problem == "source_absolute":
        document["assets"][1]["source_path"] = str(outside / "escaped.json")
    elif problem == "source_root_symlink":
        source_link = tmp_path / "source-link"
        source_link.symlink_to(source, target_is_directory=True)
        request["source_root"] = str(source_link)
    elif problem == "source_ancestor_symlink":
        (source / second).unlink()
        (source / "unsafe").rmdir()
        (outside / "document-intake-policy.json").write_bytes(b"required-second")
        (source / "unsafe").symlink_to(outside, target_is_directory=True)
    elif problem == "destination_ancestor_symlink":
        (target / "unsafe").symlink_to(outside, target_is_directory=True)
    elif problem == "digest":
        (source / second).write_bytes(b"SYNTHETIC_SECRET_DO_NOT_EXPOSE")
    manifest.write_text(json.dumps(document), encoding="utf-8")

    completed, reply = restore(manifest, request)
    assert_rejected(
        completed, reply, code=error_code,
        asset_path=None if problem == "source_root_symlink" else declared_path,
    )
    assert not (target / first).exists()
    assert not (target / second).is_file()
    assert target_sentinel.read_bytes() == b"target untouched"
    assert source_sentinel.read_bytes() == b"source untouched"
    assert outside_sentinel.read_bytes() == b"outside untouched"
    assert not (outside / "escaped.json").exists()


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
         "--lines", "1:1", "--json"],
        text=True, capture_output=True, check=False,
    )
    assert before.returncode == 3
    assert str(target / policy_paths[0]) in before.stderr

    completed, reply = restore(manifest, request)
    assert completed.returncode == 0, completed.stderr + completed.stdout
    assert reply["status"] == "restored"
    after = subprocess.run(
        [sys.executable, str(reader), "--repository-root", str(target), "--path", "AGENTS.md",
         "--lines", "1:1", "--json"],
        text=True, capture_output=True, check=False,
    )
    assert after.returncode == 0, after.stderr + after.stdout
    assert after.stderr == ""
    reader_reply = json.loads(after.stdout)
    assert reader_reply["status"] == "content"
    assert reader_reply["path"] == "AGENTS.md"
    assert reader_reply["range"] == "lines:1-1"
    assert reader_reply["content"] == "Fixture heading\n"
    assert reader_reply["hash"] == digest(b"Fixture heading\n")
    assert reader_reply["content_bytes"] == len(b"Fixture heading\n")
    assert reader_reply["content_lines"] == 1
