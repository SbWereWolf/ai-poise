"""Test-owned observation fixtures, external consumer and independent SQL effects."""
from contextlib import closing
from copy import deepcopy
import json
import os
from pathlib import Path
import select
import shutil
import signal
import sqlite3
import subprocess
import sys
import time

from reader_support import exact
from session_support import Installation, CALLS as SESSION_CALLS
from state_sql import SQL_LOG

FIX = Path(__file__).parent / 'fixtures/observations'
CALLS, CONTROLS, EFFECTS = [], [], []


def literal(name=None):
    value = json.loads((FIX / 'vectors.json').read_text())
    return value if name is None else value[name]


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, allow_nan=False,
                       separators=(',', ':')) + '\n').encode()


def event(**fields):
    value = literal('event')
    value.update(fields)
    return value


def packet(operation, value):
    return {'schema': 'stride-poise/v1', 'operation': operation, 'input': value}


def envelope(result=None, status='ok', error=None):
    return {'schema': 'stride-poise/v1', 'status': status, 'result': result,
            'error': error, 'evidence': []}


MISSING = literal('missing')


def result_contract(operation, body):
    fields=literal('result_fields')
    result=body['result']
    if operation not in fields or result is None or body==MISSING:
        return
    exact(set(result),set(fields[operation]))
    exact(result['schema'],literal('result_schemas')[operation])
    if operation=='observations.record':
        exact(type(result['count']),int)
        exact(result['count'],len(result['entries']))
        for row in result['entries']:
            exact(set(row),set(literal('record_entry_fields')))
            exact(type(row['index']),int);exact(type(row['seq']),int)
            exact(type(row['replayed']),bool)
    if operation=='observations.read' and result['events'] is not None:
        for row in result['events']:
            exact(set(row),set(literal('event_view_fields')))
            exact(type(row['seq']),int)
    if operation=='telemetry.invoke':
        exact(set(result['primary']),{'schema','status','result','error','evidence'})
        exact(result['primary']['schema'],'stride-poise/v1')
        exact(body['status'],result['primary']['status'])
        exact(body['error'],result['primary']['error'])
        exact(set(result['telemetry']),{'state','event_id','coverage','stats','error'})
        exact(set(result['telemetry']['stats']),
              {'submitted','processed','duplicates','failed','dropped','pending'})
        for value in result['telemetry']['stats'].values():
            exact(type(value),int);assert value>=0
    if operation=='telemetry.replay':
        for field in ('attempted','delivered','failed','corrupt','pending'):
            exact(type(result[field]),int);assert result[field]>=0


class MissingObservation(AssertionError):
    """Qualified actual public dispatch refusal, never a fixture/import failure."""


class DiagnosticError(RuntimeError):
    """Independent setup/control failed; no meaningful RED claim."""


def wait_file(path, seconds=5):
    end = time.monotonic() + seconds
    while not path.exists():
        if time.monotonic() >= end:
            raise DiagnosticError('Fixture gate did not respond: ' + str(path))
        time.sleep(.005)


def sql(path, query, parameters=(), *, write=False):
    location = str(path) if write else Path(path).as_uri() + '?mode=ro'
    record = {'database': str(path), 'sql': query, 'parameters': list(parameters)}
    SQL_LOG.append(record)
    with closing(sqlite3.connect(location, uri=not write, timeout=0)) as db:
        rows = [list(row) for row in db.execute(query, parameters)]
        if write:
            db.commit()
    record['rows'] = rows
    return rows


def retained_identities(owner, identities):
    """Inspect only retained references to known fixture IDs, not private names.

    No arbitrary object serialization, property execution or source-value oracle.
    Drained dispatcher state has no live capture justification for older IDs.
    """
    expected=set(identities);found=set();visited=set()
    def inspect(value):
        if id(value) in visited:
            return
        visited.add(id(value))
        if isinstance(value,(str,bytes)):
            text=value.decode('utf-8',errors='replace') if isinstance(value,bytes) else value
            found.update(identity for identity in expected if identity in text)
        elif isinstance(value,dict):
            for key,item in value.items():inspect(key);inspect(item)
        elif isinstance(value,(list,tuple,set,frozenset)) or type(value).__name__=='deque':
            for item in value:inspect(item)
        elif not callable(value) and hasattr(value,'__dict__'):
            inspect(vars(value))
    inspect(owner)
    return sorted(found)


