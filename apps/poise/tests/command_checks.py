"""Accepted C01-C33 checks through real CLI, storage, processes and stdio peers."""
from copy import deepcopy
import json
import os
from pathlib import Path
import sqlite3
import time

from command_support import (Commands, DiagnosticError, MissingCommand, await_file,
                             FIX, envelope, exact, lines, load, problem, retained_snapshot)


CASES = []


def case(id):
    CASES.append(id)


def active(app,response,request, *, operation='request',replayed=False):
    choices=[]
    for state in ('ADMITTED','RUNNING'):
        expected=load()['metadata']
        expected.update(command_id=request['command_id'],transport=request['transport'],
                        operation=operation,state=state,termination=None,replayed=replayed)
        choices.append(envelope(expected))
    for expected in choices:
        try:
            exact(response,expected)
            return
        except AssertionError:
            pass
    raise AssertionError(('Whole active snapshot differs',response,choices))


def dispatch_oracle(app,request):
    audit=lines(app.jobs[request['command_id']]/'dispatch.jsonl')
    exact(len(audit),1)
    expected={'pid':audit[0]['pid'],'argv':request['argv'][5:],
              'cwd':request['cwd'],'env':request['env']}
    assert type(expected['pid']) is int and expected['pid']>0
    exact(audit,[expected])


def replay(app,request,terminal, *, baseline=None):
    before=deepcopy(app.verified_results[app.scope_key(request['command_id'])]
                    if baseline is None else baseline)
    exact(retained_snapshot(terminal),before)
    _,repeated=app.call('command.request',request)
    expected=deepcopy(terminal)
    expected['result'].update(operation='request',replayed=True)
    exact(repeated,expected)
    exact(retained_snapshot(repeated),before)
    exact(retained_snapshot(terminal),before)
    _,read=app.call('command.completion',{'command_id':request['command_id']})
    exact(read,terminal)
    exact(retained_snapshot(read),before)
    exact(app.count(request['command_id']),1)


def delivery():
    try:
        with Commands() as app:
            request=app.request('delivery-1')
            before=app.session_rows()
            code,response=app.call('command.request',request)
            if code==2 and response==load()['missing']:
                exact(response,load()['missing'])
                exact(app.session_rows(),before)
                exact(list(app.output.iterdir()),[])
                exact(app.store.exists(),False)
                exact(app.count('delivery-1'),0)
                app.record['qualified_missing_dispatch']=True
                missing=True
            else:
                missing=False
                app.started(code,response,request)
                app.complete(request)
                dispatch_oracle(app,request)
    except Exception as error:
        if isinstance(error,MissingCommand):
            raise
        raise DiagnosticError('Delivery environment/consumer differs from qualified missing operation') from error
    if missing:
        raise MissingCommand('Missing command.request after valid copied app/config/own Session')


def parallel(cold=False):
    with Commands() as app:
        if not cold:
            warm=app.request('warm')
            app.start(warm)
            app.complete(warm)
            app.absent('warm')
        else:
            exact(app.store.exists(),False)
        request=app.request('parallel','gate')
        app.session.write_config()
        first=app.launch('command.request',request,prepared=True)
        second=app.launch('command.request',request,prepared=True)
        answers=[app.collect(first)[1],app.collect(second)[1]]
        exact(sorted(item['result']['replayed'] for item in answers),[False,True])
        for answer in answers:
            active(app,answer,request,replayed=answer['result']['replayed'])
        await_file(app.jobs['parallel']/'ready')
        exact(app.count('parallel'),1)
        (app.jobs['parallel']/'release').touch()
        final=app.complete(request,b'OUT\n',b'')
        replay(app,request,final)


def execution():
    case('C01')
    with Commands() as app:
        request=app.request('ordinary')
        app.start(request)
        final=app.complete(request)
        dispatch_oracle(app,request)
        case('C03')
        replay(app,request,final)
    case('C02')
    with Commands() as app:
        request=app.request('async','gate')
        process=app.launch('command.request',request)
        _,response=app.collect(process)
        exact(process[0].poll(),0)  # The actual admission CLI has exited.
        active(app,response,request)
        await_file(app.jobs['async']/'ready')
        (app.jobs['async']/'release').touch()
        app.complete(request,b'OUT\n',b'')
    case('C04')
    parallel()
    case('C05')
    with Commands() as app:
        first=app.request('long','gate')
        active(app,app.start(first),first)
        await_file(app.jobs['long']/'ready')
        second=app.request('short')
        app.start(second)
        app.complete(second)
        exact((app.jobs['long']/'release').exists(),False)
        (app.jobs['long']/'release').touch()
        app.complete(first,b'OUT\n',b'')
    case('C28')
    parallel(cold=True)


