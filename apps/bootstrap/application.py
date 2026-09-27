"""Transport-only adapter to the independent environment-maintenance component."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys


_LOCK_SCHEMA = "ai-poise/bootstrap-component/v1"
_ERROR_SCHEMA = "ai-poise/bootstrap-error/v1"
_LOCK_FIELDS = frozenset({
    "schema",
    "distribution",
    "version",
    "accepted_commit",
    "wheel_sha256",
    "delivery_manifest_sha256",
})
_COMMANDS = {"infra": "run", "deps": "run", "check": "check"}

_PROBE = """\
import importlib.metadata
import importlib.util
import sys

distribution, expected_version = sys.argv[1:]
try:
    actual_version = importlib.metadata.version(distribution)
except importlib.metadata.PackageNotFoundError:
    raise SystemExit(10)
if actual_version != expected_version:
    raise SystemExit(11)
try:
    module = importlib.util.find_spec("environment_maintenance")
except (ImportError, ValueError):
    module = None
raise SystemExit(0 if module is not None else 12)
"""


class LockFailure(Exception):
    def __init__(self, code: str, cause: str) -> None:
        super().__init__(cause)
        self.code = code


def _unique_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    values: dict[str, object] = {}
    for name, value in pairs:
        if name in values:
            raise ValueError(f"duplicate lock field: {name}")
        values[name] = value
    return values


def _valid_lock_value(value: object) -> bool:
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        return False
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def _load_lock() -> dict[str, str]:
    path = Path(__file__).with_name("component.json")
    try:
        source = path.read_bytes()
    except FileNotFoundError as exc:
        raise LockFailure("component_lock_missing", "component lock is missing") from exc
    except OSError as exc:
        raise LockFailure("component_lock_unreadable", "component lock cannot be read") from exc
    try:
        lock = json.loads(source.decode("utf-8"), object_pairs_hook=_unique_pairs)
    except (UnicodeDecodeError, ValueError, TypeError, RecursionError) as exc:
        raise LockFailure("component_lock_invalid", "component lock is invalid JSON or UTF-8") from exc
    if not isinstance(lock, dict) or set(lock) != _LOCK_FIELDS:
        raise LockFailure("component_lock_invalid", "component lock fields do not match the schema")
    if lock["schema"] != _LOCK_SCHEMA or not all(_valid_lock_value(value) for value in lock.values()):
        raise LockFailure("component_lock_invalid", "component lock values do not match the schema")
    return lock


def _error(code: str, command: str, cause: str, recommendations: list[str], exit_code: int) -> int:
    packet = {
        "schema": _ERROR_SCHEMA,
        "source": "bootstrap",
        "status": "error",
        "code": code,
        "command": command,
        "cause": cause,
        "recommendations": recommendations,
        "exit_code": exit_code,
    }
    sys.stdout.buffer.write((json.dumps(packet, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8"))
    return exit_code


def _parse(arguments: list[str]) -> tuple[str, list[str]]:
    parser = argparse.ArgumentParser(prog="bootstrap")
    parser.add_argument("command", choices=tuple(_COMMANDS))
    parser.add_argument("--catalog-dir", required=True)
    parser.add_argument("--values", action="append")
    parser.add_argument("--set", action="append")
    parser.add_argument("--dry-run", action="store_true")
    if not arguments or arguments[0] not in _COMMANDS:
        parser.error("first argument must be infra, deps, or check")
    parsed = parser.parse_args(arguments)
    if parsed.catalog_dir == "":
        parser.error("--catalog-dir must name a directory")
    return parsed.command, arguments[1:]


def main(arguments: list[str] | None = None) -> int:
    requested = list(sys.argv[1:] if arguments is None else arguments)
    command, forwarded = _parse(requested)
    try:
        lock = _load_lock()
    except LockFailure as exc:
        return _error(
            exc.code, command, str(exc),
            ["Restore the verified apps/bootstrap/component.json lock and retry."], 2,
        )

    try:
        probe = subprocess.run(
            [sys.executable, "-I", "-B", "-c", _PROBE, lock["distribution"], lock["version"]],
            capture_output=True, check=False,
        )
    except OSError as exc:
        return _error(
            "component_spawn_failed", command, f"component probe could not start: {exc}",
            ["Restore the selected Python interpreter and retry."], 4,
        )
    if probe.returncode in (10, 12):
        return _error(
            "component_missing", command, "component is unavailable to the isolated Python interpreter",
            ["Install the accepted environment-maintenance distribution into this interpreter."], 3,
        )
    if probe.returncode == 11:
        return _error(
            "component_version_mismatch", command, "installed component version differs from the lock",
            ["Install the exact distribution version recorded by the component lock."], 3,
        )
    if probe.returncode != 0:
        return _error(
            "component_spawn_failed", command, f"component probe terminated with code {probe.returncode}",
            ["Inspect the isolated interpreter and component installation, then retry."], 4,
        )

    try:
        action = subprocess.run(
            [sys.executable, "-I", "-B", "-m", "environment_maintenance", _COMMANDS[command], *forwarded],
            capture_output=True, check=False,
        )
    except OSError as exc:
        return _error(
            "component_spawn_failed", command, f"component action could not start: {exc}",
            ["Restore the selected Python interpreter and retry."], 4,
        )
    if action.returncode < 0:
        return _error(
            "component_signaled", command, f"component action ended with signal {-action.returncode}",
            ["Inspect the component process failure before retrying a mutating action."], 4,
        )
    sys.stdout.buffer.write(action.stdout)
    sys.stderr.buffer.write(action.stderr)
    return action.returncode
