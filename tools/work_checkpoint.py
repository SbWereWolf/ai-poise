"""Portable, verified development checkpoints; no Task lifecycle or path-policy changes.

The input database is an OFFLINE snapshot made by the existing backup owner.
The text transport is an explicitly labelled ASCII encoding, not a renamed binary.
"""
from __future__ import annotations

import argparse
import base64
from contextlib import closing
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import sqlite3
import subprocess
import tarfile
import tempfile
import textwrap

SCHEMA = "ai-poise-work-checkpoint-1"
TEXT_HEADER = "AI-POISE-RECOVERY-TEXT-1"


class CheckpointError(ValueError):
    """A checkpoint is incomplete or cannot be restored without changing existing work."""


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def git(repository: Path, *args: str) -> bytes:
    result = subprocess.run(
        ["git", "-c", "core.hooksPath=/dev/null", "-c", "core.autocrlf=false", *args],
        cwd=repository, capture_output=True, check=False,
    )
    if result.returncode:
        raise CheckpointError(result.stderr.decode("utf-8", errors="replace").strip())
    return result.stdout


def json_write(path: Path, data: dict) -> None:
    with path.open("w", encoding="utf-8") as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


def require_new(path: Path) -> None:
    if path.exists() or path.is_symlink():
        raise CheckpointError(f"Destination already exists; nothing overwritten: {path}")


def check_database(path: Path) -> None:
    if path.is_symlink() or not path.is_file():
        raise CheckpointError("Database snapshot must be an existing regular file")
    if any(Path(str(path) + suffix).exists() for suffix in ("-wal", "-journal")):
        raise CheckpointError("Use a sealed offline database snapshot, not a live database")
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro&immutable=1", uri=True)) as conn:
        if conn.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
            raise CheckpointError("Database snapshot failed integrity_check")


