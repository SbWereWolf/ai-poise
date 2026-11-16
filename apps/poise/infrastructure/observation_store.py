"""Own observation transactions, idempotent facts and bounded snapshot reads."""
from contextlib import contextmanager,closing
import json
import os
import sqlite3
from domain.generic import Problem
from domain.observations import (Event,COUNTERS,canonical,encoded,conflict,limit,
                                 usage_contribution,timing_duration,interval_duration,timestamp)
from migrations.observation_v1 import prepare as initial_schema


class ObservationStore:
    def __init__(self,settings,installation_id):
        self.settings,self.installation_id=settings,installation_id
        self.path=settings.database

    @staticmethod
    def unavailable(reason,action='retry:same-events',**details):
        raise Problem('observations_unavailable',{'reason':reason,**details},action,'unavailable')

    def version(self,db):
        try:row=db.execute('SELECT schema_version,installation_id FROM observation_meta WHERE id=?',(1,)).fetchone()
        except sqlite3.OperationalError as exc:
            if 'locked' in str(exc):raise
            self.unavailable('unprepared','prepare:observations')
        if row is None:self.unavailable('unprepared','prepare:observations')
        if row[0]!=1:self.unavailable('version','select:observations',version=row[0])
        if row[1]!=self.installation_id:self.unavailable('installation','select:observations')

    @contextmanager
    def connection(self,write=False):
        try:
            if not self.path.exists():self.unavailable('unprepared','prepare:observations')
            uri=str(self.path) if write else self.path.as_uri()+'?mode=ro'
            with closing(sqlite3.connect(uri,uri=not write,isolation_level=None,
                        timeout=self.settings.timeout if write else 0)) as db:
                db.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
                try:
                    self.version(db)
                    yield db
                    db.commit()
                except BaseException:
                    if db.in_transaction:db.rollback()
                    raise
        except sqlite3.Error as exc:
            code=getattr(exc,'sqlite_errorcode',0)&255
            self.unavailable('busy' if code in (5,6) else 'storage','retry:same-events' if write else 'retry:read')
        except OSError as exc:self.unavailable('io','retry:same-events' if write else 'configure:observations',errno=exc.errno)

    def prepare(self):
        created=False
        try:
            self.path.parent.mkdir(parents=True,exist_ok=True)
            with closing(sqlite3.connect(self.path,timeout=self.settings.timeout,isolation_level=None)) as db:
                # Inspect before changing journal mode, preserving incompatible selected data.
                existing=db.execute('SELECT name FROM sqlite_master WHERE type=? AND name=?',('table','observation_meta')).fetchone()
                if existing:
                    self.version(db)
                else:
                    tables=db.execute('SELECT name FROM sqlite_master WHERE type=? AND name NOT LIKE ?',('table','sqlite_%')).fetchall()
                    if tables:self.unavailable('schema','select:observations')
                    db.execute('PRAGMA journal_mode=WAL')
                    db.execute('BEGIN IMMEDIATE')
                    try:
                        current=db.execute('SELECT name FROM sqlite_master WHERE type=? AND name=?',('table','observation_meta')).fetchone()
                        if current:self.version(db)
                        else:initial_schema(db,self.installation_id);created=True
                        db.commit()
                    except BaseException:
                        if db.in_transaction:db.rollback()
                        raise
            # Explicit prepare also completes a prior schema-success/mode-failure.
            os.chmod(self.path,self.settings.file_mode)
        except sqlite3.Error:self.unavailable('storage','prepare:observations')
        except OSError as exc:self.unavailable('io','prepare:observations',errno=exc.errno)
        return {'schema':'poise-observation-state/v1','database':str(self.path),'version':1,'created':created}

    @staticmethod
    def view(row):
        seq,session,mode,raw,contribution,duration=row
        return {'seq':seq,'session_id':session,'source_mode':mode,'event':json.loads(raw),
                'contribution':None if contribution is None else json.loads(contribution),
                'duration_ns':None if duration is None else int(duration)}

    def record(self,events,session_id,closed=False,internal=False):
        if not isinstance(events,list) or not events:raise Problem('invalid_request',{'path':'/input/events'},'correct:request')
        if len(events)>self.settings.limits['max_events']:limit('max_events')
        parsed=[Event.parse(value,self.settings,internal) for value in events]
        with self.connection(True) as db:
            known={};novel={}
            for event in parsed:
                key=event.identity
                if key in novel:
                    if canonical(novel[key].data)!=canonical(event.data):conflict('identity')
                    continue
                row=db.execute('SELECT seq,session_id,source_mode,event_json,contribution_json,duration_ns '
                               'FROM observation_events WHERE kind=? AND source=? AND stream=? AND event_id=?',key).fetchone()
                if row is not None:
                    view=self.view(row)
                    if canonical(view['event'])!=canonical(event.data):conflict('identity')
                    known[key]=view
                else:novel[key]=event
            if closed and novel:raise Problem('session_closed',{'status':'closed'},'select:session')
            ordered=sorted(novel.values(),key=lambda e:(e.data['kind'],e.data['source'],e.data['stream'],
                              e.data['value']['sequence'] if e.data['kind']=='usage' else 0))
            for event in ordered:
                d=event.data;body=d['value'];contribution=None;duration=None
                if d['kind']=='usage':
                    previous=db.execute('SELECT last_sample FROM observation_streams WHERE source=? AND stream=?',
                                        (d['source'],d['stream'])).fetchone()
                    contribution=usage_contribution(None if previous is None else json.loads(previous[0]),d)
                elif d['kind'] in ('timing','operation'):duration=timing_duration(body)
                elif d['kind']=='interval':
                    start,end=timestamp(body['started_at'],'/input'),timestamp(body['ended_at'],'/input')
                    for (raw,) in db.execute('SELECT event_json FROM observation_events WHERE kind=? AND source=? AND stream=?',
                                             ('interval',d['source'],d['stream'])):
                        old=json.loads(raw)['value']
                        if start<timestamp(old['ended_at'],'/input') and end>timestamp(old['started_at'],'/input'):conflict('interval_overlap')
                    duration=interval_duration(body)
                cursor=db.execute('INSERT INTO observation_events(kind,source,stream,event_id,session_id,source_mode,event_json,contribution_json,duration_ns) '
                    'VALUES(?,?,?,?,?,?,?,?,?)',(*event.identity,session_id,self.settings.sources[d['source']],encoded(d),
                    None if contribution is None else encoded(contribution),None if duration is None else str(duration)))
                if d['kind']=='usage':
                    db.execute('INSERT INTO observation_streams(source,stream,mode,last_sequence,last_sample) VALUES(?,?,?,?,?) '
                        'ON CONFLICT(source,stream) DO UPDATE SET mode=excluded.mode,last_sequence=excluded.last_sequence,last_sample=excluded.last_sample',
                        (d['source'],d['stream'],body['mode'],str(body['sequence']),encoded(d)))
                known[event.identity]={'seq':cursor.lastrowid,'session_id':session_id,'source_mode':self.settings.sources[d['source']],
                                      'event':d,'contribution':contribution,'duration_ns':duration}
            receipts=[];seen=set()
            for index,event in enumerate(parsed):
                row=known[event.identity];d=event.data
                replayed=event.identity not in novel or event.identity in seen
                receipts.append({'index':index,'seq':row['seq'],'kind':d['kind'],'source':d['source'],
                    'stream':d['stream'],'event_id':d['event_id'],'session_id':row['session_id'],'replayed':replayed,
                    'contribution':row['contribution'],'duration_ns':row['duration_ns']})
                seen.add(event.identity)
        return {'schema':'poise-observations-record/v1','entries':receipts,'count':len(receipts)}

    def read(self,value,session_id=None):
        with self.connection() as db:
            as_of=db.execute('SELECT COALESCE(MAX(seq),0) FROM observation_events').fetchone()[0]
            where=['seq<=?'];args=[as_of]
            if value['scope']['kind']=='session':where.append('session_id=?');args.append(session_id)
            if value['source'] is not None:where.append('source=?');args.append(value['source'])
            clause=' AND '.join(where)
            total={k:0 for k in COUNTERS};unknown=set();incurred=0;usage=0;observed=0;page=[];more=False
            for row in db.execute('SELECT seq,session_id,source_mode,event_json,contribution_json,duration_ns '
                                  'FROM observation_events WHERE '+clause+' ORDER BY seq',args):
                view=self.view(row);event=view['event'];observed+=1
                if event['kind']=='usage':
                    usage+=1
                    if event['value']['mode']!='baseline':
                        incurred+=1
                        for key in COUNTERS:
                            n=view['contribution'][key]
                            if n is None:unknown.add(key)
                            else:total[key]+=n
                if view['seq']>value['after']:
                    if len(page)<value['limit']:page.append(view)
                    else:more=True
            totals={k:None if not incurred or k in unknown else total[k] for k in COUNTERS}
            return {'schema':'poise-observations-read/v1','scope':value['scope'],'source':value['source'],
                'as_of_seq':as_of,'events':page,'observed_events':observed,'usage_events':usage,'totals':totals,
                'next_after':page[-1]['seq'] if page else value['after'],'has_more':more,
                'availability':'observed','coverage':'partial'}