def history_bound(identities, retained, maximum):
    assert len(set(identities))>2*maximum, 'Distinct generated-ID traffic not established'
    assert set(retained)<=set(identities)
    assert len(set(retained))<=2*maximum, 'Historical identity memory exceeded bound'


def admitted_or_dropped(metadata):
    assert metadata['state'] in ('admitted','dropped'),metadata
    exact(metadata['coverage'],'partial')
    # Admission is not a completed-worker verdict; pending1/dropped0 is valid.
    for value in metadata['stats'].values():
        exact(type(value),int);assert value>=0


def install_projection_fault(connection, observe):
    def fail():
        events=[list(row) for row in connection.execute(
            'SELECT event_id,contribution_json FROM observation_events '
            'WHERE source=? AND stream=? ORDER BY seq',('vendor','transaction-stream'))]
        streams=[list(row) for row in connection.execute(
            'SELECT source,stream,last_sequence FROM observation_streams '
            'WHERE source=? AND stream=?',('vendor','transaction-stream'))]
        observe({'in_transaction':connection.in_transaction,'events':events,'streams':streams})
        raise RuntimeError('fixture failure after both real writes')
    connection.create_function('fixture_projection_failure',0,fail)


def await_fixture_workers(prefix,seconds=5):
    """CLI parents are already reaped; pre-fork tokens close late-start gaps."""
    directory=Path(str(prefix)+'.workers')
    if not directory.is_dir():raise DiagnosticError('Fixture worker registry missing: '+str(directory))
    requests=[json.loads(path.read_text()) for path in sorted(directory.glob('*.requested.json'))]
    deadline=time.monotonic()+seconds
    while True:
        completed=[];missing=[];rejected=[]
        for request in requests:
            token=request['token']
            failed=directory/(token+'.failed.json')
            registered=directory/(token+'.registered.json')
            terminal=directory/(token+'.terminal.json')
            if failed.exists():
                rejected.append(json.loads(failed.read_text()));continue
            if not registered.exists() or not terminal.exists():missing.append(token);continue
            owner=json.loads(registered.read_text());end=json.loads(terminal.read_text())
            if owner['pid']!=end['pid'] or owner['token']!=token or end['token']!=token:
                raise DiagnosticError('Fixture worker lifecycle mismatch: '+token)
            completed.append(end)
        if not missing:
            return {'prefix':str(prefix),'requested':len(requests),'completed':completed,'fork_refused':rejected}
        if time.monotonic()>=deadline:
            raise DiagnosticError('Fixture workers not terminal; retained '+str(directory)+'; missing '+str(missing))
        time.sleep(min(.005,max(0,deadline-time.monotonic())))


def retire_fixture(prefixes,retire,seconds=5):
    completed=[await_fixture_workers(prefix,seconds) for prefix in prefixes]
    retire()
    return completed


