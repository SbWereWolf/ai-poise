"""Sensitivity of test consumers only; doubles never stand in for the product."""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile

from command_checks import replay
from command_support import Commands, DiagnosticError, envelope, exact, load


def must_reject(id, operation, observations):
    try:
        operation()
    except AssertionError as error:
        observations.append({'id':id,'status':'rejected_wrong_contract','reason':str(error)})
    else:
        raise DiagnosticError('Test consumer accepted defective boundary: '+id)


def sensitivity():
    observations=[]
    request={'command_id':'control','transport':'command'}

    def consumer_boundary():
        consumer=Commands.__new__(Commands)
        consumer.selected_session='session-A'

        def status(operation):
            exact(operation,'session.status')
            return 0,{},envelope({'session':{'id':consumer.selected_session}})

        consumer.session=SimpleNamespace(config={'commands':load()['commands']},call=status)
        consumer.count=lambda _id:1
        consumer.initial_session={}
        consumer.session_rows=lambda:{}
        consumer.record={}
        consumer.admissions={}
        consumer.verified_results={}
        return consumer

    for vector in load('consumer-cases.json')['first_response_negatives']:
        consumer=consumer_boundary()
        consumer.call=lambda *_args,**_kw:(vector['exit'],deepcopy(vector['response']))
        must_reject(vector['id'],lambda:consumer.start(request),observations)
    for state in ('ADMITTED','RUNNING'):
        consumer=consumer_boundary()
        body=load()['metadata']
        body.update(command_id='control',operation='request',state=state,termination=None)
        consumer.call=lambda *_args,**_kw:(0,envelope(deepcopy(body)))
        exact(consumer.start(request),envelope(body))
        observations.append({'id':'valid-first-'+state,'status':'passed'})
    with tempfile.TemporaryDirectory(prefix='command-consumer-') as name:
        root=Path(name)
        files=root/'generated';files.mkdir()
        out,err,receipt=(files/n for n in ('stdout.bin','stderr.bin','result.json'))
        out.write_bytes(b'OUT\n');err.write_bytes(b'ERR\n')
        sources={key:{'source_id':key,'path':str(path),'bytes':4,'complete':True}
                 for key,path in (('stdout',out),('stderr',err))}
        full=load()['full_result'];full.update(command_id='control',**sources)
        raw=(json.dumps(full,ensure_ascii=False)+'\n').encode()
        receipt.write_bytes(raw)
        for path in (out,err,receipt):path.chmod(0o600)
        metadata=load()['metadata'];metadata.update(command_id='control',**sources,
            full_result_ref={'path':str(receipt),'bytes':len(raw)})
        terminal=envelope(metadata)

        def boundary():
            consumer=consumer_boundary()
            consumer.output=root
            return consumer

        # A valid already-final first response must pass the complete independent oracle.
        consumer=boundary()
        first=deepcopy(terminal);first['result']['operation']='request'
        consumer.started(0,first,request)
        consumer.terminal(terminal,request,b'OUT\n',b'ERR\n')
        valid_registry=deepcopy(consumer.verified_results)
        observations.append({'id':'valid-final-first-snapshot','status':'passed'})
        # Metadata AND receipt can agree on a wrong first outcome. The independent
        # later scenario must still reject those first bytes, even after file repair.
        consumer=boundary()
        wrong=deepcopy(full);wrong['termination']['exit_code']=7
        changed=(json.dumps(wrong,ensure_ascii=False)+'\n').encode()
        exact(len(changed),len(raw))
        receipt.write_bytes(changed)
        bad_first=deepcopy(first);bad_first['result']['termination']['exit_code']=7
        consumer.started(0,bad_first,request)
        receipt.write_bytes(raw)
        must_reject('first-final-independent-facts',
                    lambda:consumer.terminal(terminal,request,b'OUT\n',b'ERR\n'),observations)

        class ReplayBoundary:
            def __init__(self, inject=None, whitespace=False, initial=None, verified=None):
                self.inject,self.whitespace=inject,whitespace
                self.calls=[]
                self.template=deepcopy(terminal if initial is None else initial)
                self.verified_results=deepcopy(valid_registry if verified is None else verified)

            def scope_key(self,id):
                return 'session-A',id

            def call(self,operation,_value):
                self.calls.append(operation)
                value=deepcopy(self.template)
                if operation=='command.request':
                    value['result'].update(operation='request',replayed=True)
                if operation==self.inject:
                    receipt.write_bytes(raw[:-1]+b' ' if self.whitespace else changed)
                return 0,value

            def count(self,_id):
                return 1

        for operation in ('command.request','command.completion'):
            for whitespace in (False,True):
                receipt.write_bytes(raw)
                double=ReplayBoundary(operation,whitespace)
                must_reject('replay-'+operation+('-bytes' if whitespace else '-facts'),
                            lambda:replay(double,request,terminal),observations)
                observations[-1].update(calls=double.calls,
                    retained_bytes_before=len(raw),retained_bytes_after=receipt.stat().st_size)
        receipt.write_bytes(raw)
        good=ReplayBoundary()
        replay(good,request,terminal)
        exact(good.calls,['command.request','command.completion'])
        observations.append({'id':'valid-complete-replay','status':'passed'})

        # Preserve an independently checked opaque MCP body BEFORE a conflicting
        # input/refusal. A fresh read must not silently become a new expected body.
        mcp=load()['peer_result'];mcp['id']='call-control'
        mcp_full=deepcopy(full)
        mcp_full.update(transport='mcp_stdio',business_error=False,mcp_response=mcp)
        mcp_raw=(json.dumps(mcp_full,ensure_ascii=False)+'\n').encode()
        receipt.write_bytes(mcp_raw)
        mcp_meta=deepcopy(metadata)
        mcp_meta.update(transport='mcp_stdio',business_error=False,
                        full_result_ref={'path':str(receipt),'bytes':len(mcp_raw)})
        mcp_terminal=envelope(mcp_meta)
        mcp_request={'command_id':'control','transport':'mcp_stdio'}
        consumer=boundary()
        consumer.terminal(mcp_terminal,mcp_request,b'OUT\n',b'ERR\n',mcp=mcp,business=False)
        frozen=deepcopy(consumer.verified_results)
        corrupt=deepcopy(mcp_full)
        corrupt['mcp_response']['result']['vendor']['code']=8
        corrupt_raw=(json.dumps(corrupt,ensure_ascii=False)+'\n').encode()
        exact(len(corrupt_raw),len(mcp_raw))
        receipt.write_bytes(corrupt_raw)
        double=ReplayBoundary(initial=mcp_terminal,verified=frozen)
        must_reject('opaque-MCP-mutated-before-replay-entry',
                    lambda:replay(double,mcp_request,mcp_terminal),observations)
        exact(double.calls,[])
        observations[-1].update(calls=double.calls,same_size=len(mcp_raw))
        receipt.write_bytes(mcp_raw)
        replay(ReplayBoundary(initial=mcp_terminal,verified=frozen),mcp_request,mcp_terminal)
        observations.append({'id':'valid-retained-opaque-MCP','status':'passed'})

        # Both first responses are legitimately TERMINAL for the same opaque ID.
        # A's accepted first receipt must survive B's independent execution.
        receipt.write_bytes(raw)
        second=root/'generated-B';second.mkdir()
        b_sources={}
        for key,value in (('stdout',b'OUT\n'),('stderr',b'ERR\n')):
            path=second/(key+'.bin');path.write_bytes(value);path.chmod(0o600)
            b_sources[key]={'source_id':'B-'+key,'path':str(path),'bytes':4,'complete':True}
        b_full=deepcopy(full);b_full.update(**b_sources)
        b_raw=(json.dumps(b_full,ensure_ascii=False)+'\n').encode()
        b_receipt=second/'result.json';b_receipt.write_bytes(b_raw);b_receipt.chmod(0o600)
        b_meta=deepcopy(metadata);b_meta.update(**b_sources,
            full_result_ref={'path':str(b_receipt),'bytes':len(b_raw)})
        b_terminal=envelope(b_meta)
        consumer=boundary()
        consumer.started(0,first,request)
        consumer.terminal(terminal,request,b'OUT\n',b'ERR\n')
        consumer.selected_session='session-B'
        b_first=deepcopy(b_terminal);b_first['result']['operation']='request'
        consumer.started(0,b_first,request)
        consumer.terminal(b_terminal,request,b'OUT\n',b'ERR\n')
        consumer.selected_session='session-A'
        consumer.terminal(terminal,request,b'OUT\n',b'ERR\n')
        exact(sorted(consumer.admissions),[('session-A','control'),('session-B','control')])
        assert all(item['independent_scenario_checked'] for item in consumer.admissions.values())
        observations.append({'id':'two-Sessions-same-ID-both-first-TERMINAL-return-A',
                             'status':'passed','scopes':list(consumer.admissions)})
    return observations
