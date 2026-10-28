"""Full machine check gateway and bounded stdio JSON-RPC adapter."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
from queue import Queue, Empty
import threading

from ..common import PoiseError
from ..composition import project_checks_tools, check_invocation_tools
from ..infrastructure.goal_config import strict_json
from ..infrastructure.project_binding import read_project_checkout
from ..modules.project_checks.contracts import CheckFailure
from ..modules.project_checks.mcp import (CheckTransportPolicy, arguments, check_request,
                                         run_envelope, tool_definitions)
from .project_checks import prepare_request, execute_prepared, finalize_invocation


def load_policy(path):
    return CheckTransportPolicy.parse(strict_json(Path(path).read_text()))


class ChecksGateway:
    def __init__(self, config, checkout, policy, transport_policy, clock, *, environment=None):
        self.defaults = {'config': str(config), 'checkout': str(checkout), 'policy': str(policy)}
        self.policy, self.clock = transport_policy, clock
        self.environment = dict(os.environ) if environment is None else dict(environment)

    def binding(self, value):
        return self.defaults if value['binding'] is None else value['binding']

    def context(self, value):
        binding = self.binding(value)
        bound = read_project_checkout(binding['config'], binding['checkout'])
        service = project_checks_tools(bound, binding['policy'], self.environment, self.clock)
        return check_invocation_tools(service, self.policy, self.environment)

    def prepare(self, value):
        binding = self.binding(value)
        prepared = prepare_request(binding['config'], binding['checkout'], binding['policy'],
                                   check_request(value), self.clock, self.environment)
        return prepared, check_invocation_tools(prepared.service, self.policy, self.environment)

    def run(self, value, started=None):
        identity, owner, prepared = None, None, None
        try:
            prepared, owner = self.prepare(value)
            if not value['preview']:
                identity, control = owner.confirm(prepared.value, 'mcp', value.get('_invocation'))
                prepared.service.invocation = control
                prepared.service.execution.invocation = control
                if started is not None:
                    started(owner, identity)
            _, result = execute_prepared(prepared, self.clock)
            result = finalize_invocation(prepared, owner, identity, result)
            return run_envelope(result, identity)
        except (CheckFailure, PoiseError, OSError, ValueError) as exc:
            if isinstance(exc, CheckFailure):
                failure = exc
            else:
                failure = CheckFailure('unavailable_policy', 'binding', str(exc),
                                       'Восстановите выбранные настройки или прочитайте доступные сохранённые результаты.',
                                       status='unavailable')
            bound = None if prepared is None else prepared.bound
            if bound is None:
                try:
                    binding = self.binding(value)
                    bound = read_project_checkout(binding['config'], binding['checkout'])
                except (OSError, PoiseError, ValueError):
                    pass
            result = {'schema': 'poise-project-checks-result-1',
                      'operation': 'preview' if value['preview'] else 'run', 'status': failure.status,
                      'project': None if bound is None else bound.config['project'],
                      'checkout': None if bound is None else str(bound.checkout),
                      'policy_sha256': None if prepared is None else prepared.service.configuration.policy_sha256,
                      'items': [], 'error': failure.as_dict(), 'warnings': [], 'response_path': None}
            return run_envelope(result, identity)


class RpcSession:
    def __init__(self, gateway, stream, output, error_output):
        self.gateway, self.stream, self.output, self.error_output = gateway, stream, output, error_output
        self.policy = gateway.policy
        self.frames = Queue(maxsize=self.policy['limits']['max_workers'])
        self.completed = Queue()
        self.executor = ThreadPoolExecutor(max_workers=self.policy['limits']['max_workers'])
        self.initialized, self.ready, self.eof = False, False, False
        self.futures, self.controls, self.owned = {}, {}, {}
        self.owned_lock = threading.Lock()
        self.output_failed = False

    def reader(self):
        maximum = self.policy['limits']['max_message_bytes']
        try:
            while True:
                frame = self.stream.readline(maximum + 1)
                if not frame:
                    break
                if len(frame) > maximum:
                    while frame and not frame.endswith(b'\n'):
                        frame = self.stream.readline(maximum + 1)
                    self.frames.put(('error', None))
                else:
                    self.frames.put(('frame', frame))
        finally:
            self.frames.put(('eof', None))

    def write(self, identity, *, result=None, code=None, message=None):
        body = {'jsonrpc': '2.0', 'id': identity}
        body['result' if code is None else 'error'] = result if code is None else {'code': code, 'message': message}
        if not self.output_failed:
            try:
                self.output.write(json.dumps(body, ensure_ascii=False, allow_nan=False) + '\n')
                self.output.flush()
            except (OSError, ValueError) as exc:
                self.output_failed = True
                self.error_output.write('Terminal transport unavailable; read saved check results: ' + str(exc) + '\n')

    def started(self, key, owner, identity):
        with self.owned_lock:
            self.owned[key] = owner, identity
            if self.eof:
                owner.store.request_cancel(identity, 'MCP input closed')

    def dispatch(self, frame):
        try:
            packet = strict_json(frame.decode('utf-8'))
        except (PoiseError, UnicodeError, ValueError, RecursionError):
            self.write(None, code=-32700, message='Invalid JSON')
            return
        if not isinstance(packet, dict) or packet.get('jsonrpc') != '2.0' or not isinstance(packet.get('method'), str):
            self.write(None, code=-32600, message='Invalid JSON-RPC request')
            return
        identity, method, params = packet.get('id'), packet['method'], packet.get('params', {})
        notification = 'id' not in packet
        if not notification and type(identity) not in (str, int):
            self.write(None, code=-32600, message='Request ID must be string or integer')
            return
        key = (type(identity).__name__, identity)
        if method == 'initialize':
            if self.initialized:
                self.write(identity, code=-32600, message='Already initialized')
                return
            if not isinstance(params, dict) or not isinstance(params.get('protocolVersion'), str):
                self.write(identity, code=-32602, message='Explicit protocolVersion required')
                return
            self.initialized = True
            versions = self.policy['protocol_versions']
            self.write(identity, result={'protocolVersion': params['protocolVersion'] if params['protocolVersion'] in versions else versions[0],
                                        'capabilities': {'tools': {}},
                                        'serverInfo': {'name': 'poise-checks', 'version': '1'}})
        elif method == 'notifications/initialized':
            if self.initialized:
                self.ready = True
        elif not self.ready:
            if not notification:
                self.write(identity, code=-32002, message='Complete initialize/initialized first')
        elif method == 'notifications/cancelled':
            if isinstance(params, dict):
                request_id = params.get('requestId')
                target = (type(request_id).__name__, request_id)
                with self.owned_lock:
                    active = self.owned.get(target)
                if active is not None:
                    active[0].store.request_cancel(active[1], str(params.get('reason', 'Protocol cancellation')))
        elif method == 'ping':
            if not notification:
                self.write(identity, result={})
        elif method == 'tools/list':
            self.write(identity, result={'tools': tool_definitions(self.policy)})
        elif method == 'tools/call':
            if not isinstance(params, dict) or params.get('name') not in self.policy['tools'].values():
                self.write(identity, code=-32602, message='Unknown tool')
                return
            cancel = params['name'] == self.policy['tools']['cancel']
            try:
                value = arguments(params.get('arguments'), cancel=cancel)
            except CheckFailure as exc:
                self.write(identity, code=-32602, message=str(exc))
                return
            if key in self.futures or key in self.controls:
                self.write(identity, code=-32600, message='Request ID is already active')
            elif cancel:
                try:
                    owner = self.gateway.context(value)
                    result = owner.request_cancel(value['invocation_id'], value['preview'], value['reason'])
                    if isinstance(result, tuple):
                        self.controls[key] = (identity, owner, result[1])
                    else:
                        self.write(identity, result=result)
                except (PoiseError, OSError, ValueError) as exc:
                    from ..application.check_invocations import CheckInvocations
                    self.write(identity, result=CheckInvocations.cancel_result('unavailable'))
            elif len(self.futures) >= self.policy['limits']['max_workers']:
                self.write(identity, code=-32000, message='Check workers busy; retry after a current check completes')
            else:
                future = self.executor.submit(self.gateway.run, value,
                                              lambda owner, uid: self.started(key, owner, uid))
                self.futures[key] = identity, future
        elif not notification:
            self.write(identity, code=-32601, message='Unknown method')

    def observe_controls(self):
        for key, (identity, future) in list(self.futures.items()):
            if future.done():
                try:
                    result = future.result()
                    self.write(identity, result=result)
                except Exception as exc:
                    self.write(identity, code=-32603, message=str(exc))
                del self.futures[key]
                with self.owned_lock:
                    self.owned.pop(key, None)
        for key, (identity, owner, target) in list(self.controls.items()):
            result = owner.observe_cancel(target)
            if result is not None:
                self.write(identity, result=result)
                del self.controls[key]

    def serve(self):
        threading.Thread(target=self.reader, daemon=True).start()
        try:
            while not self.eof or self.futures or self.controls:
                try:
                    kind, data = self.frames.get(timeout=self.policy['cancellation']['poll_seconds'])
                except Empty:
                    kind = None
                if kind == 'frame':
                    self.dispatch(data)
                elif kind == 'error':
                    self.write(None, code=-32700, message='Message exceeds configured byte limit')
                elif kind == 'eof':
                    with self.owned_lock:
                        self.eof = True
                        for owner, uid in self.owned.values():
                            owner.store.request_cancel(uid, 'MCP input closed')
                self.observe_controls()
        finally:
            self.executor.shutdown(wait=True)
        return 1 if self.output_failed else 0


def serve(config, checkout, policy, transport_policy, stream, output, error_output, clock):
    try:
        gateway = ChecksGateway(config, checkout, policy, load_policy(transport_policy), clock)
        return RpcSession(gateway, stream, output, error_output).serve()
    except (OSError, ValueError, PoiseError) as exc:
        error_output.write(str(exc) + '\n')
        return 2
