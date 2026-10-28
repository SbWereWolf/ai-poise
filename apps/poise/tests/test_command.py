"""Qualified missing-operation RED followed by the same permanent execution checks."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import traceback

from command_checks import CASES, GROUPS, delivery
from command_support import (Commands, DiagnosticError, MissingCommand, OBSERVATIONS,
                             FIX, PEER, PY, exact, lines, load)
from generic_checks import APP, LOG
from session_support import CALLS
from state_sql import SQL_LOG


CONTROLS=[]


def prerequisites():
    if sys.version_info[:2]!=(3,13) or sys.platform!='linux' or os.geteuid()==0:
        raise DiagnosticError('Select CPython3.13 on non-root Linux for POSIX fixtures')
    for actual,expected in ((True,1),(1,True),(1.0,1)):
        try: exact(actual,expected)
        except AssertionError: pass
        else: raise DiagnosticError('Comparator ignored exact JSON scalar types')
    # Qualify actual OS execution, audit/environment and separately captured binary streams.
    with tempfile.TemporaryDirectory(prefix='command-control-') as name:
        directory=Path(name)
        child=subprocess.run([PY,'-I','-S','-B',str(PEER),'plain',name,''],
            cwd=name,env={'LC_ALL':'C.UTF-8','CONTROL':'$ROOT/path/base/cwd'},capture_output=True,timeout=5)
        CONTROLS.append({'id':'actual-cli','argv':child.args,'exit':child.returncode,
                         'stdout_hex':child.stdout.hex(),'stderr_hex':child.stderr.hex()})
        exact(child.returncode,0);exact(child.stdout,b'OUT\n');exact(child.stderr,b'ERR\n')
        audit=lines(directory/'dispatch.jsonl')
        CONTROLS[-1]['audit']=audit
        exact(audit[0]['env'],load()['request']['env'])
        exact(audit[0]['argv'],['plain',name,''])
        exact(audit[0]['cwd'],name)
        actual_permissions(directory)
    with tempfile.TemporaryDirectory(prefix='command-limit-control-') as name:
        child=subprocess.run([PY,'-I','-S','-B',str(FIX/'limit.py'),PY,'-I','-S','-B',
            str(PEER),'file-limit-control',name],cwd=name,env={'LC_ALL':'C.UTF-8'},
            capture_output=True,timeout=5)
        CONTROLS.append({'id':'actual-RLIMIT_FSIZE','argv':child.args,'exit':child.returncode,
                         'stdout_hex':child.stdout.hex(),'stderr_hex':child.stderr.hex(),
                         'result':lines(Path(name)/'limit.jsonl')})
        exact(child.returncode,0);exact(child.stdout,b'');exact(child.stderr,b'')
        exact(lines(Path(name)/'limit.jsonl'),[{'errno':27,'saved':1048576}])
        exact((Path(name)/'limited.bin').read_bytes(),b'x'*1048576)
    with tempfile.TemporaryDirectory(prefix='command-mcp-control-') as name:
        setup=load()
        raw=b''.join((json.dumps(value)+'\n').encode() for value in setup['mcp_control_requests'])
        child=subprocess.run([PY,'-I','-S','-B',str(PEER),'mcp:success',name],input=raw,
            cwd=name,env={'LC_ALL':'C.UTF-8'},capture_output=True,timeout=5)
        CONTROLS.append({'id':'actual-stdio-MCP','argv':child.args,'exit':child.returncode,
                         'stdin_hex':raw.hex(),'stdout_hex':child.stdout.hex(),
                         'stderr_hex':child.stderr.hex()})
        exact(child.returncode,0);exact(child.stderr,b'')
        expected=setup['peer_result'];expected['id']='call-control'
        frames=[json.loads(frame) for frame in child.stdout.splitlines()]
        exact(frames,[{'jsonrpc':'2.0','id':'init-control','result':setup['initialize_result']},expected])
        exact(child.stdout,(Path(name)/'emitted.bin').read_bytes())
        exact((Path(name)/'received.bin').read_bytes(),raw)
    # Setup itself reaches real generated Session/state before any proposed command dispatch.
    with Commands() as app:
        exact(app.session_rows(),app.initial_session)
        exact(app.store.exists(),False)
        exact(list(app.output.iterdir()),[])
    return {'python':PY,'version':sys.version,'uid':os.geteuid(),
            'accepted_cases':len(load('cases.json')),'behavior_groups':[n for n,_ in GROUPS],
            'qualified_real_child':True,'qualified_copied_app_and_session':True,
            'qualified_real_MCP_peer':True,'qualified_real_permission_and_EFBIG':True}


def actual_permissions(root):
    target=root/'permissions'
    target.mkdir()
    file=target/'owned.txt';file.write_bytes(b'keep')
    target.chmod(0o500)
    try:
        for operation in (lambda:(target/'new.txt').write_bytes(b'x'),file.unlink):
            try: operation()
            except OSError as error:
                CONTROLS.append({'id':'actual-EACCES','errno':error.errno})
                exact(error.errno,13)
            else: raise DiagnosticError('Actual non-root permission control did not refuse')
        exact(file.read_bytes(),b'keep')
    finally: target.chmod(0o700)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--suite',required=True,choices=['delivery','behavior'])
    parser.add_argument('--output-root')
    args=parser.parse_args()
    output=Path(args.output_root or os.environ['POISE_RUN_OUTPUT_DIR']).absolute()
    output.mkdir(parents=True,exist_ok=True)
    owned=output/'command-cases';owned.mkdir(exist_ok=True);tempfile.tempdir=str(owned)
    report={'suite':args.suite,'python':PY,'source_commit':subprocess.check_output(
        ['git','rev-parse','HEAD'],cwd=APP,text=True).strip(),'prerequisites':None,
        'groups':[],'errors':[],'failures':[],'observations':OBSERVATIONS,'setup_calls':CALLS,
        'state_calls':LOG,'sql':SQL_LOG,'executed_cases':CASES,
        'environment_controls':CONTROLS,
        'future_semantic_groups':[n for n,_ in GROUPS] if args.suite=='delivery' else []}
    executed=0
    phase='prerequisites'
    try:
        report['prerequisites']=prerequisites()
        for phase,check in ([('delivery',delivery)] if args.suite=='delivery' else GROUPS):
            executed+=1
            check()
            report['groups'].append({'id':phase,'status':'passed'})
        if args.suite=='behavior':
            exact(sorted(set(CASES)),sorted(c['id'] for c in load('cases.json')))
    except Exception as error:
        category='failures' if (isinstance(error,MissingCommand) or
            args.suite=='behavior' and isinstance(error,AssertionError)) else 'errors'
        report[category].append({'group':phase,'reason':type(error).__name__+': '+str(error),
                                 'traceback':traceback.format_exc()})
    report['status']='error' if report['errors'] else 'failed' if report['failures'] else 'passed'
    (output/'command-details.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'status':report['status'],'groups':1 if args.suite=='delivery' else 4,
                     'executed':executed,'errors':len(report['errors']),'failures':len(report['failures'])}))
    return 2 if report['errors'] else 1 if report['failures'] else 0


if __name__=='__main__':
    raise SystemExit(main())
