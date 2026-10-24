#!/usr/bin/env python3
"""Standalone Execution JSON provider with app-owned configuration/state."""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
import sys

from apps.execution.application.records import ExecutionRecords
from apps.execution.application.stage_git import StageGit
from apps.execution.application.lifecycle import ExecutionLifecycle
from apps.execution.application.execution_state import ExecutionState
from apps.execution.application.evidence import ExecutionEvidence
from apps.execution.application.progression import ExecutionProgression
from apps.execution.advance_protocol import packet as advance_packet, query_item as progression_query_item
from apps.execution.lifecycle_protocol import (settings as lifecycle_settings,
    packet as lifecycle_packet, query_item as lifecycle_query_item)
from apps.execution.errors import ExecutionError, StageCommitError
from apps.execution.domain.records import RecordsError
from apps.execution.domain.lifecycle import LifecyclePolicy
from apps.execution.infrastructure.records_store import RecordRepository, record_unit_of_work
from apps.execution.migrations.v1 import prepare as prepare_store
from apps.execution.migrations.v2 import prepare as prepare_process_store
from apps.execution.infrastructure.locking import resource_lock
from apps.execution.infrastructure.process_files import ProcessFiles
from apps.execution.infrastructure.process_store import ProcessUnitOfWork
from apps.execution.application.processes import ProcessCommands, ProcessDefinitions
from apps.execution.domain.process.errors import PoiseError
from apps.execution.domain.process.validation import configuration_fields
from apps.execution.process_protocol import settings as process_settings, packet as process_packet
from apps.execution.gate_protocol import (settings as gate_settings, packet as gate_packet,
                                         query_item as gate_query_item, files_packet)
from apps.execution.application.gates import GateCommands, GateQueries
from apps.execution.infrastructure.gate_files import GateFiles
from apps.execution.infrastructure.stage_files import StageFiles
from apps.execution.infrastructure.git_workspace import GitWorkspace

CONFIG_SCHEMA = "stride-execution-config/v1"
WIRE_SCHEMA = "stride-execution/v1"


def _strict_json(raw: str, *, allow_float: bool = False):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ExecutionError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    def parse_float(text):
        if allow_float:
            value = float(text)
            if not math.isfinite(value):
                raise ExecutionError("non-finite numbers are not supported")
            return value
        raise ExecutionError("floating-point numbers are not supported")

    def bad_constant(_):
        raise ExecutionError("non-finite numbers are not supported")

    try:
        return json.loads(raw, object_pairs_hook=pairs, parse_float=parse_float, parse_constant=bad_constant)
    except ExecutionError:
        raise
    except json.JSONDecodeError as exc:
        raise ExecutionError("invalid_json") from exc


def _exact(value, keys, where):
    required = set(keys)
    if not isinstance(value, dict):
        raise ExecutionError(f"{where}: required object")
    missing = sorted(required - set(value))
    unknown = sorted(set(value) - required)
    if missing or unknown:
        extra_label = 'extra' if where == 'Execution packet' else 'unknown'
        raise ExecutionError(
            f"{where}: required exact fields {sorted(required)}; missing {missing}; {extra_label} {unknown}"
        )


def _config_fields(value, keys, where):
    # Unknown config extensions are retained by their file owner and ignored here.
    if not isinstance(value, dict) or set(keys) - set(value):
        _exact(value, keys, where)
    return {key: value[key] for key in keys}


def _nonempty(value, where):
    if not isinstance(value, str) or not value or "\x00" in value:
        raise ExecutionError(f"{where}: required nonempty string")
    return value


def _path_text(value, where):
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise ExecutionError(f"{where}: required nonempty path")
    return value


def _positive_int(value, where):
    if type(value) is not int or value <= 0:
        raise ExecutionError(f"{where}: required positive integer")
    return value


def _positive_number(value, where):
    if type(value) not in (int, float) or isinstance(value, bool) or value <= 0 or not math.isfinite(value):
        raise ExecutionError(f"{where}: required positive finite number")
    return float(value)


def _compile_pattern(value, where):
    text = _nonempty(value, where)
    try:
        return re.compile(text)
    except re.error as exc:
        raise ExecutionError(f"{where}: invalid regular expression") from exc


def _resolve_from(base: Path, value: str):
    path = Path(value)
    return (path if path.is_absolute() else base / path).resolve()


