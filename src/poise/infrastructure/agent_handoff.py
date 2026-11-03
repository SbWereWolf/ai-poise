"""Explicit proxy configuration, request-owned files and the existing runner."""
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import shutil
import uuid

from ..execution import RegisteredCheckRunner
from ..modules.agent_handoff.domain import ProxyFailure, ProxyState, required_text
from ..modules.foundation.errors import PoiseError
from .goal_config import atomic_write, strict_json
from .locking import blocking_file_lock
from .result_views import SavedResponse


def executable(argv):
    if (not isinstance(argv, list) or not argv
            or any(not required_text(item) for item in argv) or shutil.which(argv[0]) is None):
        raise ProxyFailure('invalid_settings', 'Выбранный executable недоступен или argv неверен.',
                           'Восстановите явно выбранные launcher и notification.command.')


@dataclass(frozen=True)
class ProxySettings:
    project: str
    launcher: list
    notification: list
    state_root: Path

    @classmethod
    def read(cls, path):
        try:
            if not Path(path).is_absolute():
                raise ValueError('Select an absolute settings path')
            value = strict_json(Path(path).read_text(encoding='utf-8'))
            if (not isinstance(value, dict)
                    or set(value) != {'schema', 'project', 'launcher', 'notification', 'state_root'}
                    or value['schema'] != 'poise-agent-handoff-proxy-settings-1'
                    or not required_text(value['project'])
                    or not isinstance(value['notification'], dict)
                    or set(value['notification']) != {'command'}
                    or not isinstance(value['state_root'], str)
                    or not Path(value['state_root']).is_absolute()):
                raise ValueError('Explicit project, launcher, notification.command and absolute state_root required')
            for argv in (value['launcher'], value['notification']['command']):
                if not isinstance(argv, list) or not argv or any(not required_text(item) for item in argv):
                    raise ValueError('Commands must be nonempty argv arrays')
            return cls(value['project'], value['launcher'], value['notification']['command'], Path(value['state_root']))
        except (OSError, ValueError, TypeError, PoiseError) as exc:
            raise ProxyFailure('invalid_settings', str(exc), 'Исправьте выбранные settings handoff-proxy.') from exc


class FileProxyJournal:
    def __init__(self, root, project, request_id):
        key = hashlib.sha256(json.dumps([project, request_id], ensure_ascii=False).encode()).hexdigest()
        self.directory = root / key
        self.response_path = str(self.directory / 'response.json')

    def locked(self):
        return blocking_file_lock(self.directory / 'operation.lock')

    def load(self):
        path = self.directory / 'state.json'
        if not path.exists():
            return None
        try:
            value = strict_json(path.read_text(encoding='utf-8'))
            if not isinstance(value, dict) or value.get('schema') != 'poise-agent-handoff-proxy-state-1':
                raise ValueError('Unsupported proxy state')
            return ProxyState(**value)
        except (OSError, ValueError, TypeError, PoiseError) as exc:
            raise ProxyFailure('state_unavailable', str(exc),
                               'Восстановите исходное состояние через владельца материалов; не удаляйте неизвестную попытку.',
                               status='unknown', phase='handoff') from exc

    def save(self, state, *, replayed=False):
        atomic_write(self.directory / 'state.json',
                     json.dumps(state.to_dict(), ensure_ascii=False).encode('utf-8'), 0o600)
        result = state.project_result(self.response_path, replayed=replayed)
        SavedResponse(self.response_path, 0o600).save(result, canonical=True)
        return result


class NativeHandoffAdapter:
    def __init__(self, settings, directory, cwd, environment):
        self.settings, self.directory = settings, directory
        self.cwd, self.environment = cwd, environment

    def prepare(self):
        executable(self.settings.launcher)

    def run(self, packet):
        directory = self.directory / 'native' / uuid.uuid4().hex
        directory.mkdir(parents=True)
        source = directory / 'input.json'
        atomic_write(source, json.dumps(packet, ensure_ascii=False).encode('utf-8'), 0o600)
        capture = None
        try:
            with source.open('rb') as stream:
                capture = RegisteredCheckRunner().run(
                    'agent-handoff', [*self.settings.launcher, '--project', self.settings.project],
                    self.cwd, self.environment, None, directory / 'stdout.log', directory / 'stderr.log',
                    stdin_file=stream,
                )
            SavedResponse(directory / 'runner.json', 0o600).save(capture, canonical=True)
        except (OSError, ValueError, PoiseError) as exc:
            return {'response_path': None, 'result': None,
                    'process': {'exit_code': None if capture is None else capture['actual_exit_code'],
                                'stdout': None, 'stderr': None, 'runner_receipt': capture,
                                'raw_directory': str(directory), 'error': str(exc)}}
        process = {'exit_code': capture['actual_exit_code'], 'runner_receipt': capture,
                   'capture_complete': capture['capture_complete']}
        for name in ('stdout', 'stderr'):
            try:
                process[name] = (directory / (name + '.log')).read_text(encoding='utf-8')
            except (OSError, UnicodeError) as exc:
                process[name] = None
                process[name + '_error'] = str(exc)
        path, native, detail = None, None, None
        try:
            preview = strict_json(process['stdout'])
            path = preview['response_path']
            if not isinstance(path, str) or not Path(path).is_absolute():
                raise ValueError('Native response_path must be an absolute path')
            native = strict_json(Path(path).read_text(encoding='utf-8'))
            if not isinstance(native, dict):
                raise ValueError('Native response must be an object')
            if not capture['capture_complete'] or capture['timed_out'] or capture['cancelled']:
                detail = 'Native capture/termination is incomplete'
        except (OSError, ValueError, KeyError, TypeError, PoiseError) as exc:
            detail = str(exc)
        return {'response_path': path, 'result': native, 'process': process,
                'diagnostic': detail}