def checked_state(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise CheckpointError("Recovery state must be a JSON object")
    for key in ("current_task", "next_action"):
        if not isinstance(data.get(key), str) or not data[key].strip():
            raise CheckpointError(f"Missing explicit recovery state: {key}")
    for key in ("requirements", "completed", "blocked"):
        if not isinstance(data.get(key), list):
            raise CheckpointError(f"Recovery state must declare {key} as a list")
    rejected = data.get("rejected_heads", [])
    if not isinstance(rejected, list) or not all(isinstance(v, str) for v in rejected):
        raise CheckpointError("rejected_heads must be a list of exact commits")
    return data


def create(repository: Path, base: str, database_snapshot: Path, state: Path,
           output: Path, evidence: list[Path]) -> dict:
    repository = repository.resolve()
    output = output.absolute()
    require_new(output)
    declared = checked_state(state)
    if git(repository, "status", "--porcelain", "--untracked-files=all").strip():
        raise CheckpointError("Dirty checkout: explicitly save candidate work before checkpoint; no stash/reset is performed")
    head = git(repository, "rev-parse", "HEAD^{commit}").decode().strip()
    base = git(repository, "rev-parse", f"{base}^{{commit}}").decode().strip()
    git(repository, "merge-base", "--is-ancestor", base, head)
    ancestors = set(git(repository, "rev-list", head).decode().splitlines())
    if ancestors.intersection(declared.get("rejected_heads", [])):
        raise CheckpointError("Rejected history is present in the selected checkpoint ancestry")
    branch = git(repository, "symbolic-ref", "--short", "HEAD").decode().strip()
    git(repository, "check-ref-format", "--branch", branch)
    check_database(database_snapshot)
    before_db = digest(database_snapshot)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".checkpoint-", dir=output.parent) as temp:
        staging = Path(temp) / "checkpoint"
        payload = staging / "payload"
        payload.mkdir(parents=True)
        git(repository, "bundle", "create", str(payload / "repository.bundle"), head, "HEAD")
        shutil.copyfile(database_snapshot, payload / "tasks.sqlite")
        if digest(payload / "tasks.sqlite") != before_db or digest(database_snapshot) != before_db:
            raise CheckpointError("Database snapshot changed while packaging")
        json_write(payload / "state.json", declared)
        patch = git(repository, "diff", "--no-ext-diff", "--no-textconv", "--binary", "--full-index", base, head)
        (payload / "changes.patch.txt").write_bytes(patch)
        (payload / "commits.mbox.txt").write_bytes(git(
            repository, "format-patch", "--stdout", "--binary", "--full-index", f"{base}..{head}"))
        (payload / "commit.txt").write_bytes(git(repository, "cat-file", "commit", head))
        (payload / "evidence").mkdir()
        for index, path in enumerate(evidence):
            if not path.is_file() or path.is_symlink():
                raise CheckpointError(f"Evidence must be a regular file: {path}")
            shutil.copyfile(path, payload / "evidence" / f"{index:03d}-{path.name}")
        files = {str(p.relative_to(payload)): digest(p) for p in sorted(payload.rglob("*")) if p.is_file()}
        manifest = {"schema": SCHEMA, "base": base, "head": head, "branch": branch,
                    "state": declared, "files": files, "delivery_verified": False}
        json_write(payload / "manifest.json", manifest)
        archive = staging / "checkpoint.tar.xz"
        with tarfile.open(archive, "w:xz") as stream:
            stream.add(payload, arcname="payload")
        checksum = digest(archive)
        encoded = base64.b64encode(archive.read_bytes()).decode("ascii")
        (staging / "checkpoint.recovery.txt").write_text(
            f"{TEXT_HEADER}\nsha256:{checksum}\n\n" + "\n".join(textwrap.wrap(encoded, 76)) + "\n",
            encoding="ascii",
        )
        shutil.copyfile(payload / "changes.patch.txt", staging / "changes.patch.txt")
        record = {**manifest, "archive_sha256": checksum,
                  "transport_sha256": digest(staging / "checkpoint.recovery.txt")}
        json_write(staging / "CHECKPOINT.json", record)
        if git(repository, "rev-parse", "HEAD").decode().strip() != head or git(
            repository, "status", "--porcelain", "--untracked-files=all").strip():
            raise CheckpointError("Checkout changed during checkpoint; retry from an explicit stable candidate")
        # Output becomes visible only after all source and content checks succeed.
        require_new(output)
        staging.rename(output)
    return {"status": "created", "head": head, "checkpoint": str(output), "delivery_verified": False}


def archive_bytes(path: Path) -> bytes:
    if path.is_dir():
        path = path / "checkpoint.tar.xz"
    if path.suffix != ".txt":
        return path.read_bytes()
    text = path.read_text(encoding="ascii")
    header, checksum, body = text.split("\n", 2)
    if header != TEXT_HEADER or not checksum.startswith("sha256:"):
        raise CheckpointError("Not a supported recovery text transport")
    data = base64.b64decode("".join(body.split()), validate=True)
    if hashlib.sha256(data).hexdigest() != checksum.removeprefix("sha256:"):
        raise CheckpointError("Recovery text checksum mismatch (truncated or damaged transport)")
    return data


