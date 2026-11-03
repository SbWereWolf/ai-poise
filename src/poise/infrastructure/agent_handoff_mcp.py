"""Concrete stdio adapter for the installed app's send_message_to_thread tool."""
import json
import subprocess
import uuid

from ..modules.agent_handoff.domain import ProxyFailure
from ..modules.foundation.errors import PoiseError
from .agent_handoff import executable
from .goal_config import strict_json
from .result_views import SavedResponse


class AppNotifier:
    def __init__(self, command, directory, cwd, environment):
        self.command, self.directory = command, directory
        self.cwd, self.environment = cwd, environment
        self.child = self.incoming = self.outgoing = self.errors = None
        self.identity = 0
        self.logs = None

    def _write(self, packet):
        raw = (json.dumps(packet, ensure_ascii=False) + '\n').encode('utf-8')
        self.outgoing.write(raw)
        self.outgoing.flush()
        self.child.stdin.write(raw)
        self.child.stdin.flush()

    def _request(self, method, params):
        self.identity += 1
        identity = self.identity
        self._write({'jsonrpc': '2.0', 'id': identity, 'method': method, 'params': params})
        while True:
            raw = self.child.stdout.readline()
            if not raw:
                raise ValueError('MCP transport closed before its response')
            self.incoming.write(raw)
            self.incoming.flush()
            packet = strict_json(raw.decode('utf-8'))
            if not isinstance(packet, dict) or packet.get('jsonrpc') != '2.0':
                raise ValueError('Invalid MCP JSON-RPC frame')
            if 'id' not in packet and isinstance(packet.get('method'), str):
                continue
            if type(packet.get('id')) is not int or packet['id'] != identity or 'method' in packet or 'error' in packet:
                raise ValueError('MCP response id/error is unconfirmed; inspect the full transport logs')
            if not isinstance(packet.get('result'), dict):
                raise ValueError('MCP result must be an object')
            return packet['result']

    def prepare(self):
        if self.child is not None:
            return
        executable(self.command)
        directory = self.directory / 'notification' / uuid.uuid4().hex
        directory.mkdir(parents=True)
        self.logs = {'stdin': str(directory / 'stdin.jsonl'), 'stdout': str(directory / 'stdout.jsonl'),
                     'stderr': str(directory / 'stderr.log'), 'receipt': str(directory / 'transport.json')}
        try:
            self.incoming = (directory / 'stdout.jsonl').open('wb')
            self.outgoing = (directory / 'stdin.jsonl').open('wb')
            self.errors = (directory / 'stderr.log').open('wb')
            self.child = subprocess.Popen(self.command, cwd=self.cwd, env=self.environment,
                                          stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.errors)
            hello = self._request('initialize', {
                'protocolVersion': '2025-03-26', 'capabilities': {},
                'clientInfo': {'name': 'poise-agent-handoff', 'version': '1'},
            })
            if hello.get('protocolVersion') != '2025-03-26' or 'tools' not in hello.get('capabilities', {}):
                raise ValueError('MCP initialization does not confirm the selected protocol/tools')
            self._write({'jsonrpc': '2.0', 'method': 'notifications/initialized'})
            cursor, seen, found = None, set(), False
            while True:
                page = self._request('tools/list', {} if cursor is None else {'cursor': cursor})
                tools = page.get('tools')
                if not isinstance(tools, list):
                    raise ValueError('Malformed tools/list response')
                found = found or any(isinstance(tool, dict) and tool.get('name') == 'send_message_to_thread'
                                     for tool in tools)
                cursor = page.get('nextCursor')
                if cursor is None:
                    break
                if not isinstance(cursor, str) or not cursor or cursor in seen:
                    raise ValueError('Invalid MCP tools/list continuation')
                seen.add(cursor)
            if not found:
                raise ValueError('send_message_to_thread is unavailable')
        except (OSError, ValueError, TypeError, PoiseError) as exc:
            raise ProxyFailure('invalid_settings', str(exc),
                               f'Восстановите выбранный MCP transport; полный вывод: {directory}.') from exc

    def send(self, recipient, prompt, caller):
        arguments = {'threadId': recipient['thread_id'], 'prompt': prompt}
        if 'host_id' in recipient:
            arguments['hostId'] = recipient['host_id']
        response = None
        try:
            response = self._request('tools/call', {
                'name': 'send_message_to_thread', 'arguments': arguments,
                '_meta': {'codexThreadId': caller},
            })
            if response.get('isError', False) is not False or not isinstance(response.get('content'), list):
                raise ValueError('MCP did not confirm successful notification')
            acknowledgements = [strict_json(item['text']) for item in response['content']
                                if isinstance(item, dict) and item.get('type') == 'text' and 'text' in item]
            if not any(isinstance(ack, dict) and ack.get('threadId') == recipient['thread_id']
                       for ack in acknowledgements):
                raise ValueError('MCP acknowledgement does not confirm the assigned threadId')
            return {'response': response, 'transport': self.logs}
        except (OSError, ValueError, TypeError, KeyError, PoiseError) as exc:
            raise ProxyFailure('notification_unconfirmed', str(exc),
                               'Подтвердите доставку у получателя и используйте explicit resolve_notification.',
                               status='unknown', phase='notification',
                               details={'response': response, 'transport': self.logs}) from exc

    def close(self):
        if self.child is not None:
            try:
                self.child.stdin.close()
            except OSError:
                pass
            self.child.wait()
            SavedResponse(self.logs['receipt'], 0o600).save({
                'argv': self.command, 'cwd': str(self.cwd), 'exit_code': self.child.returncode,
                'stdin': self.logs['stdin'], 'stdout': self.logs['stdout'], 'stderr': self.logs['stderr'],
            }, canonical=True)
            self.child.stdout.close()
            self.child = None
        for stream in (self.incoming, self.outgoing, self.errors):
            if stream is not None:
                stream.close()
