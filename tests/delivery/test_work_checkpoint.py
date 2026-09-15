"""Observable recovery contract, independent temporary repositories and SQLite data."""
from __future__ import annotations
import base64
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tarfile
import io

import pytest

TOOL = Path(__file__).resolve().parents[2] / "tools/work_checkpoint.py"


def cli(*args):
    return subprocess.run([sys.executable, str(TOOL), *map(str, args)], capture_output=True, text=True)


def git(repo, *args):
    return subprocess.check_output(["git", "-c", "core.hooksPath=/dev/null", *args], cwd=repo, text=True).strip()


@pytest.fixture
def inputs(tmp_path):
    repo = tmp_path / "code with spaces"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "work/checkpoint")
    git(repo, "config", "user.name", "Recovery Test")
    git(repo, "config", "user.email", "test@example.invalid")
    (repo / "main.py").write_text("print('base')\n")
    git(repo, "add", "main.py")
    git(repo, "commit", "-qm", "base")
    base = git(repo, "rev-parse", "HEAD")
    (repo / "main.py").write_text("print('проверка')\n")
    (repo / "run.sh").write_text("#!/bin/sh\nexit 0\n")
    (repo / "run.sh").chmod(0o755)
    (repo / "binary.dat").write_bytes(b"\x00\xff\x01\x02")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "verified candidate")
    db = tmp_path / "snapshot.sqlite"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE note (id INTEGER PRIMARY KEY, value TEXT)")
        conn.execute("INSERT INTO note VALUES (1, 'unchanged')")
    state = tmp_path / "state.json"
    state.write_text(json.dumps({
        "current_task": "RECOVERY-TOOLING", "next_action": "F2 C020",
        "requirements": ["no runtime path-policy change"], "completed": [], "blocked": [],
        "rejected_heads": []
    }))
    output = tmp_path / "checkpoint"
    return repo, base, db, state, output


def create(inputs):
    repo, base, db, state, output = inputs
    return cli("create", "--repository", repo, "--base", base,
               "--database-snapshot", db, "--state", state, "--output", output)


def test_round_trip_from_text_transport_without_original_binary(inputs, tmp_path):
    repo, base, db, state, output = inputs
    head = git(repo, "rev-parse", "HEAD")
    db_hash = hashlib.sha256(db.read_bytes()).hexdigest()
    made = create(inputs)
    assert made.returncode == 0, made.stdout + made.stderr
    text = output / "checkpoint.recovery.txt"
    assert text.read_text().startswith("AI-POISE-RECOVERY-TEXT-1\n")
    (output / "checkpoint.tar.xz").unlink()
    restored = tmp_path / "restored"
    result = cli("restore", "--checkpoint", text, "--destination", restored)
    assert result.returncode == 0, result.stdout + result.stderr
    recovered = restored / "repository"
    assert git(recovered, "rev-parse", "HEAD") == head
    assert git(recovered, "status", "--porcelain") == ""
    assert git(recovered, "fsck", "--full", "--no-reflogs") == ""
    assert (recovered / "main.py").read_text() == "print('проверка')\n"
    assert (recovered / "binary.dat").read_bytes() == b"\x00\xff\x01\x02"
    assert (recovered / "run.sh").stat().st_mode & 0o111
    assert hashlib.sha256((restored / "payload/tasks.sqlite").read_bytes()).hexdigest() == db_hash
    assert hashlib.sha256(db.read_bytes()).hexdigest() == db_hash
    assert git(repo, "rev-parse", "HEAD") == head


def test_dirty_work_is_rejected_without_discarding_it(inputs):
    repo, _, _, _, output = inputs
    (repo / "main.py").write_text("unsaved work\n")
    result = create(inputs)
    assert result.returncode == 2
    assert "dirty" in result.stdout.lower()
    assert (repo / "main.py").read_text() == "unsaved work\n"
    assert not output.exists()


