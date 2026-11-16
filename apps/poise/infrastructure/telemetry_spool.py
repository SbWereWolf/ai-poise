"""Queue-owned bounded durable retry; no global fence or file authentication."""
from contextlib import contextmanager
import fcntl
import json
import math
import os
from pathlib import Path
import time
from uuid import uuid4
from domain.generic import Problem
from domain.observations import encoded,canonical


class TelemetrySpool:
    def __init__(self,policy,process):
        self.policy,self.process=policy,process
        self.directory=policy.directory

    @staticmethod
    def failure(reason):
        raise Problem('telemetry_unavailable',{'reason':reason},'inspect:telemetry','unavailable')

    @contextmanager
    def _lock(self,name):
        self.directory.mkdir(parents=True,exist_ok=True)
        fd=os.open(self.directory/name,os.O_CREAT|os.O_RDWR,self.policy.file_mode)
        try:
            deadline=time.monotonic()+self.policy.lock_seconds
            while True:
                try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB);break
                except BlockingIOError:
                    if time.monotonic()>=deadline:self.failure('queue_busy')
                    time.sleep(min(self.policy.lock_poll_seconds,max(0,deadline-time.monotonic())))
            yield
        finally:os.close(fd)

    def _entries(self):
        try:
            found=[]
            with os.scandir(self.directory) as entries:
                for entry in entries:
                    # Atomic-write working files are not published queue records.
                    if entry.name in ('queue.lock','replay.lock') or entry.name.startswith('.save-'):continue
                    found.append(Path(entry.path))
                    if len(found)>self.policy.max_entries:self.failure('queue_limit')
            return sorted(found)
        except FileNotFoundError:return []

    def _save(self,path,record):
        candidate=self.directory/('.save-'+uuid4().hex)
        try:
            fd=os.open(candidate,os.O_CREAT|os.O_EXCL|os.O_WRONLY,self.policy.file_mode)
            with os.fdopen(fd,'wb') as stream:stream.write((encoded(record)+'\n').encode())
            os.replace(candidate,path)
        finally:
            try:candidate.unlink()
            except FileNotFoundError:pass

    def _record(self,path):
        with path.open('rb') as stream:raw=stream.read(self.policy.max_event_bytes+2049)
        if len(raw)>self.policy.max_event_bytes+2048:raise ValueError('record_limit')
        value=json.loads(raw)
        if not isinstance(value,dict) or set(value)!={'schema','envelope','attempts','next_attempt_at','enqueued_at','last_error'}:raise ValueError('record_shape')
        if value['schema']!='poise-telemetry-spool/v1':raise ValueError('record_schema')
        envelope=value['envelope']
        if not isinstance(envelope,dict) or set(envelope)!={'id','event','session_id'}:raise ValueError('envelope')
        if not isinstance(envelope['id'],str) or not envelope['id'] or len(envelope['id'])>128:raise ValueError('event_id')
        if not isinstance(envelope['event'],dict):raise ValueError('event')
        if envelope['session_id'] is not None and not isinstance(envelope['session_id'],str):raise ValueError('session')
        if len(encoded(envelope).encode())>self.policy.max_event_bytes:raise ValueError('event_limit')
        if type(value['attempts']) is not int or not 0<=value['attempts']<=1000000:raise ValueError('attempts')
        for name in ('next_attempt_at','enqueued_at'):
            n=value[name]
            if type(n) not in (int,float) or not math.isfinite(n) or n<0:raise ValueError('time')
        if value['last_error'] is not None and (not isinstance(value['last_error'],str) or len(value['last_error'])>96):raise ValueError('error')
        return value

    def enqueue(self,envelope):
        payload=encoded(envelope).encode()
        if len(payload)>self.policy.max_event_bytes:self.failure('event_limit')
        record={'schema':'poise-telemetry-spool/v1','envelope':envelope,'attempts':0,
                'next_attempt_at':0,'enqueued_at':time.time(),'last_error':None}
        path=self.directory/(envelope['id']+'.json')
        with self._lock('queue.lock'):
            entries=self._entries()
            if path in entries:
                if canonical(self._record(path)['envelope'])!=canonical(envelope):self.failure('event_conflict')
                return 'duplicate'
            charge=sum(p.stat().st_size+2048 for p in entries)
            if len(entries)>=self.policy.max_entries or charge+len(payload)+2048>self.policy.max_bytes:self.failure('queue_limit')
            self._save(path,record)
        return 'persisted'

    def __call__(self,envelope):
        self.enqueue(envelope);self.replay()

    def status(self):
        result={'pending':0,'exhausted':0,'corrupt':0,'failed_attempts':0,'bytes':0,'records':[]}
        for path in self._entries():
            result['bytes']+=path.stat().st_size
            try:record=self._record(path)
            except (OSError,ValueError,TypeError,OverflowError):result['corrupt']+=1;continue
            result['exhausted' if record['attempts']>=self.policy.max_attempts else 'pending']+=1
            result['failed_attempts']+=record['attempts']
            result['records'].append({'event_id':record['envelope']['id'],**{k:record[k] for k in ('attempts','next_attempt_at','last_error')}})
        return result

    def replay(self):
        result={'schema':'poise-telemetry-replay/v1','attempted':0,'delivered':0,'failed':0,'corrupt':0,'pending':0,'coverage':'partial'}
        with self._lock('replay.lock'):
            with self._lock('queue.lock'):entries=self._entries()
            for path in entries:
                if result['attempted']>=self.policy.batch_items:break
                try:record=self._record(path)
                except (OSError,ValueError,TypeError,OverflowError):result['corrupt']+=1;continue
                if record['attempts']>=self.policy.max_attempts or record['next_attempt_at']>time.time():continue
                record['attempts']+=1;record['next_attempt_at']=time.time()+self.policy.retry_seconds
                record['last_error']='attempt_without_ack'
                with self._lock('queue.lock'):self._save(path,record)
                result['attempted']+=1
                try:self.process(record['envelope'])
                except Exception:
                    record['last_error']='delivery_failed'
                    with self._lock('queue.lock'):self._save(path,record)
                    result['failed']+=1
                else:
                    with self._lock('queue.lock'):path.unlink()
                    result['delivered']+=1
            result['pending']=len(self._entries())
        return result
