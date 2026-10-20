#!/usr/bin/env python3
"""Standalone Sequencing JSON provider with app-owned configuration/state."""
from __future__ import annotations

import argparse
import contextlib
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
import sys
import time
from uuid import uuid4

from apps.sequencing.application.ownership import OwnershipCommands
from apps.sequencing.application.execution_facts import ExecutionFactsCommands
from apps.sequencing.application.workspace import WorkspaceCommands
from apps.sequencing.domain.eligibility import project_eligibility
from apps.sequencing.domain.ownership import OwnershipError
from apps.sequencing.domain.execution_facts import FactsError
from apps.sequencing.domain.workspace import WorkspaceError
from apps.sequencing.domain.registration import (
    Dependency, RegistrationError, RequirementsReference, SprintDefinition,
)
from apps.sequencing.infrastructure.eligibility_read import EligibilityReadRepository
from apps.sequencing.infrastructure.registration_store import RegistrationRepository
from apps.sequencing.infrastructure.ownership_store import OwnershipRepository
from apps.sequencing.infrastructure.execution_facts_store import ExecutionFactsRepository
from apps.sequencing.infrastructure.workspace_store import WorkspaceRepository
from apps.sequencing.infrastructure.workspace_git import GitWorkspace
from apps.sequencing.migrations.v1 import migrate as migrate_v1
from apps.sequencing.migrations.v2 import migrate as migrate_v2

CONFIG_SCHEMA = "stride-sequencing-config/v1"
WIRE_SCHEMA = "stride-sequencing/v1"
OWNERSHIP_FIELDS = {
    'claim_task': {'id', 'kind', 'task_id', 'claimant', 'claim_request_id'},
    'switch_claim': {'id', 'kind', 'from_task_id', 'from_claim_id', 'task_id',
                     'claimant', 'claim_request_id', 'reason'},
    'release_claim': {'id', 'kind', 'task_id', 'claim_id', 'claimant', 'reason'},
    'resume_claim': {'id', 'kind', 'task_id', 'claimant', 'handoff_id'},
    'recover_claim': {'id', 'kind', 'task_id', 'claim_id', 'operator', 'decision',
                      'writers_stopped', 'reason'},
}


class SequencingError(Exception):
    pass


def _strict_json(raw: str, *, allow_float: bool = False):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise SequencingError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    def bad_float(text):
        if allow_float:
            return float(text)
        raise SequencingError("floating-point numbers are not supported")

    def bad_constant(_):
        raise SequencingError("non-finite numbers are not supported")

    try:
        return json.loads(
            raw,
            object_pairs_hook=pairs,
            parse_float=bad_float,
            parse_constant=bad_constant,
        )
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
        data = _strict_json(path.read_text(encoding="utf-8"), allow_float=True)
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
        "config_directory": path.parent,
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


def _fact_source(value):
    _exact(value, {'owner', 'id'}, 'Sequencing fact source_ref')
    for field in ('owner', 'id'):
        _nonempty(value[field], 'Sequencing fact source_ref.' + field)


def _identifier(value, pattern, kind):
    _nonempty(value, 'Sequencing ' + kind + ' identifier')
    if pattern.fullmatch(value) is None:
        raise SequencingError('Sequencing ' + kind + ' identifier violates configured policy')
    return value


