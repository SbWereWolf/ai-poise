"""Bounded external counter protocol; no invented token estimates."""
import json
import os
import selectors
import subprocess
import time
from domain.observations import encoded


class Tokenizer:
    def __init__(self,settings):self.settings=settings

    def count(self,texts):
        c=self.settings
        child=subprocess.Popen(c['argv'],cwd=c['cwd'],env=c['env'],stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE,stderr=subprocess.PIPE,start_new_session=True)
        selector=selectors.DefaultSelector();output=bytearray();errors=bytearray()
        payload=memoryview((encoded({'texts':texts})+'\n').encode())
        try:
            for stream in (child.stdin,child.stdout,child.stderr):os.set_blocking(stream.fileno(),False)
            selector.register(child.stdin,selectors.EVENT_WRITE,'input')
            selector.register(child.stdout,selectors.EVENT_READ,'output');selector.register(child.stderr,selectors.EVENT_READ,'error')
            deadline=time.monotonic()+c['timeout_seconds']
            while selector.get_map():
                remaining=deadline-time.monotonic()
                if remaining<=0:raise ValueError('instrument_timeout')
                for key,_ in selector.select(remaining):
                    if key.data=='input':
                        try:n=os.write(key.fd,payload)
                        except BrokenPipeError:payload=payload[len(payload):]
                        else:payload=payload[n:]
                        if not payload:selector.unregister(key.fileobj);key.fileobj.close()
                    else:
                        data=os.read(key.fd,min(65536,c['max_output_bytes']+1))
                        if not data:selector.unregister(key.fileobj);key.fileobj.close();continue
                        target=output if key.data=='output' else errors;target.extend(data)
                        if len(output)+len(errors)>c['max_output_bytes']:raise ValueError('instrument_output_limit')
            code=child.wait(timeout=max(.001,deadline-time.monotonic()))
            if code!=0:raise ValueError('instrument_exit')
            value=json.loads(output)
            if not isinstance(value,dict) or set(value)!={'counts'}:raise ValueError('instrument_result')
            counts=value['counts']
            if not isinstance(counts,list) or len(counts)!=2 or any(type(n) is not int or n<0 for n in counts):raise ValueError('instrument_counts')
            return counts
        except subprocess.TimeoutExpired:
            raise ValueError('instrument_timeout') from None
        finally:
            selector.close()
            if child.poll() is None:
                import signal
                os.killpg(child.pid,signal.SIGKILL)
            child.wait()
            for stream in (child.stdin,child.stdout,child.stderr):
                if not stream.closed:stream.close()