def outcomes():
    case('C06')
    with Commands() as app:
        request=app.request('binary','binary')
        app.start(request)
        app.complete(request,bytes.fromhex('00ff4f5554d0960a'),bytes.fromhex('45525200800a'),exit_code=7)
    for id,mode,facts in [('C07','term',{'exit_code':7}),('C08','ignore',{'signal_number':9})]:
        case(id)
        with Commands() as app:
            request=app.request(mode,mode,execution=1000)
            app.start(request)
            await_file(app.jobs[mode]/'ready')
            app.complete(request,b'READY\n',b'',deadline=True,**facts)
    case('C09')
    with Commands() as app:
        request=app.request('missing')
        request['argv'][0]=str(app.root/'absent-executable')
        app.start(request)
        result=app.complete(request,b'',b'',not_started=True,error=problem(
            'command_start_failed',{'path':request['argv'][0],'errno':2},'configure:command'))
        _,again=app.call('command.request',request)
        expected=deepcopy(result);expected['result'].update(operation='request',replayed=True)
        exact(again,expected);exact(app.count('missing'),0)
    case('C10')
    with Commands() as app:
        request=app.request('large','large')
        app.start(request)
        app.complete(request,'Ж🙂\n'.encode()*100000,b'err\n'*25000)
        assert all(len(bytes.fromhex(c['stdout_hex']))<65536 for c in app.calls)
    case('C21')
    with Commands() as app:
        request=app.request('drain-1','pipe',cleanup=500)
        before=time.monotonic()
        app.start(request)
        await_file(app.jobs['drain-1']/'descendant-ready')
        app.complete(request,b'READY\n',b'',stdout_complete=False,error=problem(
            'command_capture_failed',{'stream':'stdout','errno':None},'inspect:command'))
        assert time.monotonic()-before<5
        (app.jobs['drain-1']/'descendant-release').touch()
    case('C24')
    with Commands() as app:
        warm=app.request('warm')
        app.start(warm)
        app.complete(warm)
        app.absent('warm')
        request=app.request('capture-1','capture')
        app.start(request,file_limit=True)
        app.complete(request,b'x'*1048576,b'',stdout_complete=False,error=problem(
            'command_capture_failed',{'stream':'stdout','errno':27},'inspect:command'))


def mcp_case(variant,id, *, limit=None,count=None):
    with Commands() as app:
        request=app.request(id,'mcp:'+variant)
        if limit is not None:
            app.session.config['commands']['limits']['mcp_max_message_bytes']=limit
        if count is not None:
            app.session.config['commands']['limits']['mcp_max_messages']=count
        app.start(request)
        _,response=app.call('command.completion',{'command_id':id})
        received,emitted,raw=app.peer_facts(request)
        business=False
        error=None
        state='TERMINAL'
        expected_response=load()['peer_result']
        if variant=='version':
            exact(len(received),1)
            expected_response=None
            business=None
            error=problem('command_protocol_failed',{'phase':'initialize','reason':'protocol_version'},'configure:command')
        else:
            expected_response['id']=received[2]['id']
            if variant=='business':
                business=True
                expected_response['result']['isError']=True
            if variant=='rpc-error':
                business=True
                expected_response=load()['rpc_error']
                expected_response['id']=received[2]['id']
                error=problem('mcp_response_error',{'code':-32001},'inspect:command')
            if variant=='large':
                expected_response['result']['content'][0]['text']=(FIX/'large-mcp-text.txt').read_text()
                assert 65536<len(raw)<1048576
            if variant in ('eof','wrong-id','bytes-excess','count-excess'):
                business=None
                expected_response=None
                if variant=='eof':
                    state='UNKNOWN'
                    error=problem('command_observation_lost',{'phase':'mcp_response'},'inspect:command')
                else:
                    reason='invalid_response' if variant=='wrong-id' else 'message_limit'
                    error=problem('command_protocol_failed',{'phase':'tools_call','reason':reason},'configure:command')
        # Actual peer response is independently compared to literal data, not accepted as its own oracle.
        if expected_response is not None:
            exact(emitted[-1],expected_response)
        if variant=='wrong-id':
            wrong=load()['peer_result']
            wrong['id']='unmatched-2' if received[2]['id']=='unmatched-1' else 'unmatched-1'
            exact(emitted[-1],wrong)
            assert type(received[2]['id']) is not str or received[2]['id']!=wrong['id']
        if variant in ('notification','count-ok','count-excess'):
            exact(emitted[1],load()['notification'])
            exact(len(emitted),4 if variant=='count-excess' else 3)
        if variant.startswith('bytes'):
            frames=raw.splitlines()
            exact(len(frames[1]),1024 if variant=='bytes-ok' else 1025)
        app.terminal(response,request,raw,b'',mcp=expected_response,business=business,
                     state=state,error=error,exit_code=7 if variant=='exit7' else 0)
        replay(app,request,response)