class Observations:
    def __enter__(self):
        self.session = Installation().__enter__()
        try:
            self.session.prepare()
            self.session.generated()
            _, _, response = self.session.call('session.ensure')
            self.identity = response['result']['session']['id']
            if not self.identity:
                raise DiagnosticError('Existing Session setup did not produce identity')
            self.root = self.session.path.parent
            self.config = self.session.config
            self.config.update(literal('config'))
            self.database = self.root / 'observations.sqlite'
            self.spool = self.root / 'spool'
            self.session_before = self.session.facts()
            self.raw = self.root / 'raw.bin'
            self.raw.write_bytes(bytes.fromhex('4142ff0a0043'))
            self.children = []
            self.qualify_primary()
            return self
        except BaseException:
            self.session.__exit__(*sys.exc_info())
            raise

    def __exit__(self, *args):
        prefixes=getattr(self,'gates',[])
        try:
            for prefix in prefixes:prefix.with_suffix('.release').touch()
            for child in self.children:
                if child.poll() is None:child.kill()
                child.communicate(timeout=3)
            completed=retire_fixture(prefixes,lambda:self.session.__exit__(*args))
            if prefixes:EFFECTS.append({'fixture_retired_after_workers':completed})
        except BaseException as error:
            # An unknown writer outcome must keep the root, including GC cleanup.
            self.session.state.temporary._finalizer.detach()
            EFFECTS.append({'fixture_retirement':'unconfirmed','retained_root':str(self.session.root),
                            'reason':type(error).__name__+': '+str(error)})
            raise

    def seed(self):
        with closing(sqlite3.connect(self.database)) as db:
            db.executescript((FIX / 'schema.sql').read_text())
            db.execute('INSERT INTO observation_meta(id,schema_version,installation_id) VALUES(?,?,?)',
                       (1, 1, self.config['installation_id']))
            db.commit()
        CONTROLS.append({'fixture_database': str(self.database), 'schema': 'literal design4 schema1'})

    def put(self, value, contribution=None, duration=None, session='current'):
        identity = self.identity if session == 'current' else session
        sql(self.database, 'INSERT INTO observation_events(kind,source,stream,event_id,session_id,'
            'source_mode,event_json,contribution_json,duration_ns) VALUES(?,?,?,?,?,?,?,?,?)',
            (value['kind'], value['source'], value['stream'], value['event_id'], identity,
             self.config['observations']['sources'][value['source']], encoded(value).decode(),
             None if contribution is None else encoded(contribution).decode(),
             None if duration is None else str(duration)), write=True)

    def facts(self):
        return {
            'events': sql(self.database, 'SELECT seq,kind,source,stream,event_id,session_id,'
                          'source_mode,event_json,contribution_json,duration_ns '
                          'FROM observation_events ORDER BY seq'),
            'streams': sql(self.database, 'SELECT source,stream,mode,last_sequence,last_sample '
                           'FROM observation_streams ORDER BY source,stream')}

    def read_input(self, **fields):
        value = {'scope': {'kind': 'installation'}, 'source': None, 'after': 0, 'limit': 16}
        value.update(fields)
        return value

    def output_input(self):
        return {'sources': [{'path': str(self.raw), 'captured_bytes': 6}]}

    def launch(self, operation, value, *, mode=None):
        # Lifecycle-only observation retains unchanged producer/protocol behavior.
        if mode is None and operation=='telemetry.invoke':mode='lifetime-only'
        config_raw = self.session.write_config()
        argv = [sys.executable, '-I', '-S', '-B']
        prefix = self.root / ('probe-' + str(len(CALLS)))
        if mode:
            argv += [str(FIX / 'probe.py'), mode, str(self.session.install),
                     str(self.session.path), str(prefix)]
            self.gates = getattr(self, 'gates', []) + [prefix]
        else:
            argv += [str(self.session.install / 'bin/poise'), '--config', str(self.session.path)]
        raw = encoded(packet(operation, value))
        child = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, cwd=self.session.state.cwd,
                                 env=self.session.environment(), start_new_session=True)
        child.stdin.write(raw)
        child.stdin.close()
        child.stdin = None
        self.children.append(child)
        record = {'argv': argv, 'cwd': str(self.session.state.cwd), 'pid': child.pid,
                  'environment': self.session.environment(), 'input_hex': raw.hex(),
                  'config_hex': config_raw.hex(), 'mode': mode}
        CALLS.append(record)
        return child, record, prefix

    def collect(self, launched):
        child, record, prefix = launched
        try:
            out, err = child.communicate(timeout=8)
        except subprocess.TimeoutExpired as error:
            child.kill()
            out, err = child.communicate(timeout=3)
            record.update(stdout_hex=out.hex(), stderr_hex=err.hex(), exit_code=child.returncode)
            raise DiagnosticError('CLI or inherited pipe did not finish; not missing-entry RED') from error
        record.update(stdout_hex=out.hex(), stderr_hex=err.hex(), exit_code=child.returncode)
        if err or not out.endswith(b'\n') or out.endswith(b'\n\n'):
            raise DiagnosticError('Unexpected stderr or incomplete public JSON framing')
        try:
            body = json.loads(out)
        except (ValueError, UnicodeError) as error:
            raise DiagnosticError('Invalid UTF8/JSON transport') from error
        exact(set(body), {'schema', 'status', 'result', 'error', 'evidence'})
        exact(body['schema'], 'stride-poise/v1')
        exact(child.returncode, {'ok': 0, 'unavailable': 1, 'rejected': 2}[body['status']])
        if body['error'] is not None:
            exact(set(body['error']), {'code', 'details', 'action'})
        record['response'] = body
        request=json.loads(bytes.fromhex(record['input_hex']))
        result_contract(request['operation'],body)
        audit = prefix.with_suffix('.jsonl')
        record['audit'] = [json.loads(x) for x in audit.read_text().splitlines()] if audit.exists() else []
        return body

    def call(self, operation, value=None, *, mode=None):
        return self.collect(self.launch(operation, {} if value is None else value, mode=mode))

    def require(self, operation, value=None):
        body = self.call(operation, value)
        if body == MISSING:
            CALLS[-1]['qualified_missing_operation'] = True
            raise MissingObservation('Qualified absent ' + operation + ' at /operation')
        return body

    def ok(self, operation, value=None):
        body = self.require(operation, value)
        exact(body['status'], 'ok')
        exact(body['error'], None)
        exact(body['evidence'], [])
        return body['result']

    def record(self, *events):
        return self.ok('observations.record', {'events': list(events)})

    def read(self, **fields):
        return self.ok('observations.read', self.read_input(**fields))

    def unchanged_session(self):
        exact(self.session.facts(), self.session_before)

    def qualify_primary(self):
        status = self.call('session.status')
        exact(status['status'], 'ok')
        exact(status['result']['session']['id'], self.identity)
        raw = self.call('output.range', {'reads': [{'source': self.output_input()['sources'][0], 'start': 1, 'count': 3}]})
        exact(raw['status'], 'ok')
        exact(raw['result']['ranges'][0]['data'], {'encoding': 'base64', 'value': 'Qv8K'})
        exact(self.call('missing-observations-control'), MISSING)
        CONTROLS.append({'copied_entry': str(self.session.install), 'session': self.identity,
                         'raw_binary_range': 'Qv8K', 'unknown_operation_control': 'qualified'})

    def durable(self):
        self.config['telemetry']['mode'] = 'durable'
        self.config['telemetry']['spool'] = literal('spool_policy')

    def spool_entry(self, *, attempts=0, bad=False):
        self.spool.mkdir(exist_ok=True)
        value = literal('spool_record')
        value['envelope'] = literal('spool_envelope')
        value['attempts'] = attempts
        path = self.spool / (value['envelope']['id'] + '.json')
        path.write_bytes(b'{' if bad else encoded(value))
        return path, value

    def instrument(self, mode='ok'):
        audit = self.root / 'instrument-input.json'
        self.config['measurement']['tokenizer'] = {
            'kind': 'command', 'identity': 'literal-counter', 'command': {
                'argv': [sys.executable, '-I', '-S', '-B', str(FIX / 'instrument.py'), mode, str(audit)],
                'cwd': str(self.root), 'env': {}, 'timeout_seconds': 2, 'max_output_bytes': 1024}}
        return audit

    def invoke(self, operation='output.minimal', value=None):
        return self.require('telemetry.invoke', {'operation': operation,
            'input': self.output_input() if value is None else value})

    def await_worker(self, launched):
        _,record,prefix=launched
        wait_file(prefix.with_suffix('.worker-done'))
        audit=prefix.with_suffix('.jsonl')
        facts=[json.loads(line) for line in audit.read_text().splitlines()]
        record['audit_after_worker_completion']=facts
        return facts


    def qualify_invoke(self):
        # Qualify the new boundary with an explicitly invalid optional policy,
        # which cannot schedule background work in the later isolated scenario.
        saved=self.config['telemetry']['max_pending']
        self.config['telemetry']['max_pending']=0
        try:
            body=self.invoke()
            exact(body['status'],'ok')
            exact(body['result']['telemetry']['state'],'unavailable')
        finally:
            self.config['telemetry']['max_pending']=saved