def load_config(config_path):
    path = Path(config_path).resolve()
    try:
        data = _strict_json(path.read_text(encoding="utf-8"), allow_float=True)
    except OSError as exc:
        raise ExecutionError(f"Execution config cannot be read: {exc}") from exc
    configuration_fields(data, {"schema", "paths", "batch", "read", "lock", "identifiers"}, "Execution config", ExecutionError)
    if data["schema"] != CONFIG_SCHEMA:
        raise ExecutionError("Execution config schema is unsupported")
    _config_fields(data["paths"], {"state", "database", "lock"}, "Execution config paths")
    _config_fields(data["batch"], {"max_input_bytes", "max_items"}, "Execution config batch")
    _config_fields(data["read"], {"max_queries"}, "Execution config read")
    _config_fields(data["lock"], {"seconds", "poll_seconds"}, "Execution config lock")
    _config_fields(data["identifiers"], {"task"}, "Execution config identifiers")
    _config_fields(data["identifiers"]["task"], {"pattern"}, "Execution config identifiers.task")

    state = _resolve_from(path.parent, _path_text(data["paths"]["state"], "Execution config paths.state"))
    database_rel = Path(_path_text(data["paths"]["database"], "Execution config paths.database"))
    lock_rel = Path(_path_text(data["paths"]["lock"], "Execution config paths.lock"))
    if database_rel.is_absolute() or lock_rel.is_absolute():
        raise ExecutionError("Execution config database/lock paths must be relative to state")
    database = (state / database_rel).resolve()
    lock_path = (state / lock_rel).resolve()
    if not database.is_relative_to(state) or not lock_path.is_relative_to(state):
        raise ExecutionError("Execution config database/lock paths must stay inside state")
    if database == lock_path:
        raise ExecutionError("Execution config database and lock must be distinct")

    result = {
        "state": state,
        "database": database,
        "lock_path": lock_path,
        "max_input_bytes": _positive_int(data["batch"]["max_input_bytes"], "Execution config batch.max_input_bytes"),
        "max_items": _positive_int(data["batch"]["max_items"], "Execution config batch.max_items"),
        "max_queries": _positive_int(data["read"]["max_queries"], "Execution config read.max_queries"),
        "lock_seconds": _positive_number(data["lock"]["seconds"], "Execution config lock.seconds"),
        "poll_seconds": _positive_number(data["lock"]["poll_seconds"], "Execution config lock.poll_seconds"),
        "task_pattern": _compile_pattern(data["identifiers"]["task"]["pattern"], "identifiers.task.pattern"),
    }
    if "process" in data:
        result['process'] = process_settings(data['process'], state)
        if result['process']['database'] in [database,lock_path]:
            raise ExecutionError('process database must be distinct from core resources')
    if 'gates' in data:
        result['gates'] = gate_settings(data['gates'], state)
    if 'lifecycle' in data:
        result['lifecycle'] = lifecycle_settings(data['lifecycle'])
    if 'git' in data:
        _config_fields(data['git'], {'seconds', 'observations'}, 'Execution config git')
        relative = Path(_path_text(data['git']['observations'], 'Execution config git.observations'))
        observations = (state / relative).resolve()
        if relative.is_absolute() or not observations.is_relative_to(state):
            raise ExecutionError('git.observations must be relative and inside paths.state')
        result['git'] = {
            'seconds': _positive_number(data['git']['seconds'], 'Execution config git.seconds'),
            'observations': observations,
        }
    return result


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _validate_contract(contract, config):
    _exact(
        contract,
        {"task_id", "requirements_snapshot_revision", "process_ref", "admission_id", "workspace_id",
         "stage_expectations", "restart_policy_id", "methods", "evidence_plan"},
        "ExecutionContract",
    )
    task_id = _nonempty(contract["task_id"], "ExecutionContract.task_id")
    if config["task_pattern"].fullmatch(task_id) is None:
        raise ExecutionError("ExecutionContract.task_id violates configured identifier policy")
    if type(contract["requirements_snapshot_revision"]) is not int or contract["requirements_snapshot_revision"] < 1:
        raise ExecutionError("ExecutionContract.requirements_snapshot_revision: required positive integer")
    _exact(contract["process_ref"], {"goal_type", "revision"}, "ExecutionContract.process_ref")
    _nonempty(contract["process_ref"]["goal_type"], "ExecutionContract.process_ref.goal_type")
    _nonempty(contract["process_ref"]["revision"], "ExecutionContract.process_ref.revision")
    _nonempty(contract["admission_id"], "ExecutionContract.admission_id")
    if contract['workspace_id'] is not None or 'process' not in config:
        _nonempty(contract["workspace_id"], "ExecutionContract.workspace_id")
    _nonempty(contract["restart_policy_id"], "ExecutionContract.restart_policy_id")
    if not isinstance(contract["stage_expectations"], list) or not contract["stage_expectations"]:
        raise ExecutionError("ExecutionContract.stage_expectations: required nonempty list")
    if not isinstance(contract["methods"], list):
        raise ExecutionError("ExecutionContract.methods: required list")
    if not isinstance(contract["evidence_plan"], dict):
        raise ExecutionError("ExecutionContract.evidence_plan: required object")
    return contract