def mcp():
    for id,variant in [('C11','success'),('C12','business'),('C13','rpc-error'),
                       ('C14','eof'),('C15','version')]:
        case(id)
        mcp_case(variant,'mcp-'+variant)
    case('C31')
    mcp_case('wrong-id','mcp-wrong-id-1')
    mcp_case('notification','mcp-notification-1')
    case('C32')
    mcp_case('bytes-ok','mcp-bytes-ok',limit=1024)
    mcp_case('bytes-excess','mcp-bytes-excess',limit=1024)
    mcp_case('count-ok','mcp-count-ok',count=3)
    mcp_case('count-excess','mcp-count-excess',count=3)
    case('C33')
    mcp_case('large','mcp-large')
    mcp_case('exit7','mcp-exit7')


def finished(app,id='ordinary'):
    request=app.request(id)
    app.start(request)
    return request,app.complete(request)


def parser_boundaries():
    vectors=load('oracles.json')['admission_boundary_vectors']
    for vector in vectors:
        with Commands() as app:
            value=app.request('boundary')
            operation='command.request'
            config=app.session.config
            id=vector['id']
            retained=None
            if id=='B01': config.pop('commands')
            elif id=='B02': value.pop('command_id')
            elif id=='B03': value['extra']=True
            elif id=='B04': value['schema']='poise-command-request/v999'
            elif id=='B05': value['schema']=1
            elif id=='B06': value['execution_budget_ms']=True
            elif id=='B07': value['command_id']='a'*129
            elif id=='B08': value['mcp']={}
            elif id=='B09': value['execution_budget_ms']=5000
            elif id=='B10': value['cleanup_budget_ms']=100
            elif id=='B11': config['commands']['limits']['transport_budget_ms']=5000
            elif id=='B12':
                app.session.generated('new-missing-caller.json')
            elif id=='B13':
                value,original=finished(app,'exit7-1')
                retained=(deepcopy(value),original)
                value['argv'].append('changed')
            elif id=='B14': operation,value='command.completion',{'command_id':'absent-1'}
            elif id=='B15': operation='command.update'
            elif id=='B16': config['commands']=None
            elif id=='B17': value['argv']='not-an-array'
            elif id=='B18': value['argv'][1]=1
            elif id=='B19': value['env']['ENV_KEY']=1
            elif id=='B20': value['env']['BAD=KEY']='value'
            elif id in ('B21','B22'):
                value=app.request('boundary-mcp','mcp:success')
                if id=='B21': value['mcp']['arguments']=[]
                else: value['mcp']['client_info']['extra']=True
            elif id=='B23': value['command_id']='Ж'*65
            elif id=='B24':
                value=app.request('typed-1','mcp:success')
                value['mcp']['arguments']={'flag':True}
                app.start(value)
                _,complete=app.call('command.completion',{'command_id':'typed-1'})
                _,_,raw=app.peer_facts(value)
                expected=load()['peer_result'];expected['id']=lines(app.jobs['typed-1']/'received.jsonl')[2]['id']
                app.terminal(complete,value,raw,b'',business=False,mcp=expected)
                retained=(deepcopy(value),deepcopy(complete))
                value['mcp']['arguments']['flag']=1
            else: raise DiagnosticError('Unimplemented accepted boundary '+id)
            before=app.session_rows()
            outputs={str(p):p.read_bytes() for p in app.output.rglob('*') if p.is_file()}
            counts={name:app.count(name) for name in app.jobs}
            code,response=app.call(operation,value)
            exact(code,vector['expected_exit'])
            exact(response,vector['expected_response'])
            exact(app.session_rows(),before)
            exact({str(p):p.read_bytes() for p in app.output.rglob('*') if p.is_file()},outputs)
            exact({name:app.count(name) for name in app.jobs},counts)
            if retained is not None:
                replay(app,*retained)
            if id not in ('B13','B24'):
                exact(app.store.exists(),False)


