"""Operational hook registry only: no Task/Sprint table access."""
import json
import sqlite3
import errno
from pathlib import Path
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
            data={'turn_id':turn, **({} if metadata is None else metadata)}
            if kind == 'SessionEnd':
                row=db.execute('SELECT data FROM bindings WHERE id=?',(binding,)).fetchone()
                if row is None:raise PoiseError('Cannot commit End without its observed native binding')
                data['binding_record']=json.loads(row[0])
            return self.event_in(db,binding,kind,data)

    @staticmethod
    def _lifecycle_binding(db, receipt):
        if receipt.get('event') == 'SessionEnd':
            row=db.execute("SELECT data FROM hook_events WHERE binding=? AND id=? AND event='SessionEnd'",
                           (receipt['binding_id'],receipt['event_id'])).fetchone()
            data=None if row is None else json.loads(row[0]).get('binding_record')
        else:
            row=db.execute('SELECT data FROM bindings WHERE id=?',(receipt['binding_id'],)).fetchone()
            data=None if row is None else json.loads(row[0])
        if not isinstance(data,dict):
            raise PoiseError('Lifecycle binding is unavailable; select its genuine committed native delivery')
        return data

    def lifecycle_binding(self, receipt):
        if (not isinstance(receipt,dict) or not isinstance(receipt.get('binding_id'),str)
                or receipt.get('event') not in ('SessionStart','SessionEnd','PreCompact','PostCompact')
                or type(receipt.get('event_id')) is not int or receipt['event_id']<=0):
            raise PoiseError('Invalid lifecycle receipt; select its genuine committed native delivery')
        with self.read_transaction() as db:
            return self._lifecycle_binding(db,receipt)

    def get(self,session):
        with self.read_transaction() as db:
            row=db.execute('SELECT data,latest_message FROM bindings WHERE id=?',(session,)).fetchone()
            if row is None:raise PoiseError('Unknown hook session binding')
            return json.loads(row[0]),None if row[1] is None else json.loads(row[1])

    @staticmethod
    def released_binding_record(db,session):
        """Read the native End anchor only after its binding was released."""
        if db.execute('SELECT id FROM bindings WHERE id=?',(session,)).fetchone() is not None:
            return None
        last=db.execute(
            'SELECT id,event,data FROM hook_events WHERE binding=? ORDER BY id DESC LIMIT 1',
            (session,)).fetchone()
        if last is None or last[1]!='SessionEnd':
            return None
        record=json.loads(last[2]).get('binding_record')
        if not isinstance(record,dict) or record.get('session_id')!=session:
            return None
        return last[0],record

    def release_ended(self, record, end_event_id, prepare_files):
        """Release one exact ended episode and its owner-validated projections."""
        session = record['session_id']
        with self.transaction() as db:
            row = db.execute('SELECT data FROM bindings WHERE id=?', (session,)).fetchone()
            last = db.execute('SELECT id,event FROM hook_events WHERE binding=? ORDER BY id DESC LIMIT 1',
                              (session,)).fetchone()
            if last is None or tuple(last) != (end_event_id, 'SessionEnd'):
                return {'status': 'skipped', 'session_id': session}
            if row is None:
                return {'status': 'already_released', 'session_id': session}
            if json.loads(row[0]) != record:
                return {'status': 'skipped', 'session_id': session}
            files = prepare_files()
            for path, before in files.items():
                current = path.read_bytes() if path.exists() else None
                if current != before:
                    raise PoiseError('Session projection changed before release: ' + str(path) +
                                     '; Recovery: preserve edited files, refresh the exact cleanup preview '
                                     'and retry only unchanged owned projections')
            try:
                for path in files:
                    path.unlink(missing_ok=True)
                directory = Path(record['binding_path']).parent
                try:
                    directory.rmdir()
                except OSError as exc:
                    if exc.errno not in (errno.ENOTEMPTY, errno.EEXIST, errno.ENOENT):
                        raise
            except OSError as exc:
                raise PoiseError('Cannot release generated session files: ' + str(exc) +
                                 '; Recovery: correct access to the session directory and retry cleanup') from exc
            db.execute('DELETE FROM bindings WHERE id=?', (session,))
            return {'status': 'released', 'session_id': session,
                    'removed_files': [str(path) for path, before in files.items() if before is not None]}

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
        row=db.execute(
            'SELECT event FROM hook_events WHERE binding=? ORDER BY id DESC LIMIT 1',
            (binding,),
        ).fetchone()
        if row is not None and row[0]=='SessionEnd':return Liveness.DEAD
        if known is None or row is None:return Liveness.UNCERTAIN
        return Liveness.LIVE

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

    @staticmethod
    def _observed_installation(db, definition_id, limit):
        from ..diagnostic_io import json_record
        row = db.execute('SELECT substr(data,1,?) FROM installations WHERE id=?',
                         (limit+1, definition_id)).fetchone()
        return None if row is None else json_record(row[0], limit)

    def observed_installation(self, definition_id):
        from ..diagnostic_io import readonly_database
        with readonly_database(self.settings.database) as db:
            if db.execute('PRAGMA user_version').fetchone()[0] != 1:
                raise PoiseError('Unsupported hook registry schema; no migrations')
            return self._observed_installation(db, definition_id, self.settings.raw['max_input_bytes'])

    def observe(self, definition_id, session, event, event_id):
        """Existing committed owner state only; never create a missing registry."""
        from ..diagnostic_io import readonly_database, json_record
        limit = self.settings.raw['max_input_bytes']
        with readonly_database(self.settings.database) as db:
            if db.execute('PRAGMA user_version').fetchone()[0] != 1:
                raise PoiseError('Unsupported hook registry schema; no migrations')
            binding = db.execute('SELECT substr(data,1,?) FROM bindings WHERE id=?', (limit+1, session)).fetchone()
            params = [limit+1, session, event]
            suffix = ''
            if event_id is not None:
                suffix = ' AND id=?'; params.append(event_id)
            row = db.execute('SELECT id,at,binding,event,substr(data,1,?) AS data FROM hook_events WHERE binding=? AND event=?'
                             + suffix + ' ORDER BY id DESC LIMIT 1', params).fetchone()
            return {
                'installation': self._observed_installation(db, definition_id, limit),
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

    def require_lifecycle_metadata(self, receipt, metadata):
        with self.read_transaction() as db:
            row=db.execute('SELECT data FROM hook_events WHERE binding=? AND id=? AND event=?',
                           (receipt['binding_id'],receipt['event_id'],receipt['event'])).fetchone()
            if row is None:
                raise PoiseError('Committed lifecycle event is unavailable; select its genuine native delivery and retry recovery')
            committed=json.loads(row[0])
            for field, value in metadata.items():
                observed=committed.get(field)
                same=(isinstance(observed,str) and Path(observed).is_absolute() and Path(value).is_absolute()
                      and Path(observed).resolve()==Path(value).resolve()) if field=='workspace' else observed==value
                if not same:
                    raise PoiseError(f'Lifecycle {field} differs from the committed event; select its genuine native delivery and retry recovery')

    def context_transition(self, receipt, workspace, episode):
        """Resolve a compaction cycle from committed native order, never a checkpoint."""
        from ...modules.document_intake.domain import NativeContextTransition, identity
        binding, current = receipt['binding_id'], receipt['event_id']
        if (any(episode.get(key) != receipt[key] for key in ('project_id', 'session_id', 'binding_id'))
                or type(episode.get('start_event_id')) is not int
                or not 0 < episode['start_event_id'] <= receipt['native_start_event_id']):
            raise PoiseError('Native context cycle needs its verified owned Start anchor')
        with self.read_transaction() as db:
            rows = db.execute('SELECT id,event,data FROM hook_events WHERE binding=? AND id<=? '
                              'AND id>=? ORDER BY id',
                              (binding, current, episode['start_event_id'])).fetchall()
        if any(kind == 'SessionEnd' for _, kind, _ in rows):
            raise PoiseError('Native context cycle crosses a committed End; use its actual current episode')
        events = [(key, kind, json.loads(data)) for key, kind, data in rows]
        actual = next((e for e in events if e[0] == current), None)
        if actual is None or actual[1] != receipt['event']:
            raise PoiseError('Native context event is unavailable; retry its genuine lifecycle delivery')
        def same_workspace(data):
            value = data.get('workspace')
            return isinstance(value, str) and Path(value).resolve() == Path(workspace).resolve()
        if not same_workspace(actual[2]):
            raise PoiseError('Native context workspace differs; select its genuine delivery')
        kind, data = actual[1:]
        if kind == 'SessionStart' and data.get('source') == 'clear':
            return NativeContextTransition('native:' + identity([binding, workspace, current]), 'clear')
        if kind == 'SessionStart' and data.get('source') != 'compact':
            return None
        if kind == 'PreCompact':
            return None
        if kind not in ('PostCompact', 'SessionStart'):
            return None
        prior = [e for e in events if e[0] < current and same_workspace(e[2])]
        clear = next((e[0] for e in reversed(prior)
                      if e[1] == 'SessionStart' and e[2].get('source') == 'clear'), None)
        if clear is not None:
            prior = [e for e in prior if e[0] > clear]
        if kind == 'PostCompact':
            pre = next((e for e in reversed(prior) if e[1] == 'PreCompact'), None)
            if pre is not None and any(pre[2].get(k) != data.get(k) for k in ('turn_id', 'trigger')):
                raise PoiseError('Native compaction correlation differs; retry the matching PreCompact/PostCompact delivery')
            anchor = current if pre is None else pre[0]
        else:
            transition = next((e for e in reversed(prior)
                               if e[1] in ('PreCompact', 'PostCompact', 'SessionStart')), None)
            if transition is None or transition[1] == 'SessionStart':
                anchor = current
            elif transition[1] == 'PreCompact':
                anchor = transition[0]
            else:
                pre = next((e for e in reversed(prior) if e[1] == 'PreCompact'
                            and e[0] < transition[0]), None)
                if pre is not None and any(pre[2].get(k) != transition[2].get(k)
                                           for k in ('turn_id', 'trigger')):
                    raise PoiseError('Native compaction correlation differs; retry the matching native delivery')
                anchor = transition[0] if pre is None else pre[0]
        return NativeContextTransition('native:' + identity([binding, workspace, anchor]), 'compact')

    def context_delivery_current(self, receipt, workspace):
        with self.read_transaction() as db:
            later = db.execute("SELECT data FROM hook_events WHERE binding=? AND id>? "
                               "AND event IN ('PreCompact','SessionStart')",
                               (receipt['binding_id'], receipt['event_id'])).fetchall()
        return not any(isinstance((data := json.loads(row[0])).get('workspace'), str)
                       and Path(data['workspace']).resolve() == Path(workspace).resolve() for row in later)

    def bind_lifecycle_receipt(self, receipt, episode, definition_id, project_id):
        fields=('project_id','session_id','binding_id')
        if (not isinstance(receipt,dict) or not isinstance(episode,dict)
                or type(receipt.get('schema_version')) is not int or receipt['schema_version']!=1
                or receipt.get('event') not in ('SessionStart','SessionEnd','PreCompact','PostCompact')
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
            if installed is None:
                raise PoiseError('Lifecycle installation is unavailable; restore its configured native owner')
            data=self._lifecycle_binding(db,receipt)
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