def _packet(value, config):
    _exact(value, {"schema", "operation", "input"}, "Execution packet")
    if value["schema"] != WIRE_SCHEMA:
        raise ExecutionError("unsupported_schema")
    if not isinstance(value["input"], dict):
        raise ExecutionError("Execution packet input must be an object")
    if value['operation'] == 'advance':
        advance_packet(value['input'], config)
        return value
    if value['operation'] == 'lifecycle':
        lifecycle_packet(value['input'], config, _validate_contract)
        return value
    if value['operation'] == 'process':
        process_packet(value['input'], config)
        return value
    if value['operation'] == 'files':
        files_packet(value['input'], config)
        return value
    if value['operation'] == 'gate':
        gate_packet(value['input'], config)
        return value
    if value["operation"] not in {"apply", "query"}:
        raise ExecutionError(f"Unknown Execution operation: {value['operation']}")
    if value["operation"] == "apply":
        _exact(value["input"], {"request_id", "expected_revision", "operations"}, "Execution apply input")
        _nonempty(value["input"]["request_id"], "Execution apply request_id")
        if type(value["input"]["expected_revision"]) is not int or value["input"]["expected_revision"] < 0:
            raise ExecutionError("Execution apply expected_revision: required nonnegative integer")
        operations = value["input"]["operations"]
        if not isinstance(operations, list) or not operations or len(operations) > config["max_items"]:
            raise ExecutionError("Execution apply operations: required nonempty bounded list")
        seen = set()
        for op in operations:
            _exact(op, {"id", "kind", "contract"}, "Execution setup_execution")
            oid = _nonempty(op["id"], "Execution operation id")
            if oid in seen:
                raise ExecutionError("Execution operation ids must be unique")
            seen.add(oid)
            if op["kind"] != "setup_execution":
                raise ExecutionError(f"Unsupported Execution operation kind: {op['kind']}")
            _validate_contract(op["contract"], config)
    else:
        _exact(value["input"], {"queries"}, "Execution query input")
        queries = value["input"]["queries"]
        if not isinstance(queries, list) or not queries or len(queries) > config["max_queries"]:
            raise ExecutionError("Execution query queries: required nonempty bounded list")
        seen = set()
        for query in queries:
            if isinstance(query, dict) and query.get('kind') == 'progression':
                progression_query_item(query, config)
                if query['id'] in seen:
                    raise ExecutionError('Execution query ids must be unique')
                seen.add(query['id'])
                continue
            if isinstance(query, dict) and query.get('kind') in ('lifecycle', 'restart_history'):
                lifecycle_query_item(query, config)
                if query['id'] in seen:
                    raise ExecutionError('Execution query ids must be unique')
                seen.add(query['id'])
                continue
            if isinstance(query, dict) and query.get('kind') in (
                    'gate_context', 'gate_result', 'file_result', 'file_diff_result'):
                gate_query_item(query, config)
                qid = query['id']
                if qid in seen:
                    raise ExecutionError('Execution query ids must be unique')
                seen.add(qid)
                continue
            _exact(query, {"id", "kind", "task_id"}, "Execution execution query")
            qid = _nonempty(query["id"], "Execution query id")
            if qid in seen:
                raise ExecutionError("Execution query ids must be unique")
            seen.add(qid)
            if query["kind"] != "execution":
                raise ExecutionError(f"Unsupported Execution query kind: {query['kind']}")
            task_id = _nonempty(query["task_id"], "Execution query task_id")
            if config["task_pattern"].fullmatch(task_id) is None:
                raise ExecutionError("Execution query task_id violates configured identifier policy")
    return value