def qualify(root):
    """Actual independent controls; none implement future observation behavior."""
    exact(sys.version_info[:2], (3, 13))
    values = literal()
    for vector in values['texts']:
        exact([len(vector['added'].encode()), len(vector['removed'].encode())], vector['bytes'])
        exact([len(vector['added'].splitlines(keepends=True)),
               len(vector['removed'].splitlines(keepends=True))], vector['lines'])
    audit = Path(root) / 'instrument-control.json'
    process = subprocess.run([sys.executable, '-I', '-S', '-B', str(FIX / 'instrument.py'),
                              'ok', str(audit)], input=encoded(values['instrument_input']), capture_output=True)
    CONTROLS.append({'instrument_argv':process.args,'input_hex':encoded(values['instrument_input']).hex(),
                     'exit_code':process.returncode,'stdout_hex':process.stdout.hex(),
                     'stderr_hex':process.stderr.hex()})
    exact(process.returncode, 0)
    exact(json.loads(process.stdout), {'counts': [7, 3]})
    exact(json.loads(audit.read_bytes()), values['instrument_input'])
    database = Path(root) / 'busy-control.sqlite'
    with closing(sqlite3.connect(database, timeout=0)) as first, closing(sqlite3.connect(database, timeout=0)) as second:
        first.execute('CREATE TABLE control(value INTEGER)')
        first.execute('BEGIN EXCLUSIVE')
        try:
            second.execute('SELECT value FROM control').fetchall()
        except sqlite3.OperationalError as error:
            assert 'locked' in str(error), str(error)
        else:
            raise DiagnosticError('Actual SQLite busy control did not refuse')
        first.rollback()
    # Same generic gate function used by instrumented dependency adapters.
    prefix = Path(root) / 'gate-control'
    child = subprocess.Popen([sys.executable, '-I', '-S', '-B', str(FIX / 'probe.py'),
        'control', '', '', str(prefix)], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        wait_file(prefix.with_suffix('.entered'))
        exact(child.poll(), None)
        prefix.with_suffix('.release').touch()
        out, err = child.communicate(timeout=5)
        CONTROLS.append({'gate_argv':child.args,'pid':child.pid,'exit_code':child.returncode,
                         'stdout_hex':out.hex(),'stderr_hex':err.hex(),'blocked_before_release':True})
        exact((child.returncode, out, err), (0, b'', b''))
        wait_file(prefix.with_suffix('.done'))
    finally:
        if child.poll() is None:
            child.kill()
            child.communicate(timeout=2)
    # Real nonblocking full pipe, and independently detected short-write loss.
    read_fd, write_fd = os.pipe()
    try:
        os.set_blocking(write_fd, False)
        while True:
            try:
                os.write(write_fd, b'x' * 4096)
            except BlockingIOError:
                break
        poll = select.poll(); poll.register(read_fd, select.POLLIN)
        assert poll.poll(0)
        exact(os.get_blocking(write_fd), False)
        exact(os.read(read_fd, 1), b'x')
    finally:
        os.close(read_fd); os.close(write_fd)
    ids=literal('history_control_ids')
    unbounded=retained_identities({'cache':set(ids)},ids)
    exact(len(unbounded),12)
    try:
        history_bound(ids,unbounded,2)
    except AssertionError:
        pass
    else:
        raise DiagnosticError('Disabled identity eviction was not detected')
    try:
        history_bound([ids[0]]*12,[ids[0]],2)
    except AssertionError:
        pass
    else:
        raise DiagnosticError('Repeated one-ID traffic was mistaken for eviction coverage')
    bounded=retained_identities({'any-name':set(ids[-4:])},ids)
    history_bound(ids,bounded,2)
    admitted_or_dropped(literal('admitted_pending_control'))
    CONTROLS.append({'history_sensitivity':'12distinct retained rejected;4retained accepted',
                     'pending_primary_branch':'admitted/partial,pending1,dropped0 accepted'})
    # Qualify the exact trigger/UDF fault on an independent fixture-owned SQLite.
    database=Path(root)/'transaction-fault-control.sqlite'
    fault_observations=[]
    with closing(sqlite3.connect(database,isolation_level=None)) as db:
        db.executescript((FIX/'schema.sql').read_text())
        db.executescript((FIX/'projection-fault.sql').read_text())
        install_projection_fault(db,fault_observations.append)
        db.execute('BEGIN IMMEDIATE')
        value=event(stream='transaction-stream',event_id='faulted')
        db.execute('INSERT INTO observation_events(kind,source,stream,event_id,source_mode,event_json) '
                   'VALUES(?,?,?,?,?,?)',('usage','vendor','transaction-stream','faulted','reported',encoded(value).decode()))
        try:
            db.execute('INSERT INTO observation_streams(source,stream,mode,last_sequence,last_sample) '
                       'VALUES(?,?,?,?,?)',('vendor','transaction-stream','delta','1',encoded(value).decode()))
        except sqlite3.OperationalError:
            db.rollback()
        else:
            raise DiagnosticError('Real in-transaction projection fault did not fire')
        exact(len(fault_observations),1)
        exact(fault_observations[0]['in_transaction'],True)
        assert fault_observations[0]['events'] and fault_observations[0]['streams']
        exact(db.execute('SELECT event_id FROM observation_events').fetchall(),[])
        exact(db.execute('SELECT stream FROM observation_streams').fetchall(),[])
    CONTROLS.append({'sqlite_trigger_udf_inside_transaction':fault_observations,
                     'rollback_control':'both actual newly written rows removed'})
    control_root=Path(root)/'lifetime-control';control_root.mkdir()
    prefix=control_root/'probe'
    process=subprocess.Popen([sys.executable,'-I','-S','-B',str(FIX/'probe.py'),
                              'lifetime-control','','',str(prefix)],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    out,err=process.communicate(timeout=5)
    CONTROLS.append({'lifetime_control_argv':process.args,'pid':process.pid,'exit_code':process.returncode,
                     'stdout_hex':out.hex(),'stderr_hex':err.hex()})
    exact((process.returncode,out,err),(0,b'primary-ready\n',b''))
    retired=[]
    def retire():
        retired.append(True)
        shutil.rmtree(control_root)
    diagnostics=[]
    try:
        try:retire_fixture([prefix],retire,.03)
        except DiagnosticError as error:diagnostics.append(str(error))
        else:raise DiagnosticError('Retired before registered/late workers finished')
        exact(retired,[]);assert control_root.exists()
        # First worker completes; late registration is still an outstanding token.
        prefix.with_suffix('.write-0').touch()
        wait_file(Path(str(prefix)+'.workers')/'capture-0.terminal.json')
        try:retire_fixture([prefix],retire,.03)
        except DiagnosticError as error:diagnostics.append(str(error))
        else:raise DiagnosticError('Missing late worker registration was ignored')
        exact(retired,[]);assert control_root.exists()
        prefix.with_suffix('.start-1').touch()
        wait_file(Path(str(prefix)+'.workers')/'capture-1.registered.json')
        try:retire_fixture([prefix],retire,.03)
        except DiagnosticError as error:diagnostics.append(str(error))
        else:raise DiagnosticError('Registration was mistaken for terminal completion')
        exact(retired,[]);assert control_root.exists()
        prefix.with_suffix('.write-1').touch()
        completed=retire_fixture([prefix],retire)
        exact(retired,[True]);assert not control_root.exists()
        exact(completed[0]['requested'],2);exact(len(completed[0]['completed']),2)
        CONTROLS.append({'fixture_lifetime_control':'passed','primary_available_before_retirement':True,
                         'explicit_timeout_diagnostics':diagnostics,'actual_completed_workers':completed,
                         'retired_only_after_all_workers':True})
    finally:
        if control_root.exists():
            for suffix in ('.write-0','.start-1','.write-1'):prefix.with_suffix(suffix).touch()
            # Evidence/resources remain if any terminal signal is still unknown.
            await_fixture_workers(prefix)
    CONTROLS.append({'python': sys.version, 'sqlite': sqlite3.sqlite_version,
                     'literal_utf8_vectors': len(values['texts']), 'instrument': 'qualified',
                     'sqlite_busy': 'qualified', 'gate_and_cleanup': 'qualified',
                     'real_nonblocking_full_pipe': 'qualified'})
    return CONTROLS[-1]