def lifecycle():
    case('C16')
    with Commands() as app:
        a,ra=finished(app,'same')
        first_caller=deepcopy(app.session.config['caller'])
        first_job=app.jobs['same']
        first_identity=app.identity
        app.session.generated('second-caller.json')
        _,_,ensured=app.session.call('session.ensure')
        second_identity=ensured['result']['session']['id']
        assert second_identity!=first_identity
        expected=json.loads((Path(__file__).parent/'fixtures/session/sessions.json').read_text())['initial-generated']
        expected['id']=second_identity
        exact(ensured,envelope({'session':expected,'created':True}))
        app.initial_session=app.session_rows()
        b,rb=finished(app,'same')
        assert ra['result']['stdout']['path']!=rb['result']['stdout']['path']
        app.absent('same')  # Both Sessions share the SAME Session/command stores and output root.
        exact(Path(ra['result']['stdout']['path']).read_bytes(),b'OUT\n')
        app.session.config['caller']=first_caller
        app.jobs['same']=first_job
        exact(app.complete(a),ra)
        app.absent('same')
    case('C17')
    with Commands() as app:
        request,result=finished(app)
        foreign=app.output/'foreign.txt';foreign.write_bytes(b'foreign\n')
        paths=[Path(result['result'][n]['path']) for n in ('stdout','stderr','full_result_ref')]
        app.absent(request['command_id']);app.absent(request['command_id'])
        exact([p.exists() for p in paths],[False,False,False])
        exact(foreign.read_bytes(),b'foreign\n')
        app.error('command.completion',{'command_id':request['command_id']},'command_not_found',
                  {'command_id':request['command_id']},'use:command',status='unavailable')
    case('C18')
    with Commands() as app:
        app.session.config['unknown']={'path':'$ROOT','cwd':'literal','base':{'opaque':True}}
        app.session.config['commands']['opaque']=['retain',None,True]
        finished(app)
        for unused in ('absent',None,{'malformed':True}):
            if unused=='absent': app.session.config.pop('commands',None)
            else: app.session.config['commands']=unused
            _,_,value=app.session.call('session.status')
            exact(value['result']['session']['id'],app.identity)
    case('C19')
    parser_boundaries()
    case('C20')
    with Commands() as app:
        request=app.request('$ROOT/path/base/cwd')
        request['env']['OPAQUE']='$ROOT'
        app.start(request);result=app.complete(request);dispatch_oracle(app,request)
        replay(app,request,result)
        app.error('command.update',{},'invalid_request',{'path':'/operation'},'correct:request')
    case('C22')
    for invalid in ('version','bytes'):
        with Commands() as app:
            if invalid=='version':
                with sqlite3.connect(app.store) as db: db.execute('PRAGMA user_version=999')
            else: app.store.write_bytes(b'not-a-sqlite-database\n')
            before=app.store.read_bytes()
            value=app.request('incompatible')
            app.error('command.request',value,'command_store_incompatible',
                      {'version':999 if invalid=='version' else None},'select:command-store',status='unavailable')
            exact(app.store.read_bytes(),before);exact(app.count('incompatible'),0)
            exact(list(app.output.iterdir()),[])
    case('C23')
    with Commands() as app:
        finished(app,'warm')
        app.session.config['commands']['limits']['sqlite_timeout_seconds']=0
        value=app.request('busy')
        with sqlite3.connect(app.store) as holder:
            holder.execute('BEGIN IMMEDIATE')
            app.error('command.request',value,'command_store_busy',{},'retry:command',status='unavailable')
            exact(app.count('busy'),0)
            holder.rollback()
        app.start(value);app.complete(value)
    with Commands() as app:
        value=app.request('store-io')
        app.store.parent.chmod(0o500)
        try:
            app.error('command.request',value,'command_store_io',{'errno':None},'configure:command-store',status='unavailable')
            exact(app.count('store-io'),0)
            exact(app.store.exists(),False);exact(list(app.output.iterdir()),[])
        finally: app.store.parent.chmod(0o700)
        app.start(value);app.complete(value)
    case('C25')
    with Commands() as app:
        limits=app.session.config['commands']['limits']
        limits.update(completion_budget_ms=3000,transport_budget_ms=4000)
        value=app.request('wait-1','gate',cleanup=500)
        active(app,app.start(value),value)
        await_file(app.jobs['wait-1']/'ready')
        limits.update(completion_budget_ms=100,transport_budget_ms=1000)
        try:
            code,response=app.call('command.completion',{'command_id':'wait-1'})
            expected=load('oracles.json')['wait_expired']
            exact(code,expected['expected_exit']);exact(response,expected['expected_response'])
            exact(app.count('wait-1'),1)
        finally:
            (app.jobs['wait-1']/'release').touch()
            limits.update(completion_budget_ms=3000,transport_budget_ms=4000)
        final=app.complete(value,b'OUT\n',b'');replay(app,value,final)
    case('C26')
    with Commands() as app:
        value,response=finished(app,'delete-retry-1')
        out=Path(response['result']['stdout']['path']);err=Path(response['result']['stderr']['path'])
        full=Path(response['result']['full_result_ref']['path'])
        out.unlink();err.parent.chmod(0o500)
        try:
            app.error('command.delete',{'command_id':'delete-retry-1'},'command_delete_failed',
                      {'command_id':'delete-retry-1','path':str(err),'errno':13},'inspect:command',status='unavailable')
            exact(err.read_bytes(),b'ERR\n');assert full.is_file()
            app.error('command.delete',{'command_id':'delete-retry-1'},'command_delete_failed',
                      {'command_id':'delete-retry-1','path':str(err),'errno':13},'inspect:command',status='unavailable')
        finally: err.parent.chmod(0o700)
        app.absent('delete-retry-1');app.absent('delete-retry-1')
        exact([p.exists() for p in (out,err,full)],[False,False,False])
    case('C27')
    with Commands() as app:
        value,response=finished(app,'closed-old')
        before=app.session_rows();binding=app.binding.read_bytes()
        with sqlite3.connect(app.session.database) as db:
            cursor=db.execute('UPDATE poise_sessions SET status=? WHERE id=?',('closed',app.identity))
            exact(cursor.rowcount,1)
        closed=deepcopy(before)
        next(row for row in closed['sessions'] if row[0]==app.identity)[2]='closed'
        exact(app.session_rows(),closed);exact(app.binding.read_bytes(),binding)
        for operation in ('session.status','session.ensure'):
            _,_,observed=app.session.call(operation)
            expected=json.loads((Path(__file__).parent/'fixtures/session/sessions.json').read_text())['initial-generated']
            expected.update(id=app.identity,status='closed')
            result={'session':expected}
            if operation=='session.ensure': result['created']=False
            exact(observed,envelope(result))
        other=app.request('closed-new')
        app.error('command.request',other,'session_closed',{'status':'closed'},'select:session')
        exact(app.count('closed-new'),0)
        app.initial_session=closed
        exact(app.complete(value),response)
        app.absent('closed-old');app.absent('closed-old')
        exact(app.session_rows(),closed);exact(app.binding.read_bytes(),binding)
    case('C29')
    with Commands() as app:
        value=app.request('typed-1','mcp:success');value['mcp']['arguments']={'flag':True}
        app.start(value)
        _,response=app.call('command.completion',{'command_id':'typed-1'})
        received,_,raw=app.peer_facts(value);expected=load()['peer_result'];expected['id']=received[2]['id']
        app.terminal(response,value,raw,b'',mcp=expected,business=False)
        replay(app,value,response)
        baseline=deepcopy(app.verified_results[app.scope_key('typed-1')])
        changed=deepcopy(value);changed['mcp']['arguments']['flag']=1
        app.error('command.request',changed,'command_reuse_conflict',{'command_id':'typed-1'},'use:new-command')
        exact(retained_snapshot(response),baseline)
        exact(app.count('typed-1'),1)
        replay(app,value,response,baseline=baseline)
    with Commands() as app:
        accepted='Ж'*64
        finished(app,accepted)
        rejected=app.request('Ж'*65)
        app.error('command.request',rejected,'invalid_request',{'path':'/input/command_id'},'correct:request')
        exact(app.count('Ж'*65),0)
    case('C30')
    with Commands() as app:
        options=load('oracles.json')['alternate_files']
        app.session.config['commands']['files']={k:options[k] for k in ('stdout','stderr','result')}
        app.session.config['commands']['file_mode']=options['file_mode']
        previous=os.umask(options['caller_umask'])
        try: finished(app,'mode')
        finally: os.umask(previous)
        names={p.name for p in app.output.rglob('*') if p.is_file()}
        exact(names,{'out.data','err.data','receipt.data'})


GROUPS=[('execution',execution),('outcomes',outcomes),('mcp',mcp),('lifecycle',lifecycle)]
