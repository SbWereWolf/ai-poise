"""Operational hook registry only: no Task/Sprint table access."""
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime,timezone
from ...common import PoiseError,encoded
from ..locking import exclusive_lock


class HookRegistry:
    def __init__(self,settings):self.settings=settings

    @contextmanager
    def transaction(self):
        s=self.settings
        with exclusive_lock(s.lock,s.raw['lock_seconds'],s.raw['lock_poll_seconds']):
            s.database.parent.mkdir(parents=True,exist_ok=True)
            db=sqlite3.connect(s.database,timeout=0,isolation_level=None);db.row_factory=sqlite3.Row
            try:
                db.execute('PRAGMA foreign_keys=ON')
                version=db.execute('PRAGMA user_version').fetchone()[0]
                if version==0:
                    if db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchone():
                        raise PoiseError('Unknown nonempty hook database; migration forbidden')
                    db.execute('BEGIN')
                    db.execute('CREATE TABLE installations(id TEXT PRIMARY KEY, data TEXT NOT NULL)')
                    db.execute('CREATE TABLE operations(id TEXT PRIMARY KEY, data TEXT NOT NULL)')
                    db.execute('CREATE TABLE bindings(id TEXT PRIMARY KEY, data TEXT NOT NULL, latest_message TEXT)')
                    db.execute('CREATE TABLE hook_events(id INTEGER PRIMARY KEY, at TEXT NOT NULL, binding TEXT, event TEXT NOT NULL, data TEXT NOT NULL)')
                    db.execute('CREATE INDEX hook_events_binding ON hook_events(binding,id)')
                    db.execute('PRAGMA user_version=1');db.execute('COMMIT')
                elif version!=1:raise PoiseError('Unsupported hook registry schema; no migrations')
                db.execute('BEGIN')
                yield db
                db.execute('COMMIT')
            except BaseException:
                if db.in_transaction:db.execute('ROLLBACK')
                raise
            finally:db.close()

    @staticmethod
    def event_in(db,binding,kind,data):
        db.execute('INSERT INTO hook_events(at,binding,event,data) VALUES(?,?,?,?)',
            (datetime.now(timezone.utc).isoformat(),binding,kind,encoded(data)))

    def bind(self,record):
        with self.transaction() as db:
            old=db.execute('SELECT data FROM bindings WHERE id=?',(record['session_id'],)).fetchone()
            if old is not None:
                prior=json.loads(old[0])
                if prior!=record:raise PoiseError('Session binding changed; restart explicitly with a new configured actor')
            else:db.execute('INSERT INTO bindings VALUES(?,?,NULL)',(record['session_id'],encoded(record)))
        return record

    def record_event(self,binding,kind,turn,message):
        with self.transaction() as db:
            if message is not None:db.execute('UPDATE bindings SET latest_message=? WHERE id=?',(encoded(message),binding))
            self.event_in(db,binding,kind,{'turn_id':turn})

    def get(self,session):
        with self.transaction() as db:
            row=db.execute('SELECT data,latest_message FROM bindings WHERE id=?',(session,)).fetchone()
            if row is None:raise PoiseError('Unknown hook session binding')
            return json.loads(row[0]),None if row[1] is None else json.loads(row[1])

    def find(self,external,agent):
        with self.transaction() as db:
            matches=[json.loads(r[0]) for r in db.execute('SELECT data FROM bindings')]
        matches=[r for r in matches if r['external_session']==external and r['agent_id']==agent]
        if len(matches)!=1:raise PoiseError('No unique hook binding for this external identity')
        return matches[0]

    def liveness(self, binding):
        from ...modules.ownership.domain import Liveness
        with self.transaction() as db:
            known=db.execute('SELECT 1 FROM bindings WHERE id=?',(binding,)).fetchone()
            if known is None:return Liveness.UNCERTAIN
            row=db.execute(
                'SELECT event FROM hook_events WHERE binding=? ORDER BY id DESC LIMIT 1',
                (binding,),
            ).fetchone()
        if row is None:return Liveness.UNCERTAIN
        return Liveness.DEAD if row[0]=='SessionEnd' else Liveness.LIVE

    def runner_cancellation_check(self, binding):
        """Capture one native session episode, not a PID or another launch registry.

        A runner polls committed SessionEnd observations across process boundaries.
        A later resume cannot erase an end observed for an already running episode.
        """
        with self.transaction() as db:
            start = db.execute(
                "SELECT max(id) FROM hook_events WHERE binding=? AND event='SessionStart'",
                (binding,),
            ).fetchone()[0]
        if start is None:
            raise PoiseError('Registered hook launch requires an observed SessionStart')

        def requested():
            with self.transaction() as db:
                row = db.execute(
                    "SELECT 1 FROM hook_events WHERE binding=? AND event='SessionEnd' AND id>? LIMIT 1",
                    (binding, start),
                ).fetchone()
            return row is not None
        return requested
