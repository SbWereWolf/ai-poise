from __future__ import annotations
import math
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from ...modules.foundation.errors import PoiseError
from ..locking import exclusive_lock

# Storage format identity, not a project/process policy or a fallback.
SCHEMA_VERSION = 13
SCHEMA = (
    "CREATE TABLE accounting_accounts(task_id TEXT PRIMARY KEY REFERENCES tasks(id), project TEXT NOT NULL, created_at TEXT NOT NULL, data TEXT NOT NULL)",
    "CREATE TABLE accounting_usage(id TEXT PRIMARY KEY, task_id TEXT REFERENCES tasks(id), project TEXT NOT NULL, source TEXT NOT NULL, stream TEXT NOT NULL, sequence INTEGER NOT NULL, occurred_at TEXT NOT NULL, data TEXT NOT NULL, UNIQUE(source,stream,sequence))",
    "CREATE INDEX accounting_usage_task ON accounting_usage(task_id,occurred_at)",
    "CREATE TABLE accounting_cycles(id TEXT PRIMARY KEY, task_id TEXT REFERENCES tasks(id), project TEXT NOT NULL, session_id TEXT NOT NULL, started_at TEXT NOT NULL, ended_at TEXT, data TEXT NOT NULL)",
    "CREATE UNIQUE INDEX accounting_open_session ON accounting_cycles(session_id) WHERE ended_at IS NULL",
    "CREATE INDEX accounting_cycle_task ON accounting_cycles(task_id,started_at)",
    "CREATE TABLE accounting_credits(seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT NOT NULL UNIQUE, task_id TEXT NOT NULL REFERENCES tasks(id), project TEXT NOT NULL, at TEXT NOT NULL, data TEXT NOT NULL)",
    "CREATE INDEX accounting_credit_task ON accounting_credits(task_id,seq)",
    "CREATE TABLE accounting_findings(task_id TEXT NOT NULL REFERENCES tasks(id), finding_id TEXT NOT NULL, data TEXT NOT NULL, PRIMARY KEY(task_id,finding_id))",
    "CREATE TABLE transfer_requests(actor TEXT NOT NULL, request_id TEXT NOT NULL, digest TEXT NOT NULL, data TEXT NOT NULL, PRIMARY KEY(actor,request_id))",
    "CREATE TABLE transfer_imports(package_digest TEXT PRIMARY KEY, data TEXT NOT NULL)",
    "CREATE TABLE transfer_locations(task_id TEXT PRIMARY KEY REFERENCES tasks(id), data TEXT NOT NULL)",
    "CREATE TABLE runtime_bindings(identity_key TEXT PRIMARY KEY,session_id TEXT NOT NULL UNIQUE,inventory TEXT NOT NULL)",
    "CREATE TABLE runtime_cursors(session_id TEXT NOT NULL,path TEXT NOT NULL,data TEXT NOT NULL,PRIMARY KEY(session_id,path))",
    "CREATE TABLE handoffs(seq INTEGER PRIMARY KEY AUTOINCREMENT, actor TEXT NOT NULL,request_id TEXT NOT NULL,task_id TEXT NOT NULL REFERENCES tasks(id),state TEXT NOT NULL,data TEXT NOT NULL,UNIQUE(actor,request_id))",
    "CREATE INDEX handoffs_task ON handoffs(task_id,state,seq)",
    "CREATE TABLE action_runs(task_id TEXT NOT NULL REFERENCES tasks(id), stage TEXT NOT NULL, iteration INTEGER NOT NULL, version INTEGER NOT NULL, data TEXT NOT NULL, PRIMARY KEY(task_id,stage,iteration))",
    "CREATE TABLE action_events(seq INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL REFERENCES tasks(id), stage TEXT NOT NULL, iteration INTEGER NOT NULL, version INTEGER NOT NULL, at TEXT NOT NULL, data TEXT NOT NULL)",
    "CREATE INDEX action_events_owner ON action_events(task_id,stage,iteration,seq)",
    "CREATE TABLE sprints(id TEXT PRIMARY KEY, project TEXT NOT NULL, state TEXT NOT NULL, revision INTEGER NOT NULL, data TEXT NOT NULL)",
    "CREATE TABLE sprint_artifacts(sprint_id TEXT NOT NULL REFERENCES sprints(id), artifact_id TEXT NOT NULL REFERENCES artifacts(id), PRIMARY KEY(sprint_id,artifact_id))",
    "CREATE INDEX sprints_project ON sprints(project,state,id)",
    "CREATE TABLE sprint_layers(sprint_id TEXT NOT NULL REFERENCES sprints(id), revision INTEGER NOT NULL, data TEXT NOT NULL, at TEXT NOT NULL, PRIMARY KEY(sprint_id,revision))",
    "CREATE TABLE sprint_members(sprint_id TEXT NOT NULL REFERENCES sprints(id), task_id TEXT NOT NULL UNIQUE REFERENCES tasks(id), PRIMARY KEY(sprint_id,task_id))",
    "CREATE TABLE sprint_dependencies(sprint_id TEXT NOT NULL, predecessor TEXT NOT NULL, successor TEXT NOT NULL, kind TEXT NOT NULL, PRIMARY KEY(sprint_id,predecessor,successor), FOREIGN KEY(sprint_id,predecessor) REFERENCES sprint_members(sprint_id,task_id), FOREIGN KEY(sprint_id,successor) REFERENCES sprint_members(sprint_id,task_id))",
    "CREATE INDEX sprint_successors ON sprint_dependencies(sprint_id,successor)",
    "CREATE TABLE sprint_requests(sprint_id TEXT NOT NULL REFERENCES sprints(id), request_id TEXT NOT NULL, digest TEXT NOT NULL, data TEXT NOT NULL, PRIMARY KEY(sprint_id,request_id))",
    "CREATE TABLE session_sprints(session_id TEXT PRIMARY KEY, sprint_id TEXT NOT NULL REFERENCES sprints(id))",
    "CREATE TABLE interaction_events(id TEXT PRIMARY KEY, project TEXT NOT NULL, data TEXT NOT NULL, received_at TEXT NOT NULL)",
    "CREATE TABLE interaction_bindings(event_id TEXT PRIMARY KEY REFERENCES interaction_events(id), task_id TEXT NOT NULL REFERENCES tasks(id), sprint_id TEXT, goal_type TEXT NOT NULL, stage TEXT NOT NULL, iteration INTEGER NOT NULL, session_id TEXT NOT NULL, project TEXT NOT NULL)",
    "CREATE INDEX interaction_task ON interaction_bindings(task_id,event_id)",
    "CREATE TABLE interaction_reports(task_id TEXT NOT NULL REFERENCES tasks(id), stage TEXT NOT NULL, iteration INTEGER NOT NULL, at TEXT NOT NULL, source TEXT NOT NULL, PRIMARY KEY(task_id,stage,iteration))",
    "CREATE TABLE work_packets(task_id TEXT NOT NULL REFERENCES tasks(id), stage TEXT NOT NULL, iteration INTEGER NOT NULL, digest TEXT NOT NULL, PRIMARY KEY(task_id,stage,iteration))",
    "CREATE TABLE task_proofs(task_id TEXT PRIMARY KEY REFERENCES tasks(id), data TEXT NOT NULL)",
    "CREATE TABLE task_proof_layers(task_id TEXT NOT NULL REFERENCES tasks(id), version INTEGER NOT NULL, data TEXT NOT NULL, PRIMARY KEY(task_id,version))",
    "CREATE TABLE tasks(id TEXT PRIMARY KEY, status TEXT NOT NULL, stage_index INTEGER NOT NULL, iteration INTEGER NOT NULL, claimed_by TEXT, version INTEGER NOT NULL, current_submission_id INTEGER, metadata TEXT NOT NULL, FOREIGN KEY(current_submission_id,id) REFERENCES submissions(seq,task_id) DEFERRABLE INITIALLY DEFERRED)",
    "CREATE TABLE task_workflows(task_id TEXT PRIMARY KEY REFERENCES tasks(id), data TEXT NOT NULL)",
    "CREATE TABLE task_execution(task_id TEXT PRIMARY KEY REFERENCES tasks(id), data TEXT NOT NULL, version INTEGER NOT NULL)",
    "CREATE TABLE sessions(id TEXT PRIMARY KEY, task_id TEXT REFERENCES tasks(id))",
    "CREATE UNIQUE INDEX tasks_single_claimant ON tasks(claimed_by) WHERE claimed_by IS NOT NULL",
    "CREATE UNIQUE INDEX sessions_single_worktree_owner ON sessions(task_id) WHERE task_id IS NOT NULL",
    "CREATE TABLE submissions(seq INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL REFERENCES tasks(id), stage TEXT NOT NULL, iteration INTEGER NOT NULL, digest TEXT NOT NULL, data TEXT NOT NULL, UNIQUE(seq,task_id))",
    "CREATE TABLE workflow_layers(submission_id INTEGER NOT NULL, task_id TEXT NOT NULL, data TEXT NOT NULL, PRIMARY KEY(task_id,submission_id), FOREIGN KEY(submission_id,task_id) REFERENCES submissions(seq,task_id))",
    "CREATE TABLE section_layers(submission_id INTEGER NOT NULL, task_id TEXT NOT NULL, section_id TEXT NOT NULL, content TEXT NOT NULL, content_state TEXT NOT NULL, PRIMARY KEY(submission_id,section_id), FOREIGN KEY(submission_id,task_id) REFERENCES submissions(seq,task_id))",
    "CREATE TABLE task_methods(task_id TEXT NOT NULL REFERENCES tasks(id), method_id TEXT NOT NULL, version INTEGER NOT NULL, data TEXT NOT NULL, PRIMARY KEY(task_id,method_id))",
    "CREATE TABLE content_contracts(task_id TEXT NOT NULL REFERENCES tasks(id), version INTEGER NOT NULL, data TEXT NOT NULL, PRIMARY KEY(task_id,version))",
    "CREATE TABLE trace_point_layers(submission_id INTEGER NOT NULL, task_id TEXT NOT NULL, route_id TEXT NOT NULL, point_id TEXT NOT NULL, data TEXT NOT NULL, PRIMARY KEY(submission_id,route_id,point_id), FOREIGN KEY(submission_id,task_id) REFERENCES submissions(seq,task_id))",
    "CREATE INDEX trace_latest ON trace_point_layers(task_id,route_id,point_id,submission_id)",
    "CREATE TABLE task_results(task_id TEXT NOT NULL, submission_id INTEGER NOT NULL, data TEXT NOT NULL, PRIMARY KEY(task_id,submission_id), FOREIGN KEY(submission_id,task_id) REFERENCES submissions(seq,task_id))",
    "CREATE TABLE task_events(seq INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL REFERENCES tasks(id), version INTEGER NOT NULL, at TEXT NOT NULL, data TEXT NOT NULL)",
    "CREATE TABLE evidence(id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id), stage TEXT NOT NULL, iteration INTEGER NOT NULL, data TEXT NOT NULL)",
    "CREATE TABLE artifacts(id TEXT PRIMARY KEY, owner TEXT NOT NULL, scope TEXT NOT NULL, path TEXT NOT NULL, digest TEXT NOT NULL, UNIQUE(owner,scope,path))",
    "CREATE TABLE task_artifacts(task_id TEXT REFERENCES tasks(id), artifact_id TEXT REFERENCES artifacts(id), PRIMARY KEY(task_id,artifact_id))",
    "CREATE TABLE journal(seq INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, session_id TEXT NOT NULL, task_id TEXT, event TEXT NOT NULL, data TEXT NOT NULL)",
    "CREATE INDEX submissions_owner_stage ON submissions(task_id,stage,seq)",
    "CREATE INDEX layers_owner_section ON section_layers(task_id,section_id,submission_id)",
    "CREATE INDEX task_events_owner ON task_events(task_id,seq)",
    "CREATE INDEX tasks_status ON tasks(status,id)",
    "CREATE INDEX evidence_task ON evidence(task_id,stage,iteration)",
    "CREATE INDEX journal_task ON journal(task_id,seq)",
)


