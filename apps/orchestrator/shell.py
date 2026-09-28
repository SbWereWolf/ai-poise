#!/usr/bin/env python3
"""Standalone Orchestrator JSON provider with app-owned configuration/state."""
from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time

CONFIG_SCHEMA = "stride-orchestrator-config/v1"
WIRE_SCHEMA = "stride-orchestrator/v1"


class OrchestratorError(Exception):
    pass


def _strict_json(raw: str, *, allow_float: bool = False):
    def pairs(items):
        out = {}
        for key, value in items:
            if key in out:
                raise OrchestratorError(f"duplicate JSON key: {key}")
            out[key] = value
        return out

    def parse_float(text):
        if allow_float:
            value = float(text)
            if not math.isfinite(value):
                raise OrchestratorError("non-finite numbers are not supported")
            return value
        raise OrchestratorError("floating-point numbers are not supported")

    def bad_constant(_):
        raise OrchestratorError("non-finite numbers are not supported")

    try:
        return json.loads(raw, object_pairs_hook=pairs, parse_float=parse_float, parse_constant=bad_constant)
    except OrchestratorError:
        raise
    except json.JSONDecodeError as exc:
        raise OrchestratorError("invalid_json") from exc


def _exact(value, keys, where):
    required = set(keys)
    if not isinstance(value, dict):
        raise OrchestratorError(f"{where}: required object")
    missing = sorted(required - set(value))
    unknown = sorted(set(value) - required)
    if missing or unknown:
        raise OrchestratorError(
            f"{where}: required exact fields {sorted(required)}; missing {missing}; unknown {unknown}"
        )


def _nonempty(value, where):
    if not isinstance(value, str) or not value or "\x00" in value:
        raise OrchestratorError(f"{where}: required nonempty string")
    return value


def _path_text(value, where):
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise OrchestratorError(f"{where}: required nonempty path")
    return value


def _positive_int(value, where):
    if type(value) is not int or value <= 0:
        raise OrchestratorError(f"{where}: required positive integer")
    return value


def _positive_number(value, where):
    if type(value) not in (int, float) or isinstance(value, bool) or value <= 0 or not math.isfinite(value):
        raise OrchestratorError(f"{where}: required positive finite number")
    return float(value)


def _resolve_from(base: Path, value: str):
    path = Path(value)
    return (path if path.is_absolute() else base / path).resolve()


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _collection_items(value):
    if isinstance(value, dict):
        return len(value) + sum(_collection_items(v) for v in value.values())
    if isinstance(value, list):
        return len(value) + sum(_collection_items(v) for v in value)
    return 0


def _command_descriptor(command_id, value, config_dir):
    where = f"Orchestrator command {command_id}"
    _exact(value, {"protocol", "argv", "cwd", "env", "timeout_seconds", "accepted_exit_codes"}, where)
    if value["protocol"] != "owner-json-v1":
        raise OrchestratorError(f"{where}: unsupported protocol")
    argv = value["argv"]
    if not isinstance(argv, list) or not argv:
        raise OrchestratorError(f"{where}.argv: required nonempty list")
    normalized_argv = []
    for index, item in enumerate(argv):
        normalized_argv.append(_nonempty(item, f"{where}.argv[{index}]"))
    executable = normalized_argv[0]
    if not Path(executable).is_absolute() and "/" not in executable:
        raise OrchestratorError(f"{where}.argv[0]: explicit executable path required")
    cwd_text = _path_text(value["cwd"], f"{where}.cwd")
    cwd = _resolve_from(config_dir, cwd_text)
    env = value["env"]
    if not isinstance(env, dict):
        raise OrchestratorError(f"{where}.env: required object")
    normalized_env = {}
    for key, item in env.items():
        normalized_env[_nonempty(key, f"{where}.env key")] = _nonempty(item, f"{where}.env[{key}]")
    timeout = _positive_number(value["timeout_seconds"], f"{where}.timeout_seconds")
    codes = value["accepted_exit_codes"]
    if not isinstance(codes, list) or not codes or any(type(code) is not int for code in codes) or len(set(codes)) != len(codes):
        raise OrchestratorError(f"{where}.accepted_exit_codes: required nonempty unique integer list")
    return {
        "protocol": "owner-json-v1",
        "argv": normalized_argv,
        "cwd": cwd,
        "env": normalized_env,
        "timeout_seconds": timeout,
        "accepted_exit_codes": list(codes),
    }


