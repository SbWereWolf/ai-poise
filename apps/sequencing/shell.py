#!/usr/bin/env python3
"""Standalone Sequencing JSON provider with app-owned configuration/state."""
from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
import sys

CONFIG_SCHEMA = "stride-sequencing-config/v1"
WIRE_SCHEMA = "stride-sequencing/v1"


class SequencingError(Exception):
    pass


def _strict_json(raw: str):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise SequencingError(f"duplicate JSON key: {key}")
            result[key] = value
        return result
    try:
        return json.loads(raw, object_pairs_hook=pairs, parse_float=lambda _: (_ for _ in ()).throw(SequencingError("floating-point numbers are not supported")))
    except SequencingError:
        raise
    except json.JSONDecodeError as exc:
        raise SequencingError("invalid_json") from exc


def _exact(value, keys, where):
    required = set(keys)
    if not isinstance(value, dict):
        raise SequencingError(f"{where}: required object")
    missing = sorted(required - set(value))
    unknown = sorted(set(value) - required)
    if missing or unknown:
        raise SequencingError(
            f"{where}: required exact fields {sorted(required)}; missing {missing}; unknown {unknown}"
        )


def _nonempty(value, where):
    if not isinstance(value, str) or not value or "\x00" in value:
        raise SequencingError(f"{where}: required nonempty string")
    return value


def _path_text(value, where):
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise SequencingError(f"{where}: required nonempty path")
    return value


def _positive_int(value, where):
    if type(value) is not int or value <= 0:
        raise SequencingError(f"{where}: required positive integer")
    return value


def _positive_number(value, where):
    if type(value) not in (int, float) or isinstance(value, bool) or value <= 0 or not math.isfinite(value):
        raise SequencingError(f"{where}: required positive finite number")
    return float(value)


def _compile_pattern(value, where):
    text = _nonempty(value, where)
    try:
        return re.compile(text)
    except re.error as exc:
        raise SequencingError(f"{where}: invalid regular expression") from exc


def _resolve_from(base: Path, value: str):
    path = Path(value)
    return (path if path.is_absolute() else base / path).resolve()


