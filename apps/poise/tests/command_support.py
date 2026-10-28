"""External command consumer, real isolated environment, strict independent oracles."""
from copy import deepcopy
import json
import os
from pathlib import Path
import signal
import sqlite3
import stat
import subprocess
import time

from generic_checks import PY
from reader_support import exact
from session_support import Installation


FIX = Path(__file__).parent / 'fixtures/command'
PEER = FIX / 'peer.py'
OBSERVATIONS = []


def load(name='setup.json'):
    return json.loads((FIX / name).read_text())


class DiagnosticError(RuntimeError):
    """A failed environment or consumer is never the intended RED."""


class MissingCommand(AssertionError):
    """Only exact missing dispatch after real environment qualification."""


def await_file(path, seconds=3):
    until = time.monotonic() + seconds
    while not Path(path).exists():
        if time.monotonic() >= until:
            raise DiagnosticError('Independent peer did not reach gate: ' + str(path))
        time.sleep(.005)


def lines(path):
    return [json.loads(line) for line in Path(path).read_text().splitlines()] if Path(path).exists() else []


def problem(code, details, action):
    return {'code': code, 'details': details, 'action': action}


def envelope(result=None, error=None, status='ok'):
    expected = load()['envelope']
    expected.update(result=result, error=error, status=status)
    return expected


def retained_snapshot(response):
    """Read full bytes NOW; metadata consistency supplements independent scenario oracles."""
    metadata=response['result']
    exact(set(metadata),set(load()['metadata']))
    full_ref=metadata['full_result_ref']
    exact(set(full_ref),{'path','bytes'})
    assert type(full_ref['path']) is str and Path(full_ref['path']).is_absolute()
    assert type(full_ref['bytes']) is int and full_ref['bytes']>=0
    raw=Path(full_ref['path']).read_bytes()
    exact(len(raw),full_ref['bytes'])
    full=json.loads(raw)
    exact(set(full),set(load()['full_result']))
    exact(full['schema'],'poise-command-result/v1')
    for name in load()['full_result']:
        if name not in ('schema','mcp_response'):
            exact(full[name],metadata[name])
    assert full['mcp_response'] is None or type(full['mcp_response']) is dict
    streams={}
    for name in ('stdout','stderr'):
        source=metadata[name]
        exact(set(source),{'source_id','path','bytes','complete'})
        assert type(source['source_id']) is str and source['source_id']
        assert type(source['path']) is str and Path(source['path']).is_absolute()
        assert type(source['bytes']) is int and source['bytes']>=0
        assert type(source['complete']) is bool
        streams[name]=Path(source['path']).read_bytes()
        exact(len(streams[name]),source['bytes'])
    return {'full_ref':deepcopy(full_ref),'full_bytes':raw,'full':full,
            'sources':{name:deepcopy(metadata[name]) for name in ('stdout','stderr')},
            'streams':streams}


