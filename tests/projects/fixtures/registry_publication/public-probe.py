"""Fresh real CLI process; forward all observed owner/lock/write boundaries."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import runpy
import sys

control = json.loads(Path(sys.argv[1]).read_text())
stream = os.fdopen(control['event_fd'], 'w', buffering=1)


def emit(event, **data):
    stream.write(json.dumps({'event': event, 'pid': os.getpid(), **data}) + '\n')
    stream.flush()


def permit():
    if os.read(control['permit_fd'], 1) != b'1':
        raise RuntimeError('Fixture permit channel closed before release')


import poise.infrastructure.locking as locking
real_lock = locking.exclusive_lock
real_flock = locking.fcntl.flock
lock_frames = []


def observed_flock(fd, operation):
    selected = bool(lock_frames and lock_frames[-1]['selected'])
    details = {'fd': fd, 'operation': operation, 'path': lock_frames[-1]['path']} if selected else {}
    if selected and operation & locking.fcntl.LOCK_EX:
        emit('registry_kernel_attempt', **details)
    try:
        value = real_flock(fd, operation)
    except BlockingIOError:
        if selected:
            emit('registry_kernel_blocked', **details)
        raise
    if selected:
        emit('registry_kernel_released' if operation == locking.fcntl.LOCK_UN
             else 'registry_kernel_acquired', **details)
    return value


locking.fcntl.flock = observed_flock


@contextmanager
def observed_lock(path, wait, poll, **kwargs):
    selected = Path(path).resolve() == Path(control['registry_lock']).resolve()
    acquired = False
    if selected:
        emit('registry_lock_attempt', path=str(path), wait=wait, poll=poll)
    lock_frames.append({'selected': selected, 'path': str(path)})
    try:
        with real_lock(path, wait, poll, **kwargs):
            acquired = True
            if selected:
                emit('registry_lock_acquired', path=str(path))
            yield
    finally:
        if selected and acquired:
            emit('registry_lock_released', path=str(path))
        lock_frames.pop()


# Install observation before the real modules bind their primitive imports.
locking.exclusive_lock = observed_lock
import poise.infrastructure.projects as projects
import poise.infrastructure.project_config as config
import poise.application.projects as setup_application
import poise.application.project_config as profile_application

real_registry = projects.FileProjectSetup._registry
real_publish = projects.publish_registry
real_probe = projects.FileProjectSetup._probe
real_atomic = config.atomic_write


def observed_registry(owner):
    document = real_registry(owner)
    emit('registry_read', document=document)
    return document


def observed_publish(settings, document):
    emit('registry_publish_enter', path=str(settings.registry), document=document)
    if control['hold_publisher']:
        permit()
    if control['fault'] == 'registry':
        emit('fault_reached', phase='registry', path=str(settings.registry))
        raise OSError(control['io_marker'])
    result = real_publish(settings, document)
    emit('registry_publish_complete', path=str(settings.registry))
    return result


def observed_probe(owner, candidate, requested):
    if control['hold_probe']:
        emit('setup_probe_held', project=candidate['project'])
        permit()
    return real_probe(owner, candidate, requested)


def observed_atomic(path, content, mode):
    if control['fault'] == 'receipt' and Path(path).name.endswith('.publication.json'):
        emit('fault_reached', phase='receipt', path=str(path))
        raise OSError(control['io_marker'])
    return real_atomic(path, content, mode)


def forward_apply(original):
    def observed(owner, request):
        emit('public_apply_enter', request=request)
        value = original(owner, request)
        emit('public_apply_complete', result=value)
        return value
    return observed


projects.FileProjectSetup._registry = observed_registry
projects.FileProjectSetup._probe = observed_probe
projects.publish_registry = observed_publish
config.atomic_write = observed_atomic
setup_application.ProjectCommands.apply = forward_apply(setup_application.ProjectCommands.apply)
profile_application.ProjectConfigCommands.apply = forward_apply(profile_application.ProjectConfigCommands.apply)
imports = {name: str(Path(module.__file__).resolve()) for name, module in {
    'locking': locking, 'projects': projects, 'project_config': config,
    'setup_application': setup_application, 'profile_application': profile_application,
}.items()}
emit('process_subject', imports=imports, settings=control['settings'],
     cwd=str(Path.cwd()), registry_lock=control['registry_lock'])
sys.argv = ['poise', control['command'], '--settings', control['settings']]
try:
    runpy.run_module('poise', run_name='__main__')
except SystemExit as exc:
    emit('public_exit', code=exc.code)
    raise
except BaseException as exc:
    emit('public_exception', exception=type(exc).__name__, message=str(exc))
    raise
finally:
    emit('process_end')
