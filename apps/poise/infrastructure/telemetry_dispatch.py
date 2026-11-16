"""Bounded Linux detached capture with one nonblocking final metadata send."""
from collections import deque
import json
import os
import select
import stat
from domain.observations import encoded


class CaptureToken:
    def __init__(self,write_fd,receipt):
        self.write_fd,self.receipt=write_fd,receipt


def close_unrelated_pipes(*preserved):
    for entry in os.scandir('/proc/self/fd'):
        try:
            fd=int(entry.name)
            if fd>2 and fd not in preserved and stat.S_ISFIFO(os.fstat(fd).st_mode):os.close(fd)
        except (OSError,ValueError):pass


class CaptureDispatcher:
    def __init__(self,processor,max_pending,max_event_bytes):
        self.processor,self.max_pending,self.max_event_bytes=processor,max_pending,max_event_bytes
        self._receipts=[];self._seen=set();self._recent_ids=deque()
        self._stats={key:0 for key in ('submitted','processed','duplicates','failed','dropped')}

    def _remember(self,identity):
        if identity not in self._seen:
            self._seen.add(identity);self._recent_ids.append(identity)
        while len(self._recent_ids)>2*self.max_pending:self._seen.discard(self._recent_ids.popleft())

    def _harvest(self):
        remaining=[]
        for receipt in self._receipts:
            poll=select.poll();poll.register(receipt['fd'],select.POLLIN|select.POLLHUP|select.POLLERR)
            if not poll.poll(0):remaining.append(receipt);continue
            try:result=os.read(receipt['fd'],1)
            except OSError:result=b'0'
            os.close(receipt['fd'])
            if not receipt['discarded']:
                self._stats['processed' if result==b'1' else 'dropped' if result==b'd' else 'failed']+=1
            self._remember(receipt['id'])
        self._receipts=remaining

    def begin_capture(self,clock,base,builder):
        self._harvest();self._stats['submitted']+=1
        if len(self._receipts)>=self.max_pending or len(encoded(base).encode())>self.max_event_bytes:
            self._stats['dropped']+=1;return None
        if base['id'] in self._seen:
            self._stats['duplicates']+=1;return None
        try:token=self._spawn_capture(clock,base,builder)
        except OSError:self._stats['dropped']+=1;return None
        self._receipts.append(token.receipt)
        return token

    def _spawn_capture(self,clock_factory,base,builder):
        final_read,final_write=os.pipe();receipt_read,receipt_write=os.pipe()
        try:child=os.fork()
        except OSError:
            for fd in (final_read,final_write,receipt_read,receipt_write):os.close(fd)
            raise
        if child==0:
            try:
                os.close(final_write);os.close(receipt_read)
                worker=os.fork()
                if worker:os._exit(0)
                devnull=os.open(os.devnull,os.O_RDWR)
                for fd in (0,1,2):os.dup2(devnull,fd)
                if devnull>2:os.close(devnull)
                close_unrelated_pipes(final_read,receipt_write)
                outcome=b'0'
                try:
                    clock=clock_factory();started=clock.observe()
                    payload=bytearray()
                    while True:
                        chunk=os.read(final_read,min(65536,self.max_event_bytes+1-len(payload)))
                        if not chunk:break
                        payload.extend(chunk)
                        if len(payload)>self.max_event_bytes:raise ValueError('capture_metadata_limit')
                    final=json.loads(payload)
                    envelope=builder(base,started,clock.observe(),final)
                    if len(encoded(envelope).encode())>self.max_event_bytes:outcome=b'd'
                    else:self.processor(envelope);outcome=b'1'
                except Exception:outcome=b'0'
                try:os.write(receipt_write,outcome)
                except OSError:pass
                finally:
                    os.close(final_read);os.close(receipt_write)
            finally:os._exit(0)
        os.close(final_read);os.close(receipt_write)
        os.set_blocking(final_write,False)
        while True:
            try:os.waitpid(child,0);break
            except InterruptedError:continue
        return CaptureToken(final_write,{'fd':receipt_read,'id':base['id'],'discarded':False})

    def finish_capture(self,token,final):
        if token is None:return False
        accepted=False
        try:
            payload=encoded(final).encode()
            if len(payload)<=self.max_event_bytes:
                try:accepted=os.write(token.write_fd,payload)==len(payload)
                except (BlockingIOError,OSError):pass
            if not accepted:
                token.receipt['discarded']=True;self._stats['dropped']+=1
            return accepted
        finally:os.close(token.write_fd)

    def summary(self):
        self._harvest()
        return {**self._stats,'pending':len(self._receipts)}