def load_config(config_path):
    path = Path(config_path).resolve()
    try:
        data = _strict_json(path.read_text(encoding="utf-8"), allow_float=True)
    except OSError as exc:
        raise OrchestratorError(f"Orchestrator config cannot be read: {exc}") from exc
    _exact(data, {"schema", "paths", "batch", "read", "lock", "commands"}, "Orchestrator config")
    if data["schema"] != CONFIG_SCHEMA:
        raise OrchestratorError("Orchestrator config schema is unsupported")
    _exact(data["paths"], {"state", "database", "lock"}, "Orchestrator config paths")
    _exact(data["batch"], {"max_input_bytes", "max_items"}, "Orchestrator config batch")
    _exact(data["read"], {"max_queries"}, "Orchestrator config read")
    _exact(data["lock"], {"seconds", "poll_seconds"}, "Orchestrator config lock")
    if not isinstance(data["commands"], dict) or not data["commands"]:
        raise OrchestratorError("Orchestrator config commands: required nonempty object")

    state = _resolve_from(path.parent, _path_text(data["paths"]["state"], "Orchestrator config paths.state"))
    database_rel = Path(_path_text(data["paths"]["database"], "Orchestrator config paths.database"))
    lock_rel = Path(_path_text(data["paths"]["lock"], "Orchestrator config paths.lock"))
    if database_rel.is_absolute() or lock_rel.is_absolute():
        raise OrchestratorError("Orchestrator config database/lock paths must be relative to state")
    database = (state / database_rel).resolve()
    lock_path = (state / lock_rel).resolve()
    if not database.is_relative_to(state) or not lock_path.is_relative_to(state):
        raise OrchestratorError("Orchestrator config database/lock paths must stay inside state")
    if database == lock_path:
        raise OrchestratorError("Orchestrator config database and lock must be distinct")

    commands = {}
    for command_id, descriptor in data["commands"].items():
        cid = _nonempty(command_id, "Orchestrator command id")
        commands[cid] = _command_descriptor(cid, descriptor, path.parent)

    config_revision = hashlib.sha256(_canonical(data)).hexdigest()
    return {
        "state": state,
        "database": database,
        "lock_path": lock_path,
        "max_input_bytes": _positive_int(data["batch"]["max_input_bytes"], "Orchestrator config batch.max_input_bytes"),
        "max_items": _positive_int(data["batch"]["max_items"], "Orchestrator config batch.max_items"),
        "max_queries": _positive_int(data["read"]["max_queries"], "Orchestrator config read.max_queries"),
        "lock_seconds": _positive_number(data["lock"]["seconds"], "Orchestrator config lock.seconds"),
        "poll_seconds": _positive_number(data["lock"]["poll_seconds"], "Orchestrator config lock.poll_seconds"),
        "commands": commands,
        "config_revision": config_revision,
    }


def _packet(value, config):
    _exact(value, {"schema", "operation", "input"}, "Orchestrator packet")
    if value["schema"] != WIRE_SCHEMA:
        raise OrchestratorError("unsupported_schema")
    if not isinstance(value["input"], dict):
        raise OrchestratorError("Orchestrator packet input must be an object")
    operation = value["operation"]
    if operation == "invoke":
        _exact(value["input"], {"request_id", "command_id", "packet"}, "Orchestrator invoke input")
        _nonempty(value["input"]["request_id"], "Orchestrator invoke request_id")
        command_id = _nonempty(value["input"]["command_id"], "Orchestrator invoke command_id")
        if command_id not in config["commands"]:
            raise OrchestratorError(f"Orchestrator command is not configured: {command_id}")
        packet = value["input"]["packet"]
        if not isinstance(packet, dict):
            raise OrchestratorError("Orchestrator invoke packet must be an object")
        if _collection_items(packet) > config["max_items"]:
            raise OrchestratorError("Orchestrator invoke packet exceeds max_items")
    elif operation == "query":
        _exact(value["input"], {"queries"}, "Orchestrator query input")
        queries = value["input"]["queries"]
        if not isinstance(queries, list) or not queries or len(queries) > config["max_queries"]:
            raise OrchestratorError("Orchestrator query queries: required nonempty bounded list")
        seen = set()
        for query in queries:
            _exact(query, {"id", "kind"}, "Orchestrator query")
            qid = _nonempty(query["id"], "Orchestrator query id")
            if qid in seen:
                raise OrchestratorError("Orchestrator query ids must be unique")
            seen.add(qid)
            if query["kind"] != "commands":
                raise OrchestratorError(f"Unsupported Orchestrator query kind: {query['kind']}")
    else:
        raise OrchestratorError(f"Unknown Orchestrator operation: {operation}")
    return value


