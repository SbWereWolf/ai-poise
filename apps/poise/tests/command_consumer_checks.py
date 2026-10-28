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
    for vector in load('consumer-cases.json')['first_response_negatives']:
        consumer=Commands.__new__(Commands)
        consumer.call=lambda *_args,**_kw:(vector['exit'],deepcopy(vector['response']))
        must_reject(vector['id'],lambda:consumer.start(request),observations)
    for state in ('ADMITTED','RUNNING'):
        consumer=Commands.__new__(Commands)
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
            consumer=Commands.__new__(Commands)
            consumer.output=root
            consumer.session=SimpleNamespace(config={'commands':load()['commands']})
            consumer.count=lambda _id:1
            consumer.initial_session={}
            consumer.session_rows=lambda:{}
            consumer.record={}
            consumer.admissions={}
            return consumer

        # A valid already-final first response must pass the complete independent oracle.
        consumer=boundary()
        first=deepcopy(terminal);first['result']['operation']='request'
        consumer.started(0,first,request)
        consumer.terminal(terminal,request,b'OUT\n',b'ERR\n')
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
            def __init__(self, inject=None, whitespace=False):
                self.inject,self.whitespace=inject,whitespace
                self.calls=[]

            def call(self,operation,_value):
                self.calls.append(operation)
                value=deepcopy(terminal)
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
    return observations
