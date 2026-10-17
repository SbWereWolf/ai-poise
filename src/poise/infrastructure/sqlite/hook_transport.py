"""Operational hook registry only: no Task/Sprint table access."""
import json
import sqlite3
from contextlib import contextmanager, nullcontext
from datetime import datetime,timezone
from ...common import PoiseError,encoded,load_config_limits
from ...modules.hook_transport.domain import MissingHookBinding, binding_context
from .transaction import write_transaction


class HookRegistry:
    def __init__(self,settings,project_fence=None):
        self.settings=settings
        self.project_fence=project_fence

    @contextmanager
    def transaction(self):
        with (self.project_fence.locked() if self.project_fence is not None else nullcontext()):
            with self._transaction() as db:
                yield db

    @contextmanager
    def _transaction(self):
        s=self.settings
        with write_transaction(s.database,s.lock,s.raw['lock_seconds'],s.raw['lock_poll_seconds']) as db:
            version=db.execute('PRAGMA user_version').fetchone()[0]
            if version==0:
                if db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchone():
                    raise PoiseError('Unknown nonempty hook database; migration forbidden')
                db.execute('CREATE TABLE installations(id TEXT PRIMARY KEY, data TEXT NOT NULL)')
                db.execute('CREATE TABLE operations(id TEXT PRIMARY KEY, data TEXT NOT NULL)')
                db.execute('CREATE TABLE bindings(id TEXT PRIMARY KEY, data TEXT NOT NULL, latest_message TEXT)')
                db.execute('CREATE TABLE hook_events(id INTEGER PRIMARY KEY, at TEXT NOT NULL, binding TEXT, event TEXT NOT NULL, data TEXT NOT NULL)')
                db.execute('CREATE INDEX hook_events_binding ON hook_events(binding,id)')
                db.execute('PRAGMA user_version=1')
            elif version!=1:raise PoiseError('Unsupported hook registry schema; no migrations')
            yield db

    @contextmanager
    def read_transaction(self):
        from ..diagnostic_io import readonly_transaction
        limits=load_config_limits(self.settings.project_config)
        try:
            with (self.project_fence.locked() if self.project_fence is not None else nullcontext()):
                with readonly_transaction(self.settings.database, limits['sqlite_read_seconds'],
                                          limits['lock_poll_seconds']) as db:
                    if db.execute('PRAGMA user_version').fetchone()[0] != 1:
                        raise PoiseError('read-only: несовместимая schema реестра hooks; миграция не выполняется')
                    yield db
        except (OSError, sqlite3.Error) as exc:
            raise PoiseError(f'read-only: реестр hooks недоступен: {self.settings.database}: {exc}') from exc

    @staticmethod
    def event_in(db,binding,kind,data):
        cursor=db.execute('INSERT INTO hook_events(at,binding,event,data) VALUES(?,?,?,?)',
            (datetime.now(timezone.utc).isoformat(),binding,kind,encoded(data)))
        return cursor.lastrowid

    def bind(self,record):
        with self.transaction() as db:
            old=db.execute('SELECT data FROM bindings WHERE id=?',(record['session_id'],)).fetchone()
            if old is not None:
                prior=json.loads(old[0])
                if binding_context(prior)!=binding_context(record):
                    raise PoiseError('Session binding identifies another actor or settings path; select its matching configuration')
                return prior
            else:db.execute('INSERT INTO bindings VALUES(?,?,NULL)',(record['session_id'],encoded(record)))
        return record

    def record_event(self,binding,kind,turn,message,metadata=None):
        with self.transaction() as db:
            if message is not None:db.execute('UPDATE bindings SET latest_message=? WHERE id=?',(encoded(message),binding))
            return self.event_in(db,binding,kind,{'turn_id':turn, **({} if metadata is None else metadata)})

    def get(self,session):
        with self.read_transaction() as db:
            row=db.execute('SELECT data,latest_message FROM bindings WHERE id=?',(session,)).fetchone()
            if row is None:raise PoiseError('Unknown hook session binding')
            return json.loads(row[0]),None if row[1] is None else json.loads(row[1])

    def session_workspace(self, session):
        with self.read_transaction() as db:
            row = db.execute('SELECT data FROM hook_events WHERE binding=? AND event=? ORDER BY id DESC LIMIT 1',
                             (session, 'SessionStart')).fetchone()
        metadata = None if row is None else json.loads(row[0])
        if not isinstance(metadata, dict) or not isinstance(metadata.get('workspace'), str) or not metadata['workspace']:
            raise PoiseError('Session workspace is unavailable; deliver a genuine native SessionStart before refresh')
        return metadata['workspace']

    def find(self,external,agent):
        with self.read_transaction() as db:
            matches=[json.loads(r[0]) for r in db.execute('SELECT data FROM bindings')]
        matches=[r for r in matches if r['external_session']==external and (agent is None or r['agent_id']==agent)]
        if not matches:raise MissingHookBinding('No unique hook binding for this external identity')
        if len(matches)!=1:raise PoiseError('No unique hook binding for this external identity')
        return matches[0]

    def liveness(self, binding):
        with self.read_transaction() as db:
            return self.binding_liveness(db, binding)

    @staticmethod
    def binding_liveness(db, binding):
        from ...modules.ownership.domain import Liveness
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
                    "SELECT id FROM hook_events WHERE binding=? AND event='SessionEnd' AND id>? ORDER BY id LIMIT 1",
                    (binding, start),
                ).fetchone()
            requested.observation = (None if row is None else {
                'session_id': binding, 'start_event_id': start, 'end_event_id': row[0]})
            return row is not None
        requested.observation = None
        return requested

    def observe(self, definition_id, session, event, event_id):
        """Existing committed owner state only; never create a missing registry."""
        from ..diagnostic_io import readonly_database, json_record
        limit = self.settings.raw['max_input_bytes']
        with readonly_database(self.settings.database) as db:
            if db.execute('PRAGMA user_version').fetchone()[0] != 1:
                raise PoiseError('Unsupported hook registry schema; no migrations')
            installed = db.execute('SELECT substr(data,1,?) FROM installations WHERE id=?', (limit+1, definition_id)).fetchone()
            binding = db.execute('SELECT substr(data,1,?) FROM bindings WHERE id=?', (limit+1, session)).fetchone()
            params = [limit+1, session, event]
            suffix = ''
            if event_id is not None:
                suffix = ' AND id=?'; params.append(event_id)
            row = db.execute('SELECT id,at,binding,event,substr(data,1,?) AS data FROM hook_events WHERE binding=? AND event=?'
                             + suffix + ' ORDER BY id DESC LIMIT 1', params).fetchone()
            return {
                'installation': None if installed is None else json_record(installed[0], limit),
                'binding': None if binding is None else json_record(binding[0], limit),
                'event': None if row is None else {**dict(row), 'data': json_record(row['data'], limit)},
            }

    def observed_start(self, session, start_id, end_id):
        from ..diagnostic_io import readonly_database
        with readonly_database(self.settings.database) as db:
            if db.execute('PRAGMA user_version').fetchone()[0] != 1:
                raise PoiseError('Unsupported hook registry schema')
            return db.execute(
                "SELECT 1 FROM hook_events WHERE binding=? AND event='SessionStart' AND id=? AND id<?",
                (session, start_id, end_id),
            ).fetchone() is not None

    def preceding_start(self, binding, event_id):
        with self.read_transaction() as db:
            return db.execute("SELECT max(id) FROM hook_events WHERE binding=? AND event='SessionStart' AND id<=?",
                              (binding,event_id)).fetchone()[0]

    def bind_lifecycle_receipt(self, receipt, episode, definition_id, project_id):
        fields=('project_id','session_id','binding_id')
        if (not isinstance(receipt,dict) or not isinstance(episode,dict)
                or type(receipt.get('schema_version')) is not int or receipt['schema_version']!=1
                or receipt.get('event') not in ('SessionStart','SessionEnd')
                or any(not isinstance(item.get(key),str) or not item[key]
                       for item in (receipt,episode) for key in fields)
                or any(type(value) is not int or value<=0 for value in
                       (receipt.get('event_id'),receipt.get('native_start_event_id'),episode.get('start_event_id')))):
            raise PoiseError('Invalid lifecycle event receipt or episode; select a genuine SessionStart and retry recovery')
        if any(receipt[key]!=episode[key] for key in fields) or receipt['project_id']!=project_id:
            raise PoiseError('Lifecycle receipt identifies another episode')
        binding,event_id,start_id=receipt['binding_id'],receipt['event_id'],episode['start_event_id']
        with self.read_transaction() as db:
            installed=db.execute('SELECT 1 FROM installations WHERE id=?',(definition_id,)).fetchone()
            row=db.execute('SELECT data FROM bindings WHERE id=?',(binding,)).fetchone()
            if installed is None or row is None:
                raise PoiseError('Lifecycle installation/binding is unavailable; restore its configured native owner')
            data=json.loads(row[0])
            if data['project']!=project_id or data['external_session']!=receipt['session_id']:
                raise PoiseError('Lifecycle binding identifies another episode')
            actual=db.execute('SELECT event FROM hook_events WHERE binding=? AND id=?',(binding,event_id)).fetchone()
            anchor=db.execute("SELECT 1 FROM hook_events WHERE binding=? AND id=? AND event='SessionStart'",
                              (binding,start_id)).fetchone()
            preceding=db.execute("SELECT max(id) FROM hook_events WHERE binding=? AND event='SessionStart' AND id<=?",
                                 (binding,event_id)).fetchone()[0]
            interrupted=db.execute("SELECT 1 FROM hook_events WHERE binding=? AND event='SessionEnd' AND id>=? AND id<? LIMIT 1",
                                   (binding,start_id,event_id)).fetchone()
            if (actual is None or actual[0]!=receipt['event'] or anchor is None
                    or preceding!=receipt['native_start_event_id'] or start_id>preceding or interrupted is not None):
                raise PoiseError('Lifecycle event does not belong to the selected episode; verify native delivery and retry recovery')
        return {'schema_version':1,'event':receipt['event'],'event_id':event_id,
                'episode':{key:episode[key] for key in (*fields,'start_event_id')}}
