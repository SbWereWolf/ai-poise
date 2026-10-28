"""One claimed detached execution and bounded actual POSIX pipe/process observation."""
from copy import deepcopy
import os
from pathlib import Path
import selectors
import signal
import subprocess
import sys
import time

from domain.command import error
from infrastructure.command_files import CommandFiles
from infrastructure.command_mcp import McpCall


class WorkerLauncher:
    def start(self,store,session,id):
        entry=Path(__file__).resolve().parents[1]/'bin/poise-command-worker'
        subprocess.Popen([sys.executable,'-I','-S','-B',str(entry),str(store.path),
                          str(store.timeout),session,id],stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                         start_new_session=True,close_fds=True)


class ProcessExecution:
    def run(self,record,files):
        request,limits=record['input'],record['settings']['limits']
        meta=deepcopy(record['metadata'])
        protocol=McpCall(request['mcp'],limits) if request['transport']=='mcp_stdio' else None
        start=time.monotonic()
        execution_end=start+request['execution_budget_ms']/1000
        cleanup_end=None
        term_at=None
        killed=False
        child=None
        try:
            child=subprocess.Popen(request['argv'],cwd=request['cwd'],env=request['env'],
                stdin=subprocess.PIPE if protocol else subprocess.DEVNULL,
                stdout=subprocess.PIPE,stderr=subprocess.PIPE,start_new_session=True,bufsize=0)
        except OSError as exc:
            files.eof={'stdout':True,'stderr':True}
            meta.update(state='TERMINAL',termination={'kind':'not_started'},
                        error=error('command_start_failed',{'path':exc.filename or request['argv'][0],
                                                          'errno':exc.errno},'configure:command'))
            return meta,None
        with selectors.DefaultSelector() as selector:
            for name,stream in (('stdout',child.stdout),('stderr',child.stderr)):
                os.set_blocking(stream.fileno(),False)
                selector.register(stream,selectors.EVENT_READ,name)
            if protocol:
                os.set_blocking(child.stdin.fileno(),False)
            try:
                while True:
                    now=time.monotonic()
                    code=child.poll()
                    if cleanup_end is None:
                        if code is not None or protocol and protocol.finished:
                            cleanup_end=now+request['cleanup_budget_ms']/1000
                        elif now>=execution_end:
                            meta['deadline_exceeded']=True
                            cleanup_end=now+request['cleanup_budget_ms']/1000
                            term_at=now
                            self.signal(child,signal.SIGTERM)
                    if protocol and protocol.finished and child.stdin is not None:
                        self.close_input(child,selector)
                    if term_at is not None and not killed and now>=term_at+limits['terminate_grace_ms']/1000:
                        self.signal(child,signal.SIGKILL)
                        killed=True
                    readers=[key for key in selector.get_map().values() if key.data in ('stdout','stderr')]
                    if code is not None and not readers: break
                    if cleanup_end is not None and now>=cleanup_end: break
                    if protocol and child.stdin is not None:
                        try: key=selector.get_key(child.stdin)
                        except KeyError: key=None
                        if protocol.outgoing and key is None:
                            selector.register(child.stdin,selectors.EVENT_WRITE,'stdin')
                        elif not protocol.outgoing and key is not None:
                            selector.unregister(child.stdin)
                    end=execution_end if cleanup_end is None else cleanup_end
                    wait=min(limits['poll_ms']/1000,max(0,end-now))
                    for key,_ in selector.select(wait):
                        if key.data=='stdin':
                            try:
                                count=os.write(key.fd,protocol.outgoing)
                                del protocol.outgoing[:count]
                            except BlockingIOError:
                                pass
                            except BrokenPipeError:
                                self.close_input(child,selector)
                                protocol.eof()
                            continue
                        name=key.data
                        try: chunk=os.read(key.fd,limits['capture_chunk_bytes'])
                        except BlockingIOError: continue
                        except OSError as exc:
                            files.failed[name]=exc.errno
                            chunk=None
                        if chunk:
                            files.write(name,chunk)
                            if protocol and name=='stdout': protocol.receive(chunk)
                        else:
                            if chunk==b'': files.eof[name]=True
                            selector.unregister(key.fileobj)
                            key.fileobj.close()
                            if protocol and name=='stdout': protocol.eof()
            finally:
                self.close_input(child,selector)
                for key in list(selector.get_map().values()):
                    selector.unregister(key.fileobj)
                    key.fileobj.close()
                remaining=max(0,(cleanup_end if cleanup_end is not None else time.monotonic())-time.monotonic())
                try: child.wait(timeout=remaining)
                except subprocess.TimeoutExpired: pass
        code=child.returncode
        meta['termination']=None if code is None else (
            {'kind':'signal','signal':-code} if code<0 else {'kind':'exit','exit_code':code})
        meta['state']='TERMINAL' if code is not None else 'UNKNOWN'
        meta['business_error']=None if protocol is None else protocol.business
        fault=None
        for name in ('stdout','stderr'):
            if not files.eof[name] or name in files.failed:
                fault=error('command_capture_failed',{'stream':name,'errno':files.failed.get(name)},'inspect:command')
                break
        if fault is None:
            if code is None:
                fault=error('command_observation_lost',{'phase':'execution'},'inspect:command')
            elif protocol:
                fault=protocol.error
                if protocol.result is None and fault is None:
                    fault=error('command_observation_lost',{'phase':'mcp_response'},'inspect:command')
                if fault and fault['code']=='command_observation_lost': meta['state']='UNKNOWN'
        meta['error']=fault
        return meta,None if protocol is None else protocol.result

    @staticmethod
    def signal(child,number):
        try: os.killpg(child.pid,number)
        except ProcessLookupError: pass

    @staticmethod
    def close_input(child,selector):
        if child.stdin is None: return
        try: selector.unregister(child.stdin)
        except KeyError: pass
        child.stdin.close()
        child.stdin=None


def execute_owned(store,session,id):
    record=store.claim(session,id)
    if record is None: return
    files=CommandFiles(record)
    meta=deepcopy(record['metadata'])
    phase='execution'
    try:
        files.prepare()
        meta,provider=ProcessExecution().run(record,files)
        files.close()
        meta.update(files.sources())
        phase='publication'
        meta['full_result_ref']=files.publish(meta,provider)
    except Exception:
        files.close()
        meta.update(state='UNKNOWN',error=error('command_observation_lost',{'phase':phase},'inspect:command'))
    store.publish(session,id,meta)