def test_unknown_untracked_files_are_not_silently_lost(inputs):
    repo, _, _, _, output = inputs
    (repo / "private.env").write_text("SECRET=do-not-package\n")
    result = create(inputs)
    assert result.returncode == 2
    assert "dirty" in result.stdout.lower()
    assert not output.exists()


def test_transport_tampering_is_rejected_before_writes(inputs, tmp_path):
    assert create(inputs).returncode == 0
    text = inputs[-1] / "checkpoint.recovery.txt"
    lines = text.read_text().splitlines()
    lines[3] = "AAAA" + lines[3][4:]
    text.write_text("\n".join(lines) + "\n")
    dest = tmp_path / "refused"
    result = cli("restore", "--checkpoint", text, "--destination", dest)
    assert result.returncode == 2
    assert not dest.exists()


def test_restore_never_overwrites_existing_directory(inputs, tmp_path):
    assert create(inputs).returncode == 0
    dest = tmp_path / "existing"
    dest.mkdir()
    (dest / "WIP").write_text("preserve")
    result = cli("restore", "--checkpoint", inputs[-1] / "checkpoint.tar.xz", "--destination", dest)
    assert result.returncode == 2
    assert (dest / "WIP").read_text() == "preserve"


def test_bad_database_is_rejected(inputs):
    inputs[2].write_bytes(b"not a SQLite database")
    result = create(inputs)
    assert result.returncode == 2
    assert not inputs[-1].exists()


def test_rejected_history_cannot_become_checkpoint(inputs):
    repo, _, _, state, output = inputs
    doc = json.loads(state.read_text())
    doc["rejected_heads"] = [git(repo, "rev-parse", "HEAD")]
    state.write_text(json.dumps(doc))
    result = create(inputs)
    assert result.returncode == 2
    assert "rejected" in result.stdout.lower()
    assert not output.exists()


def test_delivery_requires_matching_complete_readback(inputs, tmp_path):
    assert create(inputs).returncode == 0
    output = inputs[-1]
    bad = tmp_path / "truncated.txt"
    bad.write_text((output / "checkpoint.recovery.txt").read_text()[:100])
    failure = cli("seal-delivery", "--checkpoint", output, "--message-id", "real-test-id", "--readback", bad)
    assert failure.returncode == 2
    assert not (output / "DELIVERY.json").exists()
    success = cli("seal-delivery", "--checkpoint", output, "--message-id", "real-test-id",
                  "--readback", output / "checkpoint.recovery.txt")
    assert success.returncode == 0, success.stdout
    receipt = json.loads((output / "DELIVERY.json").read_text())
    assert receipt["readback_verified"] is True
    assert receipt["message_id"] == "real-test-id"


def test_readable_patch_and_state_accompany_checkpoint(inputs):
    assert create(inputs).returncode == 0
    output = inputs[-1]
    record = json.loads((output / "CHECKPOINT.json").read_text())
    assert record["head"] == git(inputs[0], "rev-parse", "HEAD")
    assert record["base"] == inputs[1]
    assert "print('проверка')" in (output / "changes.patch.txt").read_text()
    assert record["state"]["next_action"] == "F2 C020"
    assert record["delivery_verified"] is False


def test_archive_path_escape_is_rejected(tmp_path):
    archive = tmp_path / "escape.tar.xz"
    with tarfile.open(archive, "w:xz") as tar:
        info = tarfile.TarInfo("../escape.txt")
        info.size = 6
        tar.addfile(info, io.BytesIO(b"escape"))
    result = cli("restore", "--checkpoint", archive, "--destination", tmp_path / "dest")
    assert result.returncode == 2
    assert not (tmp_path / "escape.txt").exists()


def test_recovery_rule_has_exact_document_anchor():
    root = TOOL.parents[1]
    target = "docs/workflows/checkpoint-recovery.md#контрольная-точка-и-восстановление"
    assert target in (root / "AGENTS.md").read_text()
    doc = (root / "docs/workflows/checkpoint-recovery.md").read_text()
    assert "## Контрольная точка и восстановление" in doc
    assert "work_checkpoint.py" in doc
    assert "seal-delivery" in doc