def _packet(value, config):
    _exact(value, {"schema", "operation", "input"}, "Sequencing packet")
    if value["schema"] != WIRE_SCHEMA:
        raise SequencingError("unsupported_schema")
    if not isinstance(value["input"], dict):
        raise SequencingError("Sequencing packet input must be an object")
    if value["operation"] not in {"apply", "query", "workspace"}:
        raise SequencingError(f"Unknown Sequencing operation: {value['operation']}")
    if value['operation'] == 'workspace':
        inputs = value['input']
        _exact(inputs, {'action', 'request_id', 'task_id', 'claim_id', 'claimant', 'read_only', 'roots'},
               'Sequencing workspace input')
        if inputs['action'] != 'prepare':
            raise SequencingError('Unknown Workspace action')
        for key in ('request_id', 'claim_id', 'claimant'):
            _nonempty(inputs[key], 'Sequencing workspace ' + key)
        _identifier(inputs['task_id'], config['task_pattern'], 'Task')
        if type(inputs['read_only']) is not bool:
            raise SequencingError('Sequencing workspace read_only requires a boolean')
        _exact(inputs['roots'], {'code', 'main', 'artifact'}, 'Sequencing workspace roots')
        for kind, paths in inputs['roots'].items():
            if not isinstance(paths, list):
                raise SequencingError('Sequencing workspace roots.' + kind + ' requires a list')
            for path in paths:
                _path_text(path, 'Sequencing workspace root path')
        return value
    if value["operation"] == "apply":
        _exact(value["input"], {"request_id", "expected_revision", "operations"}, "Sequencing apply input")
        _nonempty(value["input"]["request_id"], "Sequencing apply request_id")
        if type(value["input"]["expected_revision"]) is not int or value["input"]["expected_revision"] < 0:
            raise SequencingError("Sequencing apply expected_revision: required nonnegative integer")
        ops = value["input"]["operations"]
        if not isinstance(ops, list) or not ops or len(ops) > config["max_items"]:
            raise SequencingError("Sequencing apply operations: required nonempty bounded list")
        for op in ops:
            if not isinstance(op, dict):
                raise SequencingError('Sequencing operation requires an object')
            kind = op.get('kind')
            if kind == 'register_task':
                reference_fields = {'requirements_snapshot_revision', 'requirements_statement_revision'} & op.keys()
                if len(reference_fields) != 1:
                    raise SequencingError('Sequencing register_task requires exactly one Requirements reference')
                field = next(iter(reference_fields))
                _exact(op, {'id', 'kind', 'task_id', field}, 'Sequencing register_task')
                _identifier(op['task_id'], config['task_pattern'], 'Task')
                if type(op[field]) is not int or op[field] < 1:
                    raise SequencingError('Sequencing Requirements reference requires a positive integer')
            elif kind == 'register_sprint':
                _exact(op, {'id', 'kind', 'sprint_id', 'members', 'dependencies'}, 'Sequencing register_sprint')
                _identifier(op['sprint_id'], config['sprint_pattern'], 'Sprint')
                if not isinstance(op['members'], list) or not op['members']:
                    raise SequencingError('Sequencing Sprint members require a nonempty list')
                for identifier in op['members']:
                    _identifier(identifier, config['task_pattern'], 'Task')
                if not isinstance(op['dependencies'], list):
                    raise SequencingError('Sequencing Sprint dependencies require a list')
                for edge in op['dependencies']:
                    _exact(edge, {'predecessor', 'successor', 'kind'}, 'Sequencing dependency')
                    _identifier(edge['predecessor'], config['task_pattern'], 'Task')
                    _identifier(edge['successor'], config['task_pattern'], 'Task')
                    if edge['kind'] not in ('completion', 'result'):
                        raise SequencingError('Sequencing dependency kind must be completion or result')
            elif isinstance(kind, str) and kind in OWNERSHIP_FIELDS:
                fields = OWNERSHIP_FIELDS[kind]
                _exact(op, fields, 'Sequencing ' + kind)
                _identifier(op['task_id'], config['task_pattern'], 'Task')
                if kind == 'switch_claim':
                    _identifier(op['from_task_id'], config['task_pattern'], 'Task')
                for field in fields - {'id', 'kind', 'task_id', 'from_task_id', 'writers_stopped'}:
                    _nonempty(op[field], 'Sequencing ' + kind + ' ' + field)
                if kind == 'recover_claim' and op['writers_stopped'] is not True:
                    raise SequencingError('Sequencing recovery requires writers_stopped=true')
            elif kind == 'record_stage_label':
                _exact(op, {'id', 'kind', 'task_id', 'claim_id', 'claimant', 'label_id', 'label',
                            'source_ref'}, 'Sequencing record_stage_label')
                _identifier(op['task_id'], config['task_pattern'], 'Task')
                for field in ('claim_id', 'claimant', 'label_id', 'label'):
                    _nonempty(op[field], 'Sequencing record_stage_label.' + field)
                _fact_source(op['source_ref'])
            elif kind == 'record_execution_outcome':
                _exact(op, {'id', 'kind', 'outcome'}, 'Sequencing record_execution_outcome')
                fact = op['outcome']
                _exact(fact, {'outcome_id', 'task_id', 'kind', 'result_commit', 'source_ref'},
                       'Sequencing outcome')
                _identifier(fact['task_id'], config['task_pattern'], 'Task')
                _nonempty(fact['outcome_id'], 'Sequencing outcome.outcome_id')
                if not isinstance(fact['kind'], str) or fact['kind'] not in ('positive', 'negative', 'cancelled'):
                    raise SequencingError('Sequencing outcome.kind requires positive, negative or cancelled')
                commit = fact['result_commit']
                if fact['kind'] == 'positive':
                    if commit is not None and (not isinstance(commit, str)
                                               or re.fullmatch('[0-9a-f]{40}', commit) is None):
                        raise SequencingError('Sequencing positive result_commit requires null or 40 lowercase hex characters')
                elif commit is not None:
                    raise SequencingError('Sequencing negative/cancelled result_commit requires null')
                _fact_source(fact['source_ref'])
            else:
                raise SequencingError(f'Unsupported Sequencing operation kind: {kind}')
            _nonempty(op['id'], 'Sequencing operation id')
    else:
        _exact(value['input'], {'queries'}, 'Sequencing query input')
        queries = value['input']['queries']
        if not isinstance(queries, list) or not queries or len(queries) > config['max_queries']:
            raise SequencingError('Sequencing query queries: required nonempty bounded list')
        seen = set()
        for query in queries:
            if not isinstance(query, dict) or query.get('kind') not in ('task', 'sprint', 'eligibility', 'next', 'execution_facts'):
                raise SequencingError('Unsupported Sequencing query kind')
            kind = query['kind']
            if kind in ('task', 'sprint', 'execution_facts'):
                subject = 'sprint' if kind == 'sprint' else 'task'
                field = subject + '_id'
                _exact(query, {'id', 'kind', field}, 'Sequencing query')
                _identifier(query[field], config[subject + '_pattern'], subject)
            elif kind == 'eligibility':
                _exact(query, {'id', 'kind', 'task_id', 'claimant'}, 'Sequencing eligibility query')
                _identifier(query['task_id'], config['task_pattern'], 'Task')
                _nonempty(query['claimant'], 'Sequencing query claimant')
            else:
                _exact(query, {'id', 'kind', 'claimant', 'scope'}, 'Sequencing next query')
                _nonempty(query['claimant'], 'Sequencing query claimant')
                scope = query['scope']
                if not isinstance(scope, dict) or scope.get('kind') not in ('all', 'independent', 'sprint'):
                    raise SequencingError('Sequencing scope requires all, independent or sprint selector')
                fields = {'kind', 'sprint_id'} if scope['kind'] == 'sprint' else {'kind'}
                _exact(scope, fields, 'Sequencing scope')
                if scope['kind'] == 'sprint':
                    _identifier(scope['sprint_id'], config['sprint_pattern'], 'Sprint')
            qid = _nonempty(query['id'], 'Sequencing query id')
            if qid in seen:
                raise SequencingError('Sequencing query ids must be unique')
            seen.add(qid)
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
            deadline = time.monotonic() + self.config["lock_seconds"]
            while True:
                try:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError as exc:
                    if time.monotonic() >= deadline:
                        raise SequencingError("Sequencing lock timeout") from exc
                    time.sleep(self.config["poll_seconds"])
            try:
                yield
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    def connect(self):
        conn = sqlite3.connect(self.config["database"])
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            version = conn.execute('PRAGMA user_version').fetchone()[0]
            if version == 0:
                migrate_v1(conn)
            migrate_v2(conn)
        except ValueError as exc:
            conn.close()
            raise SequencingError(str(exc)) from exc
        except sqlite3.Error:
            conn.close()
            raise
        return conn

    @staticmethod
    def revision(conn):
        return int(conn.execute("SELECT value FROM meta WHERE key='revision'").fetchone()[0])

    @contextlib.contextmanager
    def workspace_transaction(self):
        with self.locked():
            conn = self.connect()
            try:
                conn.execute('BEGIN IMMEDIATE')
                yield WorkspaceRepository(conn)
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()

    def _workspace_roots(self, intrinsic, requested):
        roots = [{'kind': 'code', 'position': 0, 'path': str(intrinsic)}]
        for kind, paths in requested.items():
            for position, path in enumerate(paths, start=1 if kind == 'code' else 0):
                roots.append({'kind': kind, 'position': position,
                              'path': str(_resolve_from(self.config['config_directory'], path))})
        return sorted(roots, key=lambda root: (root['kind'], root['position']))

    def prepare_workspace(self, packet):
        commands = WorkspaceCommands(self.workspace_transaction, GitWorkspace(self.config),
                                     self._workspace_roots, lambda: uuid4().hex,
                                     lambda: datetime.now(timezone.utc).isoformat(), WIRE_SCHEMA)
        return commands.prepare(packet, hashlib.sha256(_canonical(packet)).hexdigest())

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
                repository = RegistrationRepository(conn)
                reader = EligibilityReadRepository(conn)
                ownership = OwnershipCommands(OwnershipRepository(conn),
                    lambda task_id, claimant: self._eligibility(conn, reader, task_id, claimant),
                    lambda: uuid4().hex, lambda: datetime.now(timezone.utc).isoformat())
                facts = ExecutionFactsCommands(ExecutionFactsRepository(conn),
                                               lambda: datetime.now(timezone.utc).isoformat())
                for op in packet['input']['operations']:
                    if op['kind'] == 'register_task':
                        kind = 'statement' if 'requirements_statement_revision' in op else 'snapshot'
                        field = 'requirements_' + kind + '_revision'
                        reference = RequirementsReference(kind, op[field])
                        changed = repository.register_task(op['task_id'], reference)
                        results.append({'id': op['id'], 'kind': 'register_task', 'changed': changed,
                                        'task_id': op['task_id'], field: reference.revision})
                    elif op['kind'] == 'register_sprint':
                        definition = SprintDefinition.create(op['sprint_id'], op['members'],
                            [Dependency(**edge) for edge in op['dependencies']])
                        changed, sprint_revision = repository.register_sprint(definition)
                        results.append({'id': op['id'], 'kind': 'register_sprint', 'changed': changed,
                                        'sprint_id': op['sprint_id'], 'sprint_revision': sprint_revision})
                    elif op['kind'] in ('record_stage_label', 'record_execution_outcome'):
                        results.append({'id': op['id'], 'kind': op['kind'], **facts.execute(op)})
                    else:
                        results.append({'id': op['id'], 'kind': op['kind'], **ownership.execute(op)})
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

    @staticmethod
    def _active_claim(conn, task_id):
        return OwnershipRepository(conn).active_for_task(task_id)

    @staticmethod
    def _workspace(conn, task_id):
        return WorkspaceRepository(conn).value(task_id)

    def _task_value(self, conn, row):
        membership = conn.execute('SELECT sprint_id FROM sprint_members WHERE task_id=?',
                                  (row['task_id'],)).fetchone()
        reference_field = ('requirements_snapshot_revision' if row['requirements_snapshot_revision'] is not None
                           else 'requirements_statement_revision')
        return {'task_id': row['task_id'], reference_field: int(row[reference_field]),
                'state': row['state'], 'sprint_id': None if membership is None else membership['sprint_id'],
                'claim': self._active_claim(conn, row['task_id']),
                'workspace': self._workspace(conn, row['task_id']),
                'terminal': None if row['terminal_json'] is None else json.loads(row['terminal_json'])}

    def _eligibility(self, conn, reader, task_id, claimant):
        return project_eligibility(task_id, reader.task_state(task_id), claimant,
                                   self._active_claim(conn, task_id), reader.predecessors(task_id))

    def query(self, packet):
        with self.locked():
            conn = self.connect()
            try:
                conn.execute('BEGIN')
                revision = self.revision(conn)
                results = []
                repository = RegistrationRepository(conn)
                reader = EligibilityReadRepository(conn)
                for query in packet["input"]["queries"]:
                    if query['kind'] == 'sprint':
                        results.append({'id': query['id'], 'value': repository.sprint_value(query['sprint_id'])})
                        continue
                    if query['kind'] == 'execution_facts':
                        value = ExecutionFactsRepository(conn).value(query['task_id'])
                        results.append({'id': query['id'], 'value': value})
                        continue
                    if query['kind'] == 'eligibility':
                        value = self._eligibility(conn, reader, query['task_id'], query['claimant'])
                        results.append({'id': query['id'], 'value': value})
                        continue
                    if query['kind'] == 'next':
                        scope = query['scope']
                        sprint_id = scope['sprint_id'] if scope['kind'] == 'sprint' else None
                        candidates = reader.candidate_ids(scope['kind'], sprint_id)
                        eligible = [task_id for task_id in candidates
                                    if self._eligibility(conn, reader, task_id, query['claimant'])['eligible']]
                        results.append({'id': query['id'], 'value': {'task_ids': eligible}})
                        continue
                    row = conn.execute(
                        "SELECT task_id,requirements_snapshot_revision,requirements_statement_revision,state,terminal_json "
                        "FROM tasks WHERE task_id=?",
                        (query["task_id"],),
                    ).fetchone()
                    if row is None:
                        raise SequencingError("task_not_found")
                    results.append({"id": query["id"], "value": self._task_value(conn, row)})
                return {"schema": WIRE_SCHEMA, "status": "read_only", "revision": revision, "results": results}
            finally:
                conn.rollback()
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
        if packet['operation'] == 'workspace':
            result = store.prepare_workspace(packet)
        else:
            result = store.apply(packet) if packet["operation"] == "apply" else store.query(packet)
        code = 0
    except (SequencingError, RegistrationError, OwnershipError, WorkspaceError, FactsError, OSError, sqlite3.Error) as exc:
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