class Store:
    def __init__(self, config):
        self.config = config

    @contextlib.contextmanager
    def locked(self):
        self.config["state"].mkdir(parents=True, exist_ok=True)
        self.config["lock_path"].parent.mkdir(parents=True, exist_ok=True)
        with self.config["lock_path"].open("a+b") as lock_file:
            deadline = time.monotonic() + self.config["lock_seconds"]
            while True:
                try:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError as exc:
                    if time.monotonic() >= deadline:
                        raise OrchestratorError("Orchestrator lock timeout") from exc
                    time.sleep(self.config["poll_seconds"])
            try:
                yield
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    def connect(self):
        conn = sqlite3.connect(self.config["database"])
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value INTEGER NOT NULL);
            INSERT OR IGNORE INTO meta(key,value) VALUES('revision',0);
            CREATE TABLE IF NOT EXISTS requests (
              request_id TEXT PRIMARY KEY,
              digest TEXT NOT NULL,
              descriptor_digest TEXT NOT NULL,
              response_json TEXT NOT NULL
            );
            """
        )
        return conn

    @staticmethod
    def revision(conn):
        return int(conn.execute("SELECT value FROM meta WHERE key='revision'").fetchone()[0])

    def invoke(self, packet):
        request_id = packet["input"]["request_id"]
        command_id = packet["input"]["command_id"]
        descriptor = self.config["commands"][command_id]
        digest = hashlib.sha256(_canonical(packet)).hexdigest()
        descriptor_digest = hashlib.sha256(_canonical({
            "protocol": descriptor["protocol"],
            "argv": descriptor["argv"],
            "cwd": str(descriptor["cwd"]),
            "env": descriptor["env"],
            "timeout_seconds": descriptor["timeout_seconds"],
            "accepted_exit_codes": descriptor["accepted_exit_codes"],
        })).hexdigest()
        with self.locked():
            conn = self.connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                prior = conn.execute(
                    "SELECT digest,descriptor_digest,response_json FROM requests WHERE request_id=?",
                    (request_id,),
                ).fetchone()
                if prior is not None:
                    if prior["digest"] != digest or prior["descriptor_digest"] != descriptor_digest:
                        raise OrchestratorError("Orchestrator request replay conflicts with saved request")
                    saved = json.loads(prior["response_json"])
                    saved["replayed"] = True
                    conn.rollback()
                    return saved

                child_input = json.dumps(packet["input"]["packet"], ensure_ascii=False, separators=(",", ":")) + "\n"
                try:
                    completed = subprocess.run(
                        descriptor["argv"],
                        input=child_input,
                        text=True,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        cwd=str(descriptor["cwd"]),
                        env=dict(descriptor["env"]),
                        timeout=descriptor["timeout_seconds"],
                        check=False,
                    )
                except subprocess.TimeoutExpired as exc:
                    raise OrchestratorError(f"Orchestrator command {command_id} timed out") from exc
                except OSError as exc:
                    raise OrchestratorError(f"Orchestrator command {command_id} could not start: {exc}") from exc
                if completed.returncode not in descriptor["accepted_exit_codes"]:
                    raise OrchestratorError(
                        f"Orchestrator command {command_id} returned non-accepted exit code {completed.returncode}"
                    )
                try:
                    child_packet = _strict_json(completed.stdout, allow_float=True)
                except OrchestratorError as exc:
                    raise OrchestratorError(f"Orchestrator command {command_id} returned invalid owner-json-v1 output") from exc
                if not isinstance(child_packet, dict):
                    raise OrchestratorError(f"Orchestrator command {command_id} returned non-object owner-json-v1 output")

                current = self.revision(conn)
                response = {
                    "schema": WIRE_SCHEMA,
                    "status": "delivered",
                    "revision": current + 1,
                    "request_id": request_id,
                    "replayed": False,
                    "result": {
                        "command_id": command_id,
                        "exit_code": completed.returncode,
                        "packet": child_packet,
                        "disposition": "returned",
                    },
                }
                conn.execute("UPDATE meta SET value=? WHERE key='revision'", (current + 1,))
                conn.execute(
                    "INSERT INTO requests(request_id,digest,descriptor_digest,response_json) VALUES(?,?,?,?)",
                    (request_id, digest, descriptor_digest, json.dumps(response, ensure_ascii=False, separators=(",", ":"))),
                )
                conn.commit()
                return response
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()

    def query(self, packet):
        if self.config["database"].is_file():
            with self.locked():
                conn = self.connect()
                try:
                    revision = self.revision(conn)
                finally:
                    conn.close()
        else:
            revision = 0
        results = []
        for query in packet["input"]["queries"]:
            results.append({
                "id": query["id"],
                "value": {
                    "command_ids": sorted(self.config["commands"]),
                    "config_revision": self.config["config_revision"],
                },
            })
        return {"schema": WIRE_SCHEMA, "status": "read_only", "revision": revision, "results": results}


def execute(config_path, stream, output):
    try:
        config = load_config(config_path)
        raw = stream.buffer.read(config["max_input_bytes"] + 1) if hasattr(stream, "buffer") else stream.read(config["max_input_bytes"] + 1)
        if isinstance(raw, str):
            raw_bytes = raw.encode("utf-8")
            text = raw
        else:
            raw_bytes = raw
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise OrchestratorError("invalid_utf8") from exc
        if len(raw_bytes) > config["max_input_bytes"]:
            raise OrchestratorError("Orchestrator packet exceeds max_input_bytes")
        packet = _strict_json(text, allow_float=False)
        _packet(packet, config)
        store = Store(config)
        response = store.invoke(packet) if packet["operation"] == "invoke" else store.query(packet)
        output.write(json.dumps(response, ensure_ascii=False, separators=(",", ":")) + "\n")
        return 0
    except OrchestratorError as exc:
        output.write(json.dumps({"schema": WIRE_SCHEMA, "status": "rejected", "reason": str(exc)}, ensure_ascii=False, separators=(",", ":")) + "\n")
        return 2


def main(argv=None):
    parser = argparse.ArgumentParser(prog="orchestrator")
    parser.add_argument("--config", required=True)
    args = parser.parse_args(argv)
    return execute(args.config, sys.stdin, sys.stdout)


if __name__ == "__main__":
    raise SystemExit(main())