def load_config(config_path):
    path = Path(config_path).resolve()
    try:
        data = _strict_json(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise SequencingError(f"Sequencing config cannot be read: {exc}") from exc
    _exact(data, {"schema", "paths", "batch", "read", "lock", "repository", "worktree", "transfer", "identifiers"}, "Sequencing config")
    if data["schema"] != CONFIG_SCHEMA:
        raise SequencingError("Sequencing config schema is unsupported")

    _exact(data["paths"], {"state", "database", "lock"}, "Sequencing config paths")
    _exact(data["batch"], {"max_input_bytes", "max_items"}, "Sequencing config batch")
    _exact(data["read"], {"max_queries"}, "Sequencing config read")
    _exact(data["lock"], {"seconds", "poll_seconds"}, "Sequencing config lock")
    _exact(data["repository"], {"id", "path", "base_ref"}, "Sequencing config repository")
    _exact(data["worktree"], {"root", "branch_prefix", "fs_timeout_seconds", "git_timeout_seconds"}, "Sequencing config worktree")
    _exact(data["transfer"], {"root", "max_package_bytes", "max_files", "max_file_bytes", "file_mode"}, "Sequencing config transfer")
    _exact(data["identifiers"], {"task", "sprint"}, "Sequencing config identifiers")
    _exact(data["identifiers"]["task"], {"pattern"}, "Sequencing config identifiers.task")
    _exact(data["identifiers"]["sprint"], {"pattern"}, "Sequencing config identifiers.sprint")

    state = _resolve_from(path.parent, _path_text(data["paths"]["state"], "Sequencing config paths.state"))
    database_rel = Path(_path_text(data["paths"]["database"], "Sequencing config paths.database"))
    lock_rel = Path(_path_text(data["paths"]["lock"], "Sequencing config paths.lock"))
    if database_rel.is_absolute() or lock_rel.is_absolute():
        raise SequencingError("Sequencing config database/lock paths must be relative to state")
    database = (state / database_rel).resolve()
    lock_path = (state / lock_rel).resolve()
    if not database.is_relative_to(state) or not lock_path.is_relative_to(state):
        raise SequencingError("Sequencing config database/lock paths must stay inside state")
    if database == lock_path:
        raise SequencingError("Sequencing config database and lock must be distinct")

    repository_path = _resolve_from(path.parent, _path_text(data["repository"]["path"], "Sequencing config repository.path"))
    worktree_root = _resolve_from(path.parent, _path_text(data["worktree"]["root"], "Sequencing config worktree.root"))
    transfer_root = _resolve_from(path.parent, _path_text(data["transfer"]["root"], "Sequencing config transfer.root"))
    file_mode = data["transfer"]["file_mode"]
    if type(file_mode) is not int or not 0 <= file_mode <= 0o777:
        raise SequencingError("Sequencing config transfer.file_mode: required integer between 0 and 511")

    return {
        "state": state,
        "database": database,
        "lock_path": lock_path,
        "max_input_bytes": _positive_int(data["batch"]["max_input_bytes"], "Sequencing config batch.max_input_bytes"),
        "max_items": _positive_int(data["batch"]["max_items"], "Sequencing config batch.max_items"),
        "max_queries": _positive_int(data["read"]["max_queries"], "Sequencing config read.max_queries"),
        "lock_seconds": _positive_number(data["lock"]["seconds"], "Sequencing config lock.seconds"),
        "poll_seconds": _positive_number(data["lock"]["poll_seconds"], "Sequencing config lock.poll_seconds"),
        "repository_id": _nonempty(data["repository"]["id"], "Sequencing config repository.id"),
        "repository_path": repository_path,
        "base_ref": _nonempty(data["repository"]["base_ref"], "Sequencing config repository.base_ref"),
        "worktree_root": worktree_root,
        "branch_prefix": _nonempty(data["worktree"]["branch_prefix"], "Sequencing config worktree.branch_prefix"),
        "fs_timeout_seconds": _positive_number(data["worktree"]["fs_timeout_seconds"], "Sequencing config worktree.fs_timeout_seconds"),
        "git_timeout_seconds": _positive_number(data["worktree"]["git_timeout_seconds"], "Sequencing config worktree.git_timeout_seconds"),
        "transfer_root": transfer_root,
        "max_package_bytes": _positive_int(data["transfer"]["max_package_bytes"], "Sequencing config transfer.max_package_bytes"),
        "max_files": _positive_int(data["transfer"]["max_files"], "Sequencing config transfer.max_files"),
        "max_file_bytes": _positive_int(data["transfer"]["max_file_bytes"], "Sequencing config transfer.max_file_bytes"),
        "file_mode": file_mode,
        "task_pattern": _compile_pattern(data["identifiers"]["task"]["pattern"], "identifiers.task.pattern"),
        "sprint_pattern": _compile_pattern(data["identifiers"]["sprint"]["pattern"], "identifiers.sprint.pattern"),
    }


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _packet(value, config):
    _exact(value, {"schema", "operation", "input"}, "Sequencing packet")
    if value["schema"] != WIRE_SCHEMA:
        raise SequencingError("unsupported_schema")
    if not isinstance(value["input"], dict):
        raise SequencingError("Sequencing packet input must be an object")
    if value["operation"] not in {"apply", "query"}:
        raise SequencingError(f"Unknown Sequencing operation: {value['operation']}")
    if value["operation"] == "apply":
        _exact(value["input"], {"request_id", "expected_revision", "operations"}, "Sequencing apply input")
        _nonempty(value["input"]["request_id"], "Sequencing apply request_id")
        if type(value["input"]["expected_revision"]) is not int or value["input"]["expected_revision"] < 0:
            raise SequencingError("Sequencing apply expected_revision: required nonnegative integer")
        ops = value["input"]["operations"]
        if not isinstance(ops, list) or not ops or len(ops) > config["max_items"]:
            raise SequencingError("Sequencing apply operations: required nonempty bounded list")
        for op in ops:
            _exact(op, {"id", "kind", "task_id", "requirements_snapshot_revision"}, "Sequencing register_task")
            _nonempty(op["id"], "Sequencing operation id")
            if op["kind"] != "register_task":
                raise SequencingError(f"Unsupported Sequencing operation kind: {op['kind']}")
            task_id = _nonempty(op["task_id"], "Sequencing register_task task_id")
            if config["task_pattern"].fullmatch(task_id) is None:
                raise SequencingError("Sequencing register_task task_id violates configured identifier policy")
            if type(op["requirements_snapshot_revision"]) is not int or op["requirements_snapshot_revision"] < 1:
                raise SequencingError("Sequencing register_task requirements_snapshot_revision: required positive integer")
    else:
        _exact(value["input"], {"queries"}, "Sequencing query input")
        queries = value["input"]["queries"]
        if not isinstance(queries, list) or not queries or len(queries) > config["max_queries"]:
            raise SequencingError("Sequencing query queries: required nonempty bounded list")
        seen = set()
        for query in queries:
            _exact(query, {"id", "kind", "task_id"}, "Sequencing task query")
            qid = _nonempty(query["id"], "Sequencing query id")
            if qid in seen:
                raise SequencingError("Sequencing query ids must be unique")
            seen.add(qid)
            if query["kind"] != "task":
                raise SequencingError(f"Unsupported Sequencing query kind: {query['kind']}")
            task_id = _nonempty(query["task_id"], "Sequencing query task_id")
            if config["task_pattern"].fullmatch(task_id) is None:
                raise SequencingError("Sequencing query task_id violates configured identifier policy")
    return value


class Store:
    def __init__(self, config):
        self.config = config

    @contextlib.contextmanager
    def locked(self):
        state = self.config["state"]
        state.mkdir(parents=True, exist_ok=True)
        self.config["lock_path"].parent.mkdir(parents=True, exist_ok=True)
        with self.config["lock_path"].open("a+b") as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
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
            CREATE TABLE IF NOT EXISTS tasks (
              task_id TEXT PRIMARY KEY,
              requirements_snapshot_revision INTEGER NOT NULL,
              state TEXT NOT NULL,
              sprint_id TEXT,
              claim_json TEXT,
              workspace_json TEXT,
              terminal_json TEXT
            );
            CREATE TABLE IF NOT EXISTS requests (
              request_id TEXT PRIMARY KEY,
              digest TEXT NOT NULL,
              response_json TEXT NOT NULL
            );
            """
        )
        return conn

    @staticmethod
    def revision(conn):
        return int(conn.execute("SELECT value FROM meta WHERE key='revision'").fetchone()[0])

    def apply(self, packet):
        request_id = packet["input"]["request_id"]
        digest = hashlib.sha256(_canonical(packet)).hexdigest()
        with self.locked():
            conn = self.connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                prior = conn.execute("SELECT digest,response_json FROM requests WHERE request_id=?", (request_id,)).fetchone()
                if prior is not None:
                    if prior["digest"] != digest:
                        raise SequencingError("request_reuse_conflict")
                    saved = json.loads(prior["response_json"])
                    saved["replayed"] = True
                    conn.rollback()
                    return saved
                current = self.revision(conn)
                if packet["input"]["expected_revision"] != current:
                    raise SequencingError("revision_conflict")
                results = []
                for op in packet["input"]["operations"]:
                    row = conn.execute(
                        "SELECT requirements_snapshot_revision FROM tasks WHERE task_id=?", (op["task_id"],)
                    ).fetchone()
                    changed = row is None
                    if row is None:
                        conn.execute(
                            "INSERT INTO tasks(task_id,requirements_snapshot_revision,state,sprint_id,claim_json,workspace_json,terminal_json) VALUES(?,?, 'available',NULL,NULL,NULL,NULL)",
                            (op["task_id"], op["requirements_snapshot_revision"]),
                        )
                    elif int(row["requirements_snapshot_revision"]) != op["requirements_snapshot_revision"]:
                        raise SequencingError("identity_conflict")
                    results.append({
                        "id": op["id"], "kind": "register_task", "changed": changed,
                        "task_id": op["task_id"],
                        "requirements_snapshot_revision": op["requirements_snapshot_revision"],
                    })
                revision = current + 1
                response = {"schema": WIRE_SCHEMA, "status": "applied", "revision": revision, "replayed": False, "results": results}
                conn.execute("UPDATE meta SET value=? WHERE key='revision'", (revision,))
                conn.execute("INSERT INTO requests(request_id,digest,response_json) VALUES(?,?,?)", (request_id, digest, json.dumps(response, ensure_ascii=False, separators=(",", ":"))))
                conn.commit()
                return response
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()

    def query(self, packet):
        with self.locked():
            conn = self.connect()
            try:
                revision = self.revision(conn)
                results = []
                for query in packet["input"]["queries"]:
                    row = conn.execute(
                        "SELECT task_id,requirements_snapshot_revision,state,sprint_id,claim_json,workspace_json,terminal_json FROM tasks WHERE task_id=?",
                        (query["task_id"],),
                    ).fetchone()
                    if row is None:
                        raise SequencingError("task_not_found")
                    results.append({"id": query["id"], "value": {
                        "task_id": row["task_id"],
                        "requirements_snapshot_revision": int(row["requirements_snapshot_revision"]),
                        "state": row["state"],
                        "sprint_id": row["sprint_id"],
                        "claim": json.loads(row["claim_json"]) if row["claim_json"] else None,
                        "workspace": json.loads(row["workspace_json"]) if row["workspace_json"] else None,
                        "terminal": json.loads(row["terminal_json"]) if row["terminal_json"] else None,
                    }})
                return {"schema": WIRE_SCHEMA, "status": "read_only", "revision": revision, "results": results}
            finally:
                conn.close()


def execute(config_path, stream, output):
    try:
        config = load_config(config_path)
        raw = stream.read(config["max_input_bytes"] + 1)
        if len(raw) > config["max_input_bytes"]:
            raise SequencingError("Sequencing packet exceeds max_input_bytes")
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise SequencingError("invalid_json") from exc
        packet = _packet(_strict_json(text), config)
        store = Store(config)
        result = store.apply(packet) if packet["operation"] == "apply" else store.query(packet)
        code = 0
    except (SequencingError, OSError, sqlite3.Error) as exc:
        result = {"schema": WIRE_SCHEMA, "status": "rejected", "reason": str(exc)}
        code = 2
    output.write(json.dumps(result, ensure_ascii=False, separators=(",", ":")) + "\n")
    return code


def main(argv=None):
    parser = argparse.ArgumentParser(prog="sequencing")
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args(argv)
    return execute(args.config, sys.stdin.buffer, sys.stdout)


if __name__ == "__main__":
    raise SystemExit(main())
