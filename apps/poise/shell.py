"""Independent Poise CLI/config foundation; Session ownership follows in T060."""
from __future__ import annotations

import json
import math
from pathlib import Path
import sys


SCHEMA = "stride-poise/v1"
CONFIG_SCHEMA = "stride-poise-config/v1"
CONFIG_FIELDS = {
    "schema", "installation_id", "state_root", "session_store", "lock_path",
    "results_root", "caller", "roles", "authorization", "tools", "limits",
    "paths", "environment",
}
COUNT_LIMITS = {
    "packet_bytes", "result_bytes", "input_bytes", "id_bytes", "batch_count",
    "provider_output_bytes",
}
DURATION_LIMITS = {
    "lock_timeout_seconds", "lock_poll_milliseconds", "provider_timeout_seconds",
}
MESSAGES = {
    "invalid_json": "Invalid JSON packet",
    "unsupported_schema": "Unsupported Poise schema",
    "invalid_packet": "Invalid Poise packet",
    "limit_exceeded": "Configured limit exceeded",
    "session_not_found": "Session was not found",
    "store_incompatible": "Session store requires the Poise Session implementation",
}


class Problem(Exception):
    def __init__(self, code: str, details: dict):
        super().__init__(MESSAGES[code])
        self.code = code
        self.details = details


def invalid(path: str):
    raise Problem("invalid_packet", {"path": path})


def exact(value, fields: set[str], path: str):
    if not isinstance(value, dict):
        invalid(path)
    missing = sorted(fields - value.keys())
    unknown = value.keys() - fields
    if unknown or len(missing) > 1:
        invalid(path)
    if missing:
        invalid(path.rstrip("/") + "/" + missing[0])


def unique_pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def finite_float(text):
    value = float(text)
    if not math.isfinite(value):
        raise ValueError("Nonfinite JSON number")
    return value


def nonfinite_constant(text):
    raise ValueError("Nonfinite JSON constant")


def loads(raw: bytes):
    value = json.loads(
        raw.decode("utf-8"), object_pairs_hook=unique_pairs,
        parse_float=finite_float, parse_constant=nonfinite_constant,
    )
    # Escaped lone surrogates also cannot represent UTF-8 text on this boundary.
    json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
    return value


def positive_count(value, path: str):
    if type(value) is not int or value <= 0:
        invalid(path)


def positive_duration(value, path: str):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        invalid(path)
    try:
        finite = math.isfinite(value)
    except OverflowError:
        finite = False
    if not finite or value <= 0:
        invalid(path)


def identifier(value, bound: int, path: str):
    if not isinstance(value, str) or not value or value in {".", ".."}:
        invalid(path)
    if any(character in value for character in ("\x00", "/", "\\")):
        invalid(path)
    if len(value.encode("utf-8")) > bound:
        invalid(path)


def resolve_path(base: Path, value, path: str) -> Path:
    if not isinstance(value, str) or not value or "\x00" in value:
        invalid(path)
    try:
        selected = Path(value)
        return (selected if selected.is_absolute() else base / selected).resolve()
    except (OSError, RuntimeError, ValueError):
        invalid(path)


def load_config(path: Path):
    try:
        config = loads(path.read_bytes())
        base = path.resolve().parent
    except (OSError, ValueError, RecursionError, RuntimeError):
        invalid("/config")
    exact(config, CONFIG_FIELDS, "/config")
    if config["schema"] != CONFIG_SCHEMA:
        invalid("/config/schema")
    limits = config["limits"]
    exact(limits, COUNT_LIMITS | DURATION_LIMITS, "/config/limits")
    for name in sorted(COUNT_LIMITS):
        positive_count(limits[name], "/config/limits/" + name)
    for name in sorted(DURATION_LIMITS):
        positive_duration(limits[name], "/config/limits/" + name)
    identifier(config["installation_id"], limits["id_bytes"], "/config/installation_id")
    roles = config["roles"]
    if not isinstance(roles, list) or not roles:
        invalid("/config/roles")
    seen = set()
    for index, role in enumerate(roles):
        label = f"/config/roles/{index}"
        identifier(role, limits["id_bytes"], label)
        if role in seen:
            invalid(label)
        seen.add(role)
    for name in ("caller", "authorization", "tools", "paths", "environment"):
        if not isinstance(config[name], dict):
            invalid("/config/" + name)
    for name in ("state_root", "session_store", "lock_path", "results_root"):
        config[name] = resolve_path(base, config[name], "/config/" + name)
    return config


def read_packet(limit: int):
    remaining = limit + 1
    data = bytearray()
    while remaining:
        chunk = sys.stdin.buffer.read(min(remaining, 65536))
        if not chunk:
            break
        data.extend(chunk)
        remaining -= len(chunk)
    if len(data) > limit:
        raise Problem("limit_exceeded", {"limit": "packet_bytes"})
    try:
        return loads(data)
    except (ValueError, RecursionError):
        raise Problem("invalid_json", {}) from None


def session_status(config):
    try:
        config["session_store"].lstat()
    except FileNotFoundError:
        raise Problem("session_not_found", {}) from None
    except OSError:
        raise Problem("store_incompatible", {"schema": None}) from None
    # T060 owns Session persistence and caller resolution. Do not infer a Session
    # or open an existing file with a guessed SQLite schema in this foundation.
    raise Problem("store_incompatible", {"schema": None})


def main(argv=None):
    arguments = sys.argv[1:] if argv is None else argv
    try:
        if len(arguments) != 2 or arguments[0] != "--config" or not arguments[1]:
            invalid("/config")
        config = load_config(Path(arguments[1]))
        packet = read_packet(config["limits"]["packet_bytes"])
        exact(packet, {"schema", "operation", "input"}, "/")
        if packet["schema"] != SCHEMA:
            value = packet["schema"] if isinstance(packet["schema"], str) else None
            raise Problem("unsupported_schema", {"schema": value})
        if packet["operation"] != "session.status":
            invalid("/operation")
        exact(packet["input"], set(), "/input")
        session_status(config)
    except Problem as problem:
        response = {
            "schema": SCHEMA, "status": "rejected",
            "error": {
                "code": problem.code, "message": str(problem),
                "retryable": False, "details": problem.details,
            },
        }
        print(json.dumps(response, ensure_ascii=False, allow_nan=False, separators=(",", ":")))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