class Database:
    """Linux-only external lock + short SQL transaction with one owned v12 upgrade."""
    def __init__(self, path: Path, lock: Path, wait: float, poll: float):
        for value in (wait, poll):
            if type(value) not in (int,float) or not math.isfinite(value) or value <= 0:
                raise PoiseError("Требуются явные конечные положительные пределы lock")
        self.path, self.lock, self.wait, self.poll = path, lock, wait, poll
        path.parent.mkdir(parents=True, exist_ok=True)
        lock.parent.mkdir(parents=True, exist_ok=True)
        with self.transaction() as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version == 0:
                if db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchone():
                    raise PoiseError("Неизвестная БД; автоматическая миграция запрещена")
                for statement in SCHEMA:
                    db.execute(statement)
                db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
            elif version == 12:
                self._upgrade_v12_ownership(db)
            elif version != SCHEMA_VERSION:
                raise PoiseError("Schema БД несовместима; миграция не выполняется. Нужен новый пустой store")

    @staticmethod
    def _upgrade_v12_ownership(db):
        """Install physical ownership uniqueness without guessing an ambiguous live owner."""
        duplicate_task_owners = db.execute(
            "SELECT claimed_by FROM tasks WHERE claimed_by IS NOT NULL "
            "GROUP BY claimed_by HAVING COUNT(*) > 1"
        ).fetchall()
        for row in duplicate_task_owners:
            actor = row["claimed_by"]
            claims = [item["id"] for item in db.execute(
                "SELECT id FROM tasks WHERE claimed_by=? ORDER BY id", (actor,)
            )]
            binding = db.execute(
                "SELECT task_id FROM sessions WHERE id=?", (actor,)
            ).fetchone()
            retained = None if binding is None else binding["task_id"]
            if retained not in claims:
                raise PoiseError(
                    "Ownership schema upgrade cannot choose a Task claim for session "
                    f"{actor}; repair the session binding before retrying"
                )
            db.execute(
                "UPDATE tasks SET claimed_by=NULL WHERE claimed_by=? AND id<>?",
                (actor, retained),
            )

        duplicate_worktree_owners = db.execute(
            "SELECT task_id FROM sessions WHERE task_id IS NOT NULL "
            "GROUP BY task_id HAVING COUNT(*) > 1"
        ).fetchall()
        for row in duplicate_worktree_owners:
            task_id = row["task_id"]
            owners = [item["id"] for item in db.execute(
                "SELECT id FROM sessions WHERE task_id=? ORDER BY id", (task_id,)
            )]
            task = db.execute(
                "SELECT status,claimed_by FROM tasks WHERE id=?", (task_id,)
            ).fetchone()
            retained = None if task is None else task["claimed_by"]
            if retained in owners:
                db.execute(
                    "UPDATE sessions SET task_id=NULL WHERE task_id=? AND id<>?",
                    (task_id, retained),
                )
            elif task is not None and task["status"] in ("completed", "cancelled", "superseded") and retained is None:
                db.execute("UPDATE sessions SET task_id=NULL WHERE task_id=?", (task_id,))
            else:
                raise PoiseError(
                    "Ownership schema upgrade cannot choose a worktree owner for Task "
                    f"{task_id}; repair the Task claim before retrying"
                )

        db.execute("CREATE UNIQUE INDEX tasks_single_claimant ON tasks(claimed_by) WHERE claimed_by IS NOT NULL")
        db.execute("CREATE UNIQUE INDEX sessions_single_worktree_owner ON sessions(task_id) WHERE task_id IS NOT NULL")
        db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")

    @contextmanager
    def transaction(self):
        with exclusive_lock(self.lock, self.wait, self.poll):
            db = None
            try:
                db = sqlite3.connect(self.path, timeout=0, isolation_level=None, autocommit=True)
                db.row_factory = sqlite3.Row
                db.execute("PRAGMA foreign_keys=ON")
                if db.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
                    raise PoiseError("SQLite foreign_keys не включён")
                db.execute("BEGIN IMMEDIATE")
                yield db
                db.execute("COMMIT")
            except BaseException:
                if db is not None and db.in_transaction:
                    db.execute("ROLLBACK")
                raise
            finally:
                if db is not None:
                    db.close()