def unpack_checked(data: bytes, destination: Path) -> tuple[Path, dict]:
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:xz") as stream:
        seen = set()
        for entry in stream.getmembers():
            path = PurePosixPath(entry.name)
            if path.is_absolute() or ".." in path.parts or not path.parts or path.parts[0] != "payload":
                raise CheckpointError("Unsafe archive path")
            if not (entry.isfile() or entry.isdir()) or entry.name in seen:
                raise CheckpointError("Links, special files and duplicate entries are not allowed")
            seen.add(entry.name)
        stream.extractall(destination, filter="data")
    payload = destination / "payload"
    manifest = json.loads((payload / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("schema") != SCHEMA or not isinstance(manifest.get("files"), dict):
        raise CheckpointError("Unsupported checkpoint manifest")
    actual = {str(p.relative_to(payload)): digest(p) for p in payload.rglob("*")
              if p.is_file() and p != payload / "manifest.json"}
    if actual != manifest["files"]:
        raise CheckpointError("Checkpoint file membership or SHA-256 mismatch")
    if json.loads((payload / "state.json").read_text(encoding="utf-8")) != manifest["state"]:
        raise CheckpointError("Manifest disagrees with recovery state")
    check_database(payload / "tasks.sqlite")
    return payload, manifest


def restore(checkpoint: Path, destination: Path) -> dict:
    destination = destination.absolute()
    require_new(destination)
    data = archive_bytes(checkpoint)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".restore-", dir=destination.parent) as temp:
        staging = Path(temp) / "restored"
        staging.mkdir()
        payload, manifest = unpack_checked(data, staging)
        restored = staging / "repository"
        restored.mkdir()
        git(restored, "init", "--quiet")
        git(restored, "bundle", "verify", str(payload / "repository.bundle"))
        git(restored, "fetch", "--quiet", str(payload / "repository.bundle"), "HEAD")
        if git(restored, "rev-parse", "FETCH_HEAD").decode().strip() != manifest["head"]:
            raise CheckpointError("Git bundle does not contain the recorded HEAD")
        git(restored, "check-ref-format", "--branch", manifest["branch"])
        git(restored, "checkout", "--quiet", "-b", manifest["branch"], manifest["head"])
        git(restored, "merge-base", "--is-ancestor", manifest["base"], "HEAD")
        if set(git(restored, "rev-list", "HEAD").decode().splitlines()).intersection(
            manifest["state"].get("rejected_heads", [])
        ):
            raise CheckpointError("Rejected history found during restore")
        git(restored, "fsck", "--full", "--no-reflogs")
        if git(restored, "status", "--porcelain").strip():
            raise CheckpointError("Restored tree is not clean")
        require_new(destination)
        staging.rename(destination)
    return {"status": "restored", "head": manifest["head"], "destination": str(destination),
            "next_action": manifest["state"]["next_action"]}


def verify(checkpoint: Path) -> dict:
    with tempfile.TemporaryDirectory(prefix="checkpoint-verify-") as temp:
        result = restore(checkpoint, Path(temp) / "restored")
    return {"status": "verified", "head": result["head"], "next_action": result["next_action"]}


def seal_delivery(checkpoint: Path, message_id: str, readback: Path) -> dict:
    if not message_id.strip():
        raise CheckpointError("A confirmed Gmail message ID is required")
    record = json.loads((checkpoint / "CHECKPOINT.json").read_text(encoding="utf-8"))
    if digest(readback) != record["transport_sha256"]:
        raise CheckpointError("Complete transport readback does not match the sent checkpoint")
    # Validate structure/content too, not just the separately stored hash.
    verified = verify(readback)
    if verified["head"] != record["head"]:
        raise CheckpointError("Readback belongs to another checkpoint")
    receipt = {"schema": "ai-poise-checkpoint-delivery-1", "message_id": message_id,
               "head": record["head"], "transport_sha256": record["transport_sha256"],
               "readback_verified": True}
    json_write(checkpoint / "DELIVERY.json", receipt)
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    create_parser = commands.add_parser("create")
    for arg in ("repository", "database-snapshot", "state", "output"):
        create_parser.add_argument("--" + arg, type=Path, required=True)
    create_parser.add_argument("--base", required=True)
    create_parser.add_argument("--evidence", type=Path, action="append", default=[])
    for action in ("verify", "restore"):
        sub = commands.add_parser(action)
        sub.add_argument("--checkpoint", type=Path, required=True)
        if action == "restore":
            sub.add_argument("--destination", type=Path, required=True)
    delivery = commands.add_parser("seal-delivery")
    delivery.add_argument("--checkpoint", type=Path, required=True)
    delivery.add_argument("--message-id", required=True)
    delivery.add_argument("--readback", type=Path, required=True)
    args = vars(parser.parse_args())
    action = args.pop("action")
    try:
        result = {"create": create, "verify": verify, "restore": restore,
                  "seal-delivery": seal_delivery}[action](**args)
    except (ValueError, OSError, sqlite3.Error, tarfile.TarError, KeyError, TypeError) as exc:
        print(json.dumps({"status": "rejected", "reason": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
