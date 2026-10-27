"""Extracted bounded MCP stdio observation; no hook or Session transport."""
import json
import os
import selectors
import signal
import subprocess
import time

from infrastructure.json_io import loads


class StdioProbe:
    def __init__(self, spec, output):
        self.spec, self.output = spec, output
        self.paths = output.capture_paths()
        self.child = None
        self.buffer = b''
        self.messages, self.total, self.next_id = 0, 0, 0
        self.sent = []

    def send(self, value):
        data = (json.dumps(value, ensure_ascii=False, allow_nan=False) + '\n').encode('utf-8')
        if len(data) > self.spec['mcp']['max_message_bytes']:
            raise ValueError('MCP request exceeds configured message bound')
        item = {'message': value, 'bytes_sent': 0}
        self.sent.append(item)
        view = memoryview(data)
        while view:
            remaining = self.deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError('MCP whole-probe deadline exceeded')
            with selectors.DefaultSelector() as selector:
                selector.register(self.child.stdin, selectors.EVENT_WRITE)
                if not selector.select(remaining):
                    raise TimeoutError('MCP stdin timed out')
            try:
                count = os.write(self.child.stdin.fileno(), view)
            except BlockingIOError:
                continue
            item['bytes_sent'] += count
            view = view[count:]

    def read(self):
        limits = self.spec['mcp']
        while b'\n' not in self.buffer:
            remaining = self.deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError('MCP whole-probe deadline exceeded')
            with selectors.DefaultSelector() as selector:
                selector.register(self.child.stdout, selectors.EVENT_READ)
                if not selector.select(remaining):
                    raise TimeoutError('MCP response timed out')
            try:
                chunk = os.read(self.child.stdout.fileno(), min(65536, limits['max_message_bytes'] + 1))
            except BlockingIOError:
                continue
            if not chunk:
                raise ValueError('MCP closed stdout before the response')
            self.raw.write(chunk)
            self.raw.flush()
            self.total += len(chunk)
            self.buffer += chunk
            if self.total > self.spec['max_output_bytes']:
                raise ValueError('MCP configured output bound exceeded')
            if b'\n' not in self.buffer and len(self.buffer) > limits['max_message_bytes']:
                raise ValueError('MCP configured message bound exceeded')
        line, self.buffer = self.buffer.split(b'\n', 1)
        self.messages += 1
        if len(line) > limits['max_message_bytes'] or self.messages > limits['max_messages']:
            raise ValueError('MCP configured message bound exceeded')
        value = loads(line)
        if not isinstance(value, dict) or value.get('jsonrpc') != '2.0':
            raise ValueError('Invalid MCP JSON-RPC response')
        return value

    def request(self, method, parameters):
        self.next_id += 1
        identity = self.next_id
        self.send({'jsonrpc': '2.0', 'id': identity, 'method': method, 'params': parameters})
        while True:
            message = self.read()
            if 'id' not in message:
                if not isinstance(message.get('method'), str):
                    raise ValueError('Malformed MCP notification')
                continue
            if 'method' in message or type(message['id']) is not int or message['id'] != identity:
                raise ValueError('MCP response ID mismatch')
            if 'error' in message or not isinstance(message.get('result'), dict):
                raise ValueError('MCP returned an unsuccessful result')
            return message['result']

    def finish(self, error, stderr):
        if self.child is not None:
            try:
                self.child.stdin.close()
            except BrokenPipeError:
                pass
            try:
                self.child.wait(timeout=self.spec['mcp']['shutdown_seconds'])
            except subprocess.TimeoutExpired:
                if self.child.poll() is None:
                    try:
                        os.killpg(self.child.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                self.child.wait()
            while True:
                try:
                    tail = os.read(self.child.stdout.fileno(), 65536)
                except BlockingIOError:
                    break
                if not tail:
                    break
                self.raw.write(tail)
            self.child.stdout.close()
        self.raw.flush()
        stderr.flush()
        self.output.evidence(self.paths['stdout'], 'stdout')
        self.output.evidence(self.paths['stderr'], 'stderr')
        overflow = (self.paths['stdout'].stat().st_size + self.paths['stderr'].stat().st_size
                    > self.spec['max_output_bytes'])
        self.output.save_receipt(self.paths, {
            'argv': self.spec['argv'], 'cwd': self.spec['cwd'],
            'environment': self.spec['environment'], 'sent': self.sent,
            'exit_code': None if self.child is None else self.child.returncode,
            'error': error or ('MCP configured output bound exceeded' if overflow else None),
            'output_limit_exceeded': overflow, 'max_output_bytes': self.spec['max_output_bytes'],
            'timeout_seconds': self.spec['timeout_seconds'],
            'stdout': str(self.paths['stdout']), 'stderr': str(self.paths['stderr'])})
        return overflow

    def run(self):
        self.deadline = time.monotonic() + self.spec['timeout_seconds']
        limits = self.spec['mcp']
        error = None
        with self.paths['stdout'].open('wb') as self.raw, self.paths['stderr'].open('wb') as stderr:
            try:
                self.child = subprocess.Popen(self.spec['argv'], cwd=self.spec['cwd'],
                    env=self.spec['environment'], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                    stderr=stderr, start_new_session=True, bufsize=0)
                os.set_blocking(self.child.stdout.fileno(), False)
                os.set_blocking(self.child.stdin.fileno(), False)
                hello = self.request('initialize', {'protocolVersion': limits['protocol_version'],
                    'capabilities': {}, 'clientInfo': limits['client_info']})
                if hello.get('protocolVersion') != limits['protocol_version']:
                    raise ValueError('MCP protocol version differs from the explicit selection')
                server = hello.get('serverInfo')
                if (not isinstance(server, dict) or not isinstance(server.get('version'), str)
                        or not isinstance(server.get('name'), str)):
                    raise ValueError('MCP server version is missing')
                if not isinstance(hello.get('capabilities'), dict) or 'tools' not in hello['capabilities']:
                    raise ValueError('MCP server does not advertise tools')
                self.send({'jsonrpc': '2.0', 'method': 'notifications/initialized'})
                tools, seen, cursor = {}, set(), None
                for _ in range(limits['max_pages']):
                    page = self.request('tools/list', {} if cursor is None else {'cursor': cursor})
                    if not isinstance(page.get('tools'), list):
                        raise ValueError('Malformed MCP tool inventory')
                    for tool in page['tools']:
                        if (not isinstance(tool, dict) or not isinstance(tool.get('name'), str)
                                or not isinstance(tool.get('inputSchema'), dict)):
                            raise ValueError('Malformed MCP tool')
                        if tool['name'] in tools:
                            raise ValueError('Duplicate MCP tool')
                        tools[tool['name']] = tool
                    cursor = page.get('nextCursor')
                    if cursor is None:
                        break
                    if not isinstance(cursor, str) or not cursor or cursor in seen:
                        raise ValueError('MCP pagination cursor is invalid')
                    seen.add(cursor)
                else:
                    raise ValueError('MCP configured page limit reached')
                if set(limits['required_tools']) - tools.keys():
                    raise ValueError('Required MCP tool is unavailable')
                smoke = None
                if limits['call'] is not None:
                    call = limits['call']
                    smoke = self.request('tools/call', {'name': call['name'], 'arguments': call['arguments']})
                    if smoke.get('isError', False) is not False:
                        raise ValueError('MCP operation returned isError')
                if self.paths['stdout'].stat().st_size + self.paths['stderr'].stat().st_size > self.spec['max_output_bytes']:
                    raise ValueError('MCP configured output bound exceeded')
                result = {'server': server, 'tools': list(tools), 'smoke': smoke,
                        'observation': 'mcp_tools_listed' if smoke is None else 'mcp_smoke_passed',
                        'receipt': str(self.paths['receipt'])}
            except (OSError, ValueError) as exc:
                error = str(exc)
                raise
            finally:
                overflow = self.finish(error, stderr)
        if overflow:
            raise ValueError('MCP configured output bound exceeded')
        return result