class Store:
    def __init__(self, config):
        self.config = config
        self.records = ExecutionRecords(
            lambda write: record_unit_of_work(self.locked, self.connect, write))
        files = GateFiles(config['gates']) if 'gates' in config else None
        self.gates = GateCommands(
            lambda write: record_unit_of_work(self.locked, self.connect, write), files,
            StageFiles(files) if files is not None else None)
        self.stage_git = StageGit(
            lambda write: record_unit_of_work(self.locked, self.connect, write),
            self.gates, config.get('git'), config, GitWorkspace)
        state_owner = ExecutionState()
        self.lifecycle = ExecutionLifecycle(
            lambda write: record_unit_of_work(self.locked, self.connect, write),
            config.get('lifecycle'),
            ProcessDefinitions(ProcessFiles(config['process'])) if 'process' in config else None,
            self.records, lambda contract: _validate_contract(contract, config), state_owner)
        self.progression = ExecutionProgression(
            lambda write: record_unit_of_work(self.locked, self.connect, write),
            config.get('lifecycle'), state_owner, ExecutionEvidence())

    @contextlib.contextmanager
    def locked(self):
        state = self.config["state"]
        state.mkdir(parents=True, exist_ok=True)
        with resource_lock(self.config['lock_path'], self.config['lock_seconds'], self.config['poll_seconds']):
            yield

    def connect(self):
        conn = sqlite3.connect(self.config["database"])
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA foreign_keys=ON")
            version = conn.execute('PRAGMA user_version').fetchone()[0]
            if version != 2:
                prepare_store(conn)
            if 'process' in self.config:
                prepare_process_store(conn)
        except ValueError as exc:
            conn.close()
            raise ExecutionError(str(exc)) from exc
        except BaseException:
            conn.close()
            raise
        return conn

    @staticmethod
    def _record_call(operation, *args, **kwargs):
        try:
            return operation(*args, **kwargs)
        except RecordsError as exc:
            raise ExecutionError(str(exc)) from exc
        except OverflowError as exc:
            raise ExecutionError('Execution storage cannot represent this integer; provide a value within the SQLite signed 64-bit range') from exc
        except UnicodeEncodeError as exc:
            raise ExecutionError('Execution storage requires valid UTF-8 text; provide encodable record values and identities') from exc

    def write_records(self, *, request_id, expected_revision, records):
        return self._record_call(self.records.write, request_id=request_id,
                                 expected_revision=expected_revision, records=records)

    def read_records(self, task_id):
        return self._record_call(self.records.read, task_id)

    def read_attempt(self, task_id, attempt_id):
        return self._record_call(self.records.attempt, task_id, attempt_id)

    def prepare_stage_commit(self, *, task_id, generation, context_id, stage_id, root,
                             baseline_ref, expected_head, candidate_tree, message,
                             author_name, author_email, commit_pattern):
        return self.stage_git.prepare_stage_commit({
            'task_id': task_id, 'generation': generation, 'context_id': context_id,
            'stage_id': stage_id, 'root': root, 'baseline_ref': baseline_ref,
            'expected_head': expected_head, 'candidate_tree': candidate_tree,
            'message': message, 'author_name': author_name, 'author_email': author_email,
            'commit_pattern': commit_pattern,
        })

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
                        raise ExecutionError("request_reuse_conflict")
                    saved = json.loads(prior["response_json"])
                    saved["replayed"] = True
                    conn.rollback()
                    return saved
                current = self.revision(conn)
                if packet["input"]["expected_revision"] != current:
                    raise ExecutionError("revision_conflict")
                results = []
                for op in packet["input"]["operations"]:
                    contract = op["contract"]
                    task_id = contract["task_id"]
                    revision = hashlib.sha256(_canonical(contract)).hexdigest()
                    row = conn.execute("SELECT contract_revision FROM executions WHERE task_id=?", (task_id,)).fetchone()
                    if row is not None:
                        if row["contract_revision"] != revision:
                            raise ExecutionError("identity_conflict")
                        changed = False
                    else:
                        process = None
                        if 'process' in self.config:
                            process = ProcessDefinitions(ProcessFiles(self.config['process'])).resolve(contract['process_ref'])
                            LifecyclePolicy.validate_process(process, contract, ExecutionError)
                        changed = True
                        conn.execute(
                            "INSERT INTO executions(task_id,generation,contract_revision,contract_json,state,current_stage,iteration,workspace_id,admission_id) VALUES(?,1,?,?,'draft',NULL,1,?,?)",
                            (task_id, revision, _canonical(contract).decode("utf-8"), contract["workspace_id"], contract["admission_id"]),
                        )
                        self._record_call(self.records.setup_snapshot, RecordRepository(conn),
                                          contract, revision, 1)
                        if process is not None:
                            self._record_call(self.records.setup_process_snapshot,
                                              RecordRepository(conn), contract, process, 1)
                    results.append({
                        "id": op["id"], "kind": "setup_execution", "changed": changed,
                        "task_id": task_id, "generation": 1, "contract_revision": revision,
                    })
                new_revision = current + 1
                response = {"schema": WIRE_SCHEMA, "status": "applied", "revision": new_revision, "replayed": False, "results": results}
                conn.execute("UPDATE meta SET value=? WHERE key='revision'", (new_revision,))
                conn.execute(
                    "INSERT INTO requests(request_id,digest,response_json) VALUES(?,?,?)",
                    (request_id, digest, json.dumps(response, ensure_ascii=False, separators=(",", ":"))),
                )
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
                conn.execute('BEGIN')
                revision = self.revision(conn)
                results = []
                repository = RecordRepository(conn)
                records = {}
                file_queries = False
                for query in packet["input"]["queries"]:
                    if query['kind'] == 'progression':
                        result = self._record_call(self.progression.query, repository, query['task_id'])
                        results.append({'id': query['id'], 'kind': query['kind'], 'result': result})
                        continue
                    if query['kind'] in ('lifecycle', 'restart_history'):
                        result = self._record_call(self.lifecycle.query, repository, query['kind'], query['task_id'])
                        results.append({'id': query['id'], 'kind': query['kind'], 'result': result})
                        continue
                    if query['kind'] in ('gate_context', 'gate_result', 'file_result', 'file_diff_result'):
                        if query['task_id'] not in records:
                            records[query['task_id']] = repository.list_records(query['task_id'])
                        file_queries |= GateQueries.uses_file_policy(query, records[query['task_id']])
                        results.append(GateQueries.resolve(query, records[query['task_id']]))
                        continue
                    row = conn.execute(
                        "SELECT task_id,generation,contract_revision,state,current_stage,iteration,workspace_id,admission_id FROM executions WHERE task_id=?",
                        (query["task_id"],),
                    ).fetchone()
                    if row is None:
                        raise ExecutionError("execution_not_found")
                    results.append({"id": query["id"], "value": {
                        "task_id": row["task_id"], "generation": int(row["generation"]),
                        "contract_revision": row["contract_revision"], "state": row["state"],
                        "current_stage": row["current_stage"], "iteration": int(row["iteration"]),
                        "workspace_id": row["workspace_id"], "admission_id": row["admission_id"],
                    }})
                response = {"schema": WIRE_SCHEMA, "status": "read_only", "revision": revision, "results": results}
                if file_queries:
                    response['replayed'] = False
                return response
            finally:
                conn.close()


