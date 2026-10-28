"""One command catalog: short admission, worker claim and result transactions."""
from contextlib import contextmanager, closing
import json
import os
from pathlib import Path
import sqlite3
import time
from uuid import uuid4

from domain.command import metadata, same_json
from domain.generic import Problem


def encoded(value):
    return json.dumps(value,ensure_ascii=False,allow_nan=False,separators=(',',':'))


class CommandStore:
    def __init__(self,path,timeout):
        self.path,self.timeout=Path(path),timeout

    @contextmanager
    def connection(self,write=False,*,timeout=None):
        try:
            deadline=None if timeout is None else time.monotonic()+max(0,timeout)
            uri=str(self.path) if write else self.path.as_uri()+'?mode=ro'
            wait=self.timeout if timeout is None else min(self.timeout,max(0,timeout))
            with closing(sqlite3.connect(uri,timeout=wait,isolation_level=None,uri=not write)) as db:
                version=db.execute('PRAGMA user_version').fetchone()[0]
                if version!=1:
                    raise Problem('command_store_incompatible',{'version':version},'select:command-store','unavailable')
                if deadline is not None:
                    milliseconds=int(min(self.timeout,max(0,deadline-time.monotonic()))*1000)
                    db.execute('PRAGMA busy_timeout='+str(milliseconds))
                if write: db.execute('BEGIN IMMEDIATE')
                try:
                    yield db
                    if write: db.commit()
                except BaseException:
                    if db.in_transaction: db.rollback()
                    raise
        except sqlite3.Error as exc:
            self.failure(exc)
        except OSError as exc:
            raise Problem('command_store_io',{'errno':exc.errno},'configure:command-store','unavailable') from None

    @staticmethod
    def failure(exc):
        code=getattr(exc,'sqlite_errorcode',0)&255
        if code in (sqlite3.SQLITE_BUSY,sqlite3.SQLITE_LOCKED):
            raise Problem('command_store_busy',{},'retry:command','unavailable') from None
        if code in (sqlite3.SQLITE_NOTADB,sqlite3.SQLITE_CORRUPT):
            raise Problem('command_store_incompatible',{'version':None},'select:command-store','unavailable') from None
        raise Problem('command_store_io',{'errno':getattr(exc,'errno',None)},'configure:command-store','unavailable') from None

    def prepare_new(self):
        if self.path.exists(): return
        candidate=self.path.with_name('.command-init-'+uuid4().hex+'.sqlite')
        try:
            with closing(sqlite3.connect(candidate,timeout=self.timeout,isolation_level=None)) as db:
                db.execute('PRAGMA journal_mode=WAL')
                db.execute('BEGIN IMMEDIATE')
                db.execute('''CREATE TABLE commands(
                    session_id TEXT NOT NULL,command_id TEXT NOT NULL,
                    execution_id TEXT NOT NULL UNIQUE,stdout_id TEXT NOT NULL,stderr_id TEXT NOT NULL,
                    input_json TEXT NOT NULL,settings_json TEXT NOT NULL,
                    dispatch_state TEXT NOT NULL,metadata_json TEXT NOT NULL,
                    PRIMARY KEY(session_id,command_id))''')
                db.execute('PRAGMA user_version=1')
                db.commit()
            try: os.link(candidate,self.path)
            except FileExistsError: pass
        except sqlite3.Error as exc:
            self.failure(exc)
        except OSError as exc:
            raise Problem('command_store_io',{'errno':exc.errno},'configure:command-store','unavailable') from None
        finally:
            try: candidate.unlink()
            except FileNotFoundError: pass

    @staticmethod
    def row(db,session,id):
        values=db.execute('''SELECT execution_id,stdout_id,stderr_id,input_json,settings_json,
                            dispatch_state,metadata_json FROM commands WHERE session_id=? AND command_id=?''',
                          (session,id)).fetchone()
        if values is None: return None
        names=('execution_id','stdout_id','stderr_id','input','settings','dispatch_state','metadata')
        return {key:json.loads(value) if key in ('input','settings','metadata') else value
                for key,value in zip(names,values)}

    def read(self,session,id,*,timeout=None):
        if not self.path.exists(): return None
        with self.connection(timeout=timeout) as db: return self.row(db,session,id)

    def admit(self,session,value,settings):
        self.prepare_new()
        id=value['command_id']
        with self.connection(write=True) as db:
            existing=self.row(db,session,id)
            if existing is not None:
                if not same_json(existing['input'],value) or not same_json(existing['settings'],settings):
                    raise Problem('command_reuse_conflict',{'command_id':id},'use:new-command')
                return existing,False
            db.execute('INSERT INTO commands VALUES(?,?,?,?,?,?,?,?,?)',
                (session,id,uuid4().hex,uuid4().hex,uuid4().hex,encoded(value),encoded(settings),
                 'new',encoded(metadata(id,value['transport']))))
            return self.row(db,session,id),True

    def claim(self,session,id):
        with self.connection(write=True) as db:
            row=self.row(db,session,id)
            if row is None or row['dispatch_state']!='new': return None
            row['metadata']['state']='RUNNING'
            db.execute('UPDATE commands SET dispatch_state=?,metadata_json=? WHERE session_id=? AND command_id=?',
                       ('claimed',encoded(row['metadata']),session,id))
            return row

    def publish(self,session,id,value):
        with self.connection(write=True) as db:
            db.execute('''UPDATE commands SET dispatch_state=?,metadata_json=?
                          WHERE session_id=? AND command_id=? AND dispatch_state!=?''',
                       ('finished',encoded(value),session,id,'finished'))

    def delete(self,session,id):
        with self.connection(write=True) as db:
            db.execute('DELETE FROM commands WHERE session_id=? AND command_id=?',(session,id))