class Commands:
    def __enter__(self):
        self.session = Installation().__enter__()
        try:
            self.session.prepare()
            self.binding = self.session.generated()
            _, _, response = self.session.call('session.ensure')
            self.identity = response['result']['session']['id']
            expected = json.loads((FIX.parent/'session/sessions.json').read_text())['initial-generated']
            expected['id'] = self.identity
            exact(response, envelope({'session':expected, 'created':True}))
            if not isinstance(self.identity, str) or not self.identity:
                raise DiagnosticError('Missing real generated Session identity')
            self.root = self.session.root / 'command-fixture'
            self.root.mkdir()
            self.output = self.session.path.parent / 'command-output'
            self.output.mkdir()
            self.store = self.session.path.parent / 'command-state/commands.sqlite'
            self.store.parent.mkdir()
            self.session.config['commands'] = load()['commands']
            self.session.extra_environment['PARENT_ONLY'] = 'must-not-enter-child'
            self.jobs, self.children, self.calls, self.admissions = {}, [], [], {}
            self.record = {'session_id':self.identity, 'calls':self.calls, 'jobs':{}, 'cleanup':[]}
            OBSERVATIONS.append(self.record)
            self.initial_session = self.session_rows()
            return self
        except BaseException:
            self.session.__exit__(*__import__('sys').exc_info())
            raise

    def __exit__(self, *args):
        try:
            for child in self.children:
                if child.poll() is None:
                    child.kill()
                child.communicate(timeout=2)
            for directory in self.jobs.values():
                (directory/'release').touch()
                (directory/'descendant-release').touch()
                if (directory/'descendant-ready').exists():
                    await_file(directory/'descendant-finished',seconds=2)
                for name in ('dispatch.jsonl','received.jsonl','emitted.jsonl','effects.jsonl'):
                    self.record['jobs'].setdefault(str(directory), {})[name] = lines(directory/name)
                for item in lines(directory/'dispatch.jsonl'):
                    # Only this fixture's independently audited child, never a global process scan.
                    try:
                        os.kill(item['pid'], signal.SIGTERM)
                    except ProcessLookupError:
                        continue
                    time.sleep(.01)
                    try:
                        os.kill(item['pid'], signal.SIGKILL)
                    except ProcessLookupError:
                        pass
            self.record['final_session'] = self.session_rows()
        finally:
            self.session.__exit__(*args)

    def session_rows(self):
        with sqlite3.connect(self.session.database) as db:
            return {'sessions':[list(row) for row in db.execute(
                'SELECT id,origin,status,revision FROM poise_sessions ORDER BY id')],
                'bindings':[list(row) for row in db.execute(
                'SELECT identity_key,session_id,inventory FROM poise_bindings ORDER BY identity_key')]}

    def request(self, id, mode='plain', *, execution=2000, cleanup=1000):
        directory = self.root / ('job-' + str(len(self.jobs)))
        directory.mkdir()
        self.jobs[id] = directory
        value = load()['request']
        value.update(command_id=id, argv=[PY,'-I','-S','-B',str(PEER),mode,str(directory),''],
                     cwd=str(directory), execution_budget_ms=execution, cleanup_budget_ms=cleanup)
        if mode.startswith('mcp:'):
            value['transport'] = 'mcp_stdio'
            value['mcp'] = load('oracles.json')['request_mcp']['input']['mcp']
        return value

    def packet(self, operation, value):
        packet = load()['packet']
        packet.update(operation=operation, input=deepcopy(value))
        return packet

    def launch(self, operation, value, *, file_limit=False, prepared=False):
        raw_config = self.session.path.read_bytes() if prepared else self.session.write_config()
        packet = self.packet(operation,value)
        argv = [PY,'-I','-S','-B',str(self.session.install/'bin/poise'),
                '--config',str(self.session.path)]
        if file_limit:
            argv = [PY,'-I','-S','-B',str(FIX/'limit.py'),*argv]
        raw = (json.dumps(packet, ensure_ascii=False)+'\n').encode()
        child = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, cwd=self.session.state.cwd, env=self.session.environment())
        self.children.append(child)
        child.stdin.write(raw)
        child.stdin.close()
        child.stdin = None
        record = {'argv':argv,'request':packet,'input_hex':raw.hex(),'config_hex':raw_config.hex(),
                  'environment':self.session.environment(),'pid':child.pid}
        self.calls.append(record)
        return child,record,raw_config

    def collect(self, process):
        child,record,raw_config = process
        try:
            out,err = child.communicate(timeout=15)
        except subprocess.TimeoutExpired as error:
            child.kill()
            out,err=child.communicate(timeout=2)
            record.update(stdout_hex=out.hex(),stderr_hex=err.hex(),exit_code=child.returncode)
            raise DiagnosticError('Actual command CLI exceeded test transport bound') from error
        record.update(stdout_hex=out.hex(),stderr_hex=err.hex(),exit_code=child.returncode)
        if err or not out.endswith(b'\n') or out.endswith(b'\n\n'):
            raise DiagnosticError('CLI stderr/framing mismatch; full bytes retained')
        if self.session.path.read_bytes()!=raw_config:
            raise DiagnosticError('Subject rewrote raw config bytes')
        try:
            value=json.loads(out)
        except (ValueError,UnicodeError) as error:
            raise DiagnosticError('CLI response is not complete UTF8 JSON') from error
        record['response']=value
        exact(set(value),{'schema','status','result','error','evidence'})
        exact(value['schema'],'stride-poise/v1')
        exact(value['evidence'],[])
        exact(child.returncode,{'ok':0,'unavailable':1,'rejected':2}[value['status']])
        return child.returncode,value

    def call(self, operation, value, **kwargs):
        return self.collect(self.launch(operation,value,**kwargs))

    def error(self, operation, value, code, details, action, *, status='rejected', **kwargs):
        actual_code,response=self.call(operation,value,**kwargs)
        exact(actual_code,{'rejected':2,'unavailable':1}[status])
        exact(response,envelope(error=problem(code,details,action),status=status))
        return response

    def absent(self,id):
        code,response=self.call('command.delete',{'command_id':id})
        exact(code,0)
        exact(response,envelope({'schema':'poise-command-delete/v1','command_id':id,'state':'absent'}))

    def count(self,id):
        return len(lines(self.jobs[id]/'dispatch.jsonl'))

    def start(self,request, **kwargs):
        code,response=self.call('command.request',request,**kwargs)
        return self.started(code,response,request)

    def started(self,code,response,request):
        """Check first framing/types now; retain final bytes for the independent scenario oracle."""
        if code==2 and response==load()['missing']:
            raise MissingCommand('Standalone command.request dispatch is missing')
        exact(set(response),set(load()['envelope']))
        exact(response['schema'],'stride-poise/v1');exact(response['evidence'],[])
        metadata=response['result']
        assert type(metadata) is dict, ('First admission result must be metadata',response)
        exact(set(metadata),set(load()['metadata']))
        exact(metadata['schema'],'poise-command/v1')
        exact(metadata['operation'],'request');exact(metadata['replayed'],False)
        exact(metadata['command_id'],request['command_id'])
        exact(metadata['transport'],request['transport'])
        assert type(metadata['deadline_exceeded']) is bool
        assert metadata['business_error'] is None or type(metadata['business_error']) is bool
        assert metadata['state'] in ('ADMITTED','RUNNING','TERMINAL','UNKNOWN')
        if metadata['state'] in ('ADMITTED','RUNNING'):
            expected=load()['metadata']
            expected.update(operation='request',command_id=request['command_id'],
                            transport=request['transport'],state=metadata['state'],termination=None)
            exact(code,0);exact(response,envelope(expected))
            snapshot=None
        else:
            termination=metadata['termination']
            if termination is not None:
                assert type(termination) is dict
                if termination.get('kind')=='exit':
                    exact(set(termination),{'kind','exit_code'})
                    assert type(termination['exit_code']) is int and 0<=termination['exit_code']<=255
                elif termination.get('kind')=='signal':
                    exact(set(termination),{'kind','signal'})
                    assert type(termination['signal']) is int and termination['signal']>0
                else: exact(termination,{'kind':'not_started'})
            error=metadata['error']
            if error is not None:
                assert type(error) is dict
                exact(set(error),{'code','details','action'})
                assert type(error['code']) is str and type(error['action']) is str
                assert type(error['details']) is dict
            failed=error is not None or metadata['state']=='UNKNOWN'
            exact(code,1 if failed else 0)
            exact(response,envelope(metadata,error,'unavailable' if failed else 'ok'))
            snapshot=retained_snapshot(response)
        if not hasattr(self,'admissions'):
            self.admissions={}
        self.admissions[request['command_id']]={'response':deepcopy(response),'snapshot':snapshot,
            'independent_scenario_checked':False}
        return response

    def complete(self,request, stdout=b'OUT\n', stderr=b'ERR\n', **facts):
        _,response=self.call('command.completion',{'command_id':request['command_id']})
        self.terminal(response,request,stdout,stderr,**facts)
        return response

    def terminal(self,response,request,stdout,stderr, *, exit_code=0, signal_number=None,
                 deadline=False,error=None,state='TERMINAL',business=None,mcp=None,
                 stdout_complete=True,stderr_complete=True,operation='completion',replayed=False,
                 not_started=False):
        actual=response['result']
        if not isinstance(actual,dict):
            raise AssertionError(('Missing command result',response))
        source_values={}
        expected_names=self.session.config['commands']['files']
        mode=self.session.config['commands']['file_mode']
        paths=[]
        for name,raw,complete in (('stdout',stdout,stdout_complete),('stderr',stderr,stderr_complete)):
            item=actual[name]
            exact(set(item),{'source_id','path','bytes','complete'})
            assert type(item['source_id']) is str and item['source_id']
            assert type(item['path']) is str and Path(item['path']).is_absolute()
            path=Path(item['path'])
            assert path.is_relative_to(self.output) and path.parent.parent==self.output
            exact(path.name,expected_names[name])
            exact(stat.S_IMODE(path.stat().st_mode),mode)
            exact(path.read_bytes(),raw)
            source_values[name]={'source_id':item['source_id'],'path':str(path),
                                 'bytes':len(raw),'complete':complete}
            paths.append(path)
        assert paths[0].parent==paths[1].parent
        assert source_values['stdout']['source_id']!=source_values['stderr']['source_id']
        full_ref=actual['full_result_ref']
        exact(set(full_ref),{'path','bytes'})
        full_path=Path(full_ref['path'])
        exact(full_path.parent,paths[0].parent)
        exact(full_path.name,expected_names['result'])
        exact(stat.S_IMODE(full_path.stat().st_mode),mode)
        full_bytes=full_path.read_bytes()
        expected=load()['metadata']
        termination=({'kind':'signal','signal':signal_number} if signal_number is not None
                     else {'kind':'exit','exit_code':exit_code})
        if not_started:
            termination={'kind':'not_started'}
        expected.update(command_id=request['command_id'],transport=request['transport'],state=state,
                        termination=termination,deadline_exceeded=deadline,business_error=business,
                        error=error,operation=operation,replayed=replayed,**source_values,
                        full_result_ref={'path':str(full_path),'bytes':len(full_bytes)})
        full=load()['full_result']
        full.update(command_id=request['command_id'],transport=request['transport'],state=state,
                    termination=termination,deadline_exceeded=deadline,business_error=business,
                    error=error,mcp_response=mcp,**source_values)
        exact(json.loads(full_bytes),full)
        exact(response,envelope(expected,error,'unavailable' if error or state=='UNKNOWN' else 'ok'))
        admission=self.admissions.get(request['command_id'])
        if admission is not None:
            first=admission['response']
            if first['result']['state'] in ('TERMINAL','UNKNOWN'):
                first_expected=deepcopy(expected)
                first_expected.update(operation='request',replayed=False)
                exact(first,envelope(first_expected,error,'unavailable' if error or state=='UNKNOWN' else 'ok'))
                # Compare the bytes observed at FIRST response with independent literals,
                # not merely the later receipt generated by the same producer.
                snapshot=admission['snapshot']
                exact(snapshot['full'],full)
                exact(snapshot['full_bytes'],full_bytes)
                exact(snapshot['streams'],{'stdout':stdout,'stderr':stderr})
            admission['independent_scenario_checked']=True
        exact(self.count(request['command_id']),0 if not_started else 1)
        exact(self.session_rows(),self.initial_session)
        self.record.setdefault('full_results',[]).append({'metadata':response,'full':full,
            'full_hex':full_bytes.hex(),'stdout_hex':stdout.hex(),'stderr_hex':stderr.hex()})
        return expected

    def peer_facts(self,request):
        directory=self.jobs[request['command_id']]
        received=lines(directory/'received.jsonl')
        emitted=lines(directory/'emitted.jsonl')
        exact(received[0]['method'],'initialize')
        exact(received[0]['jsonrpc'],'2.0')
        expected=request['mcp']
        exact(received[0]['params'],{'protocolVersion':expected['protocol_version'],
                                    'capabilities':{},'clientInfo':expected['client_info']})
        ids=[]
        for item in received:
            if 'id' in item:
                assert type(item['id']) in (str,int) and item['id'] is not None
                assert not any(type(item['id']) is type(id) and item['id']==id for id in ids)
                ids.append(item['id'])
        for item in received[2:]:
            exact(set(item),{'jsonrpc','id','method','params'})
            exact(item['jsonrpc'],'2.0')
            exact(item['method'],'tools/call')
            exact(item['params'],{'name':expected['tool_name'],'arguments':expected['arguments']})
        if len(received)>1:
            choices=[{'jsonrpc':'2.0','method':'notifications/initialized'},
                     {'jsonrpc':'2.0','method':'notifications/initialized','params':{}}]
            assert any(received[1]==choice for choice in choices)
            exact(received[1],choices[1] if 'params' in received[1] else choices[0])
            exact(len(received),3)
            exact(lines(directory/'effects.jsonl'),[{'name':expected['tool_name'],'arguments':expected['arguments']}])
        return received,emitted,(directory/'emitted.bin').read_bytes()
