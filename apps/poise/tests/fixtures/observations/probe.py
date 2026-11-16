"""Narrow test-owned dependency instrumentation around the actual application.

No missing observation behavior is implemented here. External tests first call
an unmodified CLI; these probes run only after that boundary exists.
"""
from collections import deque
import errno
import io
import json
import os
from pathlib import Path
import runpy
import sqlite3
import stat
import sys
import time

mode, install, config, prefix = sys.argv[1:]
prefix = Path(prefix)
parent = os.getpid()
plain_open, plain_io, plain_stat = open, io.open, Path.stat
plain_write = os.write


def audit(kind, **facts):
    with plain_open(prefix.with_suffix('.jsonl'), 'a') as stream:
        stream.write(json.dumps({'kind': kind, 'pid': os.getpid(), **facts}) + '\n')


def gate():
    prefix.with_suffix('.entered').touch()
    audit('gate_entered')
    deadline = time.monotonic() + 15
    while not prefix.with_suffix('.release').exists():
        if time.monotonic() >= deadline:
            raise TimeoutError('Fixture gate not released')
        time.sleep(.005)
    prefix.with_suffix('.done').touch()
    audit('gate_released')


# Register before the first fork, so late registration/start cannot disappear
# behind a returned CLI. Only these fixture-owned process tokens are observed.
workers=Path(str(prefix)+'.workers')
workers.mkdir()
original_fork,original_process_exit=os.fork,os._exit


def lifecycle_record(path,value):
    temporary=Path(str(path)+'.tmp')
    with plain_open(temporary,'w') as stream:stream.write(json.dumps(value))
    os.replace(temporary,path)

lifecycle={'sequence':0,'token':None,'worker':False}


def process_fork():
    caller=os.getpid()
    if caller==parent:
        token='capture-'+str(lifecycle['sequence']);lifecycle['sequence']+=1
        lifecycle['token']=token
        lifecycle_record(workers/(token+'.requested.json'),{'token':token,'owner_pid':parent})
    token=lifecycle['token']
    try:child=original_fork()
    except OSError as error:
        lifecycle_record(workers/(token+'.failed.json'),{'token':token,'errno':error.errno})
        raise
    if child==0:lifecycle['worker']=caller!=parent
    elif caller!=parent:
        lifecycle_record(workers/(token+'.registered.json'),{'token':token,'pid':child})
    return child


def process_exit(status):
    if lifecycle['worker']:
        token=lifecycle['token']
        lifecycle_record(workers/(token+'.terminal.json'),{'token':token,'pid':os.getpid(),'status':status})
    original_process_exit(status)


os.fork,os._exit=process_fork,process_exit

if mode == 'lifetime-control':
    def await_control(path):
        deadline=time.monotonic()+15
        while not path.exists():
            if time.monotonic()>=deadline:raise TimeoutError('Lifetime control not released')
            time.sleep(.005)
    for slot in range(2):
        intermediate=os.fork()
        if intermediate==0:
            # Preserve no caller transport pipe while the fixture controls startup.
            null=os.open(os.devnull,os.O_RDWR)
            for fd in (0,1,2):os.dup2(null,fd)
            if null>2:os.close(null)
            if slot==1:await_control(prefix.with_suffix('.start-1'))
            child=os.fork()
            if child:os._exit(0)
            await_control(prefix.with_suffix('.write-'+str(slot)))
            prefix.with_suffix('.payload-'+str(slot)).write_text('actual fixture write')
            os._exit(0)
        if slot==0:os.waitpid(intermediate,0)
    # Slot1 has a registered request but deliberately no worker registration yet.
    sys.stdout.write('primary-ready\n')
    raise SystemExit(0)

if mode == 'control':
    gate()
    raise SystemExit(0)

sys.path.insert(0, install)
if mode!='lifetime-only':
    settings = json.loads(Path(config).read_bytes())
    observation_path = Path(config).parent / settings['observations']['database']
    spool_path = Path(config).parent / 'spool'
    session_path = Path(settings['session_store'])
    if not session_path.is_absolute():
        session_path = Path(config).parent / session_path

if mode=='installation-no-session-io':
    original_resolve=Path.resolve
    def resolve(self,*args,**kwargs):
        if str(self)==str(session_path):
            raise PermissionError(errno.EACCES,'unselected Session path unavailable',str(self))
        return original_resolve(self,*args,**kwargs)
    Path.resolve=resolve