def execute(config_path, stream, output):
    try:
        config = load_config(config_path)
        raw = stream.read(config["max_input_bytes"] + 1)
        if len(raw) > config["max_input_bytes"]:
            raise ExecutionError("Execution packet exceeds max_input_bytes")
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ExecutionError("invalid_json") from exc
        packet = _packet(_strict_json(text), config)
        store = Store(config)
        if packet['operation'] == 'advance':
            result = store._record_call(store.progression.execute, packet['input'])
        elif packet['operation'] == 'lifecycle':
            result = store._record_call(store.lifecycle.execute, packet['input'])
        elif packet['operation'] == 'process':
            result = ProcessCommands(ProcessUnitOfWork(store),ProcessFiles(config['process'])).execute(packet)
        elif packet['operation'] == 'files' and packet['input']['action'] == 'diff':
            result = store._record_call(store.stage_git.diff, packet['input'])
        elif packet['operation'] in ('gate', 'files'):
            result = store._record_call(store.gates.execute, packet['operation'], packet['input'])
        else:
            result = store.apply(packet) if packet["operation"] == "apply" else store.query(packet)
        code = (3 if result['status'] == 'pending' else
                2 if result['status'] == 'evaluated' and not result['accepted'] else
                2 if result['status'] in ('checked', 'diff_checked') and not result['allowed'] else 0)
    except (ExecutionError, PoiseError, OSError, sqlite3.Error, ValueError) as exc:
        result = {"schema": WIRE_SCHEMA, "status": "rejected", "reason": str(exc)}
        code = 2
    output.write(json.dumps(result, ensure_ascii=False, separators=(",", ":")) + "\n")
    return code


def main(argv=None):
    parser = argparse.ArgumentParser(prog="execution")
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args(argv)
    return execute(args.config, sys.stdin.buffer, sys.stdout)


if __name__ == "__main__":
    raise SystemExit(main())
