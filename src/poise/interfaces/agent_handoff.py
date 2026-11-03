"""One explicit CLI packet, native identity, and complete proxy result."""
import json
import os
from pathlib import Path

from ..application.agent_handoff import HandoffProxyCommands
from ..modules.agent_handoff.domain import ProxyFailure, ProxyRequest
from ..modules.foundation.errors import PoiseError
from ..infrastructure.agent_handoff import FileProxyJournal, NativeHandoffAdapter, ProxySettings
from ..infrastructure.agent_handoff_mcp import AppNotifier
from ..infrastructure.goal_config import strict_json
from ..infrastructure.session_establishment import direct_caller, native_session


def execute(settings_path, stream, output):
    request = commands = None
    result = {'schema': 'poise-agent-handoff-proxy-result-1', 'status': 'blocked', 'phase': 'admission',
              'request_id': None, 'task': None, 'handoff': None, 'notification': None,
              'error': None, 'replayed': False, 'response_path': None}
    try:
        try:
            request = ProxyRequest.parse(strict_json(stream.read().decode('utf-8')))
        except (ValueError, UnicodeError, PoiseError) as exc:
            raise ProxyFailure('invalid_input', str(exc), 'Исправьте исходный JSON пакет handoff-proxy.') from exc
        result['request_id'] = request.request_id
        if request.operation == 'deliver':
            result['task'] = request.value['handoff']['input']['task_id']
        settings = ProxySettings.read(settings_path)
        try:
            native_session(os.environ, required=True)
            caller = direct_caller(settings.project, os.environ, create_binding=False).native_session
        except PoiseError as exc:
            raise ProxyFailure('invalid_identity', str(exc),
                               'Используйте настоящую native session без переопределений и mixed binding.') from exc
        journal = FileProxyJournal(settings.state_root, settings.project, request.request_id)
        native = NativeHandoffAdapter(settings, journal.directory, Path.cwd(), dict(os.environ))
        notifier = AppNotifier(settings.notification, journal.directory, Path.cwd(), dict(os.environ))
        commands = HandoffProxyCommands(settings.project, caller, journal, native, notifier)
        result = commands.execute(request)
    except ProxyFailure as exc:
        if commands is not None and commands.state is not None:
            result = commands.state.project_result(commands.journal.response_path)
        result.update(status=exc.status, phase=exc.phase, error=exc.error())
    except (OSError, ValueError, PoiseError) as exc:
        if commands is not None and commands.state is not None:
            result = commands.state.project_result(commands.journal.response_path)
        failure = ProxyFailure('state_unavailable', str(exc),
                               'Сохраните материалы операции и восстановите её исходные файлы через владельца.',
                               status='unknown', phase=result['phase'])
        result.update(status=failure.status, error=failure.error())
    output.write(json.dumps(result, ensure_ascii=False, allow_nan=False) + '\n')
    return 0 if result['status'] == 'delivered' else 2