if mode=='prepare-mode-failure':
    os.umask(0o022)
    original_chmod=os.chmod
    def chmod(path,*args,**kwargs):
        if str(path)==str(observation_path):
            raise PermissionError(errno.EPERM,'own preparation mode step unavailable',str(path))
        return original_chmod(path,*args,**kwargs)
    os.chmod=chmod

if mode in ('clock-gate', 'bounds'):
    from infrastructure.observation_clock import SystemClock
    clock_observe = SystemClock.observe

    def observed_clock(self, *args, **kwargs):
        gate()
        return clock_observe(self, *args, **kwargs)

    SystemClock.observe = observed_clock

if mode in ('store-gate', 'receiver-commit-gate', 'uncertain-commit'):
    from infrastructure.observation_store import ObservationStore
    original_record = ObservationStore.record

    def record(self, *args, **kwargs):
        if mode == 'store-gate':
            gate()
        result = original_record(self, *args, **kwargs)
        if mode == 'receiver-commit-gate':
            gate()
        if mode == 'uncertain-commit':
            # Real receiver committed first. Simulate the caller losing that outcome.
            audit('committed_then_lost')
            raise sqlite3.OperationalError('fixture lost commit outcome')
        return result

    ObservationStore.record = record

if mode in ('spool-gate','spool-audit'):
    from infrastructure.telemetry_spool import TelemetrySpool
    original_enqueue = TelemetrySpool.enqueue

    def enqueue(self, *args, **kwargs):
        if mode == 'spool-gate':
            gate()
        try:
            return original_enqueue(self, *args, **kwargs)
        finally:
            if mode == 'spool-audit':
                audit('enqueue_finished')
                prefix.with_suffix('.done').touch()

    TelemetrySpool.enqueue = enqueue

if mode in ('read-cache','read-other-key'):
    from application.observations import ObservationOperations
    original_read = ObservationOperations.read

    def cached_read(self, *args, **kwargs):
        first = original_read(self, *args, **kwargs)
        audit('first_read', result=first)
        # An actual SQLite exclusive lock; repeat on the same service instance/key.
        connection = sqlite3.connect(observation_path, timeout=0, isolation_level=None)
        connection.execute('PRAGMA journal_mode=DELETE')
        connection.execute('BEGIN EXCLUSIVE')
        try:
            if mode == 'read-other-key':
                return original_read(self, dict(args[0],source='poise'), *args[1:], **kwargs)
            return original_read(self, *args, **kwargs)
        finally:
            connection.rollback()
            connection.close()

    ObservationOperations.read = cached_read

if mode == 'no-optional-io':
    def optional(value):
        if os.getpid() != parent or isinstance(value, int):
            return False
        name = os.path.abspath(os.fspath(value))
        return (name == str(observation_path) or name.startswith(str(spool_path)) or
                name.startswith(str(session_path)) or
                name == '/proc/sys/kernel/random/boot_id')

    def checked_open(original):
        def call(value, *args, **kwargs):
            if optional(value):
                audit('forbidden_optional_io', path=str(value), operation='open')
                raise AssertionError('Optional I/O before primary delivery')
            return original(value, *args, **kwargs)
        return call

    import builtins
    builtins.open = checked_open(plain_open)
    io.open = checked_open(plain_io)
    original_connect = sqlite3.connect

    def connect(value, *args, **kwargs):
        if optional(str(value).removeprefix('file:').split('?')[0]):
            audit('forbidden_optional_io', path=str(value), operation='sqlite')
            raise AssertionError('Optional DB access in primary')
        return original_connect(value, *args, **kwargs)

    sqlite3.connect = connect
    for method in ('stat', 'lstat', 'resolve'):
        original = getattr(Path, method)

        def filesystem(self, *args, _original=original, _method=method, **kwargs):
            if optional(self):
                audit('forbidden_optional_io', path=str(self), operation=_method)
                raise AssertionError('Optional path traversal in primary')
            return _original(self, *args, **kwargs)

        setattr(Path, method, filesystem)

if mode in ('pipe-block', 'pipe-short'):
    def write(fd, data):
        if os.getpid() == parent and fd > 2 and stat.S_ISFIFO(os.fstat(fd).st_mode):
            audit('final_write', size=len(data), blocking=os.get_blocking(fd))
            if mode == 'pipe-block':
                raise BlockingIOError(errno.EAGAIN, 'fixture final pipe full')
            if len(data) <= 1:
                return 0
            return plain_write(fd, data[:-1])
        return plain_write(fd, data)

    os.write = write

