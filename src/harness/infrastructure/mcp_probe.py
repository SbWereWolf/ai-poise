"""Bounded MCP stdio probe, not a general-purpose tool gateway.

Uses the standard newline-delimited JSON-RPC initialize/initialized sequence.
Only lists tools and optionally calls one explicitly configured read-only tool.
"""
import json
import os
import selectors
import signal
import subprocess
import time
from ..common import HarnessError
from .goal_config import strict_json


class StdioProbe:
    def __init__(self,spec,directory,files):
        self.spec=spec;self.directory=directory;self.files=files;self.child=None
        self.buffer=b'';self.messages=0;self.total=0;self.next_id=0

    def _send(self, value):
        data=(json.dumps(value,ensure_ascii=False,allow_nan=False)+'\n').encode()
        # A tiny probe must not block on a peer which does not read stdin.
        if len(data)>self.spec['mcp']['max_message_bytes']:raise HarnessError('MCP request exceeds message bound')
        view=memoryview(data)
        while view:
            remaining=self.deadline-time.monotonic()
            if remaining<=0:raise HarnessError('MCP whole-probe deadline exceeded')
            with selectors.DefaultSelector() as sel:
                sel.register(self.child.stdin,selectors.EVENT_WRITE)
                if not sel.select(remaining):raise HarnessError('MCP stdin timeout')
            try:count=os.write(self.child.stdin.fileno(),view)
            except BlockingIOError:continue
            view=view[count:]

    def _read(self):
        m=self.spec['mcp']
        while b'\n' not in self.buffer:
            remaining=self.deadline-time.monotonic()
            if remaining<=0:raise HarnessError('MCP whole-probe deadline exceeded')
            with selectors.DefaultSelector() as sel:
                sel.register(self.child.stdout,selectors.EVENT_READ)
                if not sel.select(remaining):raise HarnessError('MCP response timeout')
            try:chunk=os.read(self.child.stdout.fileno(),min(65536,m['max_message_bytes']+1))
            except BlockingIOError:continue
            if not chunk:raise HarnessError('MCP closed stdout before completing the exchange')
            self.raw.write(chunk);self.raw.flush()
            self.total+=len(chunk);self.buffer+=chunk
            if self.total>self.spec['max_output_bytes']:raise HarnessError('MCP total response bound exceeded')
            if b'\n' not in self.buffer and len(self.buffer)>m['max_message_bytes']:
                raise HarnessError('MCP message bound exceeded')
        line,self.buffer=self.buffer.split(b'\n',1)
        if len(line)>m['max_message_bytes']:raise HarnessError('MCP message bound exceeded')
        self.messages+=1
        if self.messages>m['max_messages']:raise HarnessError('MCP message count exceeded')
        value=strict_json(line.decode('utf-8'))
        if not isinstance(value,dict) or value.get('jsonrpc')!='2.0':raise HarnessError('Invalid MCP JSON-RPC message')
        return value

    def request(self, method, params):
        self.next_id+=1;rid=self.next_id
        self._send({'jsonrpc':'2.0','id':rid,'method':method,'params':params})
        while True:
            message=self._read()
            if 'id' not in message:
                if not isinstance(message.get('method'),str):raise HarnessError('Malformed MCP notification')
                continue
            if 'method' in message:raise HarnessError('Server-initiated requests are not supported by this probe')
            if type(message['id']) is not int or message['id']!=rid:raise HarnessError('MCP response id mismatch')
            if 'error' in message:raise HarnessError('MCP returned an error; inspect local protocol output')
            if not isinstance(message.get('result'),dict):raise HarnessError('MCP result object required')
            return message['result']

    def run(self):
        m=self.spec['mcp'];self.deadline=time.monotonic()+self.spec['timeout_seconds']
        with (self.directory/self.files['stdout']).open('wb') as self.raw,(self.directory/self.files['stderr']).open('wb') as err:
            try:
                self.child=subprocess.Popen(self.spec['argv'],cwd=self.spec['cwd'],env=self.spec['environment'],
                    stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=err,start_new_session=True,bufsize=0)
                os.set_blocking(self.child.stdout.fileno(),False);os.set_blocking(self.child.stdin.fileno(),False)
                hello=self.request('initialize',{'protocolVersion':m['protocol_version'],'capabilities':{},'clientInfo':m['client_info']})
                if hello.get('protocolVersion')!=m['protocol_version']:raise HarnessError('MCP protocol version mismatch; no fallback negotiation')
                server=hello.get('serverInfo');caps=hello.get('capabilities')
                if not isinstance(server,dict) or not isinstance(server.get('name'),str) or not isinstance(server.get('version'),str):
                    raise HarnessError('MCP serverInfo is missing')
                if not isinstance(caps,dict) or 'tools' not in caps:raise HarnessError('Server does not advertise tools')
                self._send({'jsonrpc':'2.0','method':'notifications/initialized'})
                tools={};cursor=None;seen=set()
                for _ in range(m['max_pages']):
                    page=self.request('tools/list',{} if cursor is None else {'cursor':cursor})
                    if not isinstance(page.get('tools'),list):raise HarnessError('Malformed tools/list result')
                    for tool in page['tools']:
                        if not isinstance(tool,dict) or not isinstance(tool.get('name'),str) or not isinstance(tool.get('inputSchema'),dict):
                            raise HarnessError('Malformed advertised tool')
                        if tool['name'] in tools:raise HarnessError('Duplicate advertised tool')
                        tools[tool['name']]=tool
                    cursor=page.get('nextCursor')
                    if cursor is None:break
                    if not isinstance(cursor,str) or not cursor or cursor in seen:raise HarnessError('MCP cursor cycle/invalid cursor')
                    seen.add(cursor)
                else:raise HarnessError('MCP pagination limit reached')
                missing=set(m['required_tools'])-tools.keys()
                if missing:raise HarnessError(f'Required MCP tools absent: {sorted(missing)}')
                smoke=None
                if m['call'] is not None:
                    call=m['call'];smoke=self.request('tools/call',{'name':call['name'],'arguments':call['arguments']})
                    if smoke.get('isError',False) is not False:raise HarnessError('MCP smoke returned isError')
                return {'server':server,'tools':list(tools),'smoke':smoke,
                        'observation':'mcp_tools_listed' if smoke is None else 'mcp_smoke_passed'}
            finally:
                if self.child is not None:
                    try:
                        self.child.stdin.close()
                        self.child.wait(timeout=m['shutdown_seconds'])
                    except (subprocess.TimeoutExpired,BrokenPipeError):
                        if self.child.poll() is None:
                            os.killpg(self.child.pid,signal.SIGKILL);self.child.wait()
                    finally:self.child.stdout.close()
