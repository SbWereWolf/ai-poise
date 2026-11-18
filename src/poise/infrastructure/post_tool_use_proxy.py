"""Invocation-owned detached capture for original asynchronous native callbacks."""
import argparse
import json
import os
from pathlib import Path
import select
import subprocess
import time
import uuid

from ..common import PoiseError
from .goal_config import atomic_write, read_document, strict_json
from .initial_native_capture import capture_native_callback
from .session_start_proxy import SessionStartMemberExecutor, body
from .session_establishment import project_session_runtime_path


def save_member(settings, path, record):
    try:
        atomic_write(path, body(record), settings.raw['file_mode'], create_parent=False)
    except FileNotFoundError:
        # Session retirement owns removal; a late write must not recreate it.
        pass


class PostToolUseMemberExecutor(SessionStartMemberExecutor):
    def execute(self, position, handler):
        if not handler.get('async', False):
            return super().execute(position, handler)
        directory = self.reserve_member()
        path = directory / 'result.json'
        identifier = uuid.uuid4().hex
        job = {'schema': 'initial-native-callback-1', 'invocation_id': identifier,
            'settings_path': str(self.settings.path),
            'owner_project_config': self.configuration['owner_project_config'],
            'session_id': self.record['session_id'],
            'assigned_session_id': self.record['assigned_session_id'],
            'input_path': str(self.input), 'position': position, 'handler': handler,
            'cleanup_seconds': self.configuration['budgets']['cleanup_seconds'],
            'status': 'launching', 'launched': False, 'execution': None}
        save_member(self.settings, path, job)
        reference = {'position': position, 'command': handler['command'], 'mode': 'async',
                     'invocation_id': identifier, 'result_path': str(path), 'launch_status': 'launching'}
        self.record['members'].append(reference)
        self.save_record()
        reference['launch_status'] = self.launch(path, directory, job)
        self.save_record()
        # The native async command never contributes a sync reply/exit/control.
        return {'exit_code': 0, 'failed': False, 'response': None, 'stderr': b''}

    def launch(self, path, directory, job):
        read_fd, write_fd = os.pipe()
        child = None
        deadline = time.monotonic() + job['handler']['timeout']
        try:
            argv = [self.settings.raw['python'], '-P', '-B', '-m',
                'poise.infrastructure.post_tool_use_proxy', '--settings', str(self.settings.path),
                '--invocation', str(path), '--ack-fd', str(write_fd)]
            environment = dict(os.environ)
            environment['PYTHONPATH'] = self.settings.source_python_path()
            with (directory / 'worker.stdout').open('wb') as out, \
                    (directory / 'worker.stderr').open('wb') as err:
                child = subprocess.Popen(argv, cwd=Path.cwd(), env=environment,
                    stdin=subprocess.DEVNULL, stdout=out, stderr=err,
                    start_new_session=True, close_fds=True, pass_fds=(write_fd,))
            os.close(write_fd)
            write_fd = None
            received = b''
            while time.monotonic() < deadline:
                readable, _, _ = select.select([read_fd], [], [], max(0, deadline-time.monotonic()))
                if not readable:
                    break
                chunk = os.read(read_fd, 4096)
                if not chunk:
                    break
                received += chunk
                if b'\n' in received:
                    if strict_json(received.decode()) == {'invocation_id': job['invocation_id'], 'launched': True}:
                        return 'launched'
                    break
            actual = read_document(path)
            if actual['launched']:
                return 'launched'  # A genuine durable Popen fact survives lost ack.
            if actual['status'] == 'failed' or child.poll() is not None:
                if child.poll() is not None:
                    child.wait()
                return 'failed'
            return 'unknown'  # Do not replay a possibly launched native command.
        except (OSError, PoiseError, ValueError, UnicodeError) as exc:
            if child is None:
                job.update(status='failed', error={'kind': type(exc).__name__, 'detail': str(exc)})
                save_member(self.settings, path, job)
                return 'failed'
            # Only the child writes its terminal result after launch; the parent
            # keeps an uncertainty fact in its own separate aggregate record.
            return 'unknown'
        finally:
            os.close(read_fd)
            if write_fd is not None:
                os.close(write_fd)


def run(settings_path, invocation_path, ack_fd):
    from .hook_transport import HookSettings
    settings = HookSettings(settings_path)
    path = Path(invocation_path).resolve()
    job = read_document(path)
    if (job.get('schema') != 'initial-native-callback-1'
            or job['settings_path'] != str(settings.path)
            or job['owner_project_config'] != str(settings.project_config)
            or job['status'] != 'launching'):
        raise PoiseError('Select the original admitted native callback invocation')
    root = project_session_runtime_path(settings.project_config, job['assigned_session_id']) / 'initial-post-tool-use-proxy'
    source = Path(job['input_path'])
    if not path.is_relative_to(root) or source != path.parent.parent / 'input.json':
        raise PoiseError('Native callback capture is outside its admitted session owner')

    def launched(pid):
        job.update(status='running', launched=True, process_id=pid)
        save_member(settings, path, job)
        try:
            os.write(ack_fd, (json.dumps({'invocation_id': job['invocation_id'], 'launched': True}) + '\n').encode())
        except BrokenPipeError:
            pass  # The worker still owns terminal capture after the parent exits.

    try:
        member, outcome = capture_native_callback(settings, job['position'], job['handler'],
            source, path.parent, job['cleanup_seconds'], parse_response=False,
            launch_observer=launched)
        job.update(member, status='failed' if outcome['failed'] else 'completed')
        save_member(settings, path, job)
    except (PoiseError, OSError, ValueError, UnicodeError, KeyError) as exc:
        job.update(status='failed', error={'kind': type(exc).__name__, 'detail': str(exc)})
        save_member(settings, path, job)
    finally:
        os.close(ack_fd)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--settings', required=True)
    parser.add_argument('--invocation', required=True)
    parser.add_argument('--ack-fd', required=True, type=int)
    arguments = parser.parse_args()
    run(arguments.settings, arguments.invocation, arguments.ack_fd)


if __name__ == '__main__':
    main()