if mode == 'bounds':
    from infrastructure.telemetry_dispatch import CaptureDispatcher
    begin = CaptureDispatcher.begin_capture

    def bounded(self, *args, **kwargs):
        tokens = [begin(self, *args, **kwargs) for _ in range(12)]
        summary = self.summary()
        collections = {key: len(value) for key, value in vars(self).items()
                       if isinstance(value, (dict, list, set, deque))}
        audit('bounds', summary=summary, collections=collections,
              admitted=sum(token is not None for token in tokens))
        return next((token for token in tokens if token is not None), None)

    CaptureDispatcher.begin_capture = bounded

if mode == 'recent-history':
    from infrastructure.telemetry_dispatch import CaptureDispatcher
    from application.telemetry import TelemetryOperations
    sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
    from observations_support import retained_identities
    original_new=CaptureDispatcher.__new__
    original_init=CaptureDispatcher.__init__
    shared={}

    def shared_new(cls,*args,**kwargs):
        if 'dispatcher' not in shared:
            shared['dispatcher']=(object.__new__(cls) if original_new is object.__new__
                                  else original_new(cls,*args,**kwargs))
        return shared['dispatcher']

    def shared_init(self,*args,**kwargs):
        if not shared.get('initialized'):
            original_init(self,*args,**kwargs)
            shared['initialized']=True

    CaptureDispatcher.__new__=staticmethod(shared_new)
    CaptureDispatcher.__init__=shared_init
    original_invoke=TelemetryOperations.invoke

    def invoke_history(self,*args,**kwargs):
        first=None
        for index in range(12):
            # Run the real use case anew: UUID generation is not copied/replaced.
            result=original_invoke(self,*args,**kwargs)
            if index==0:first=result
            dispatcher=shared['dispatcher']
            deadline=time.monotonic()+5
            while dispatcher.summary()['pending']:
                if time.monotonic()>=deadline:
                    raise TimeoutError('Actual capture failed to finish')
                time.sleep(.005)
        with sqlite3.connect(observation_path.as_uri()+'?mode=ro',uri=True,timeout=0) as db:
            identities=[row[0] for row in db.execute(
                'SELECT event_id FROM observation_events WHERE kind=? AND source=? ORDER BY seq',
                ('operation','poise'))]
        audit('recent_history',generated_ids=identities,
              retained_ids=retained_identities(dispatcher,identities),
              summary=dispatcher.summary(),shared_dispatcher_instances=1)
        return first

    TelemetryOperations.invoke=invoke_history

if mode == 'projection-fault':
    sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
    from observations_support import install_projection_fault
    original_connect=sqlite3.connect

    def with_fault(location,*args,**kwargs):
        connection=original_connect(location,*args,**kwargs)
        name=str(location).removeprefix('file:').split('?')[0]
        if os.path.abspath(name)==str(observation_path):
            install_projection_fault(connection,lambda facts:audit('transaction_fault',**facts))
        return connection

    sqlite3.connect=with_fault

if mode in ('deterministic-capture','completed-event-overflow'):
    from dataclasses import is_dataclass,replace
    from infrastructure.observation_clock import SystemClock
    original_observe=SystemClock.observe
    vectors=json.loads((Path(__file__).parent/'vectors.json').read_text())
    samples=vectors['automatic_clocks']
    if mode=='completed-event-overflow':
        for sample in samples:sample['comparison_domain']=vectors['oversized_clock_domain']
    clock_state={'used':False,'count':0}
    original_exit=os._exit

    def worker_exit(status):
        if os.getpid()!=parent and clock_state['used']:
            audit('clock_worker_exit',status=status,clock_calls=clock_state['count'])
            prefix.with_suffix('.worker-done').touch()
        original_exit(status)

    os._exit=worker_exit

    def deterministic_clock(self,*args,**kwargs):
        clock_state['used']=True
        index=clock_state['count'];clock_state['count']+=1
        if mode=='completed-event-overflow' and index==0:gate()
        sample=dict(samples[index])
        # Preserve only the adapter's carrier type; values/oracles are literal.
        carrier=original_observe(self,*args,**kwargs)
        if is_dataclass(carrier):value=replace(carrier,**sample)
        elif isinstance(carrier,dict):value=sample
        else:raise TypeError('Clock carrier is not the declared observation value')
        audit('supplied_clock',index=index,sample=sample)
        return value

    SystemClock.observe=deterministic_clock

sys.argv = [str(Path(install) / 'bin/poise'), '--config', config]
runpy.run_path(sys.argv[0], run_name='__main__')
