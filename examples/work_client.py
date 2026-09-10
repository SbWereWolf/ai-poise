"""Example driver: one JSON stdin call; never edits a Harness result file."""
import json
import subprocess
import sys
from pathlib import Path


class WorkClient:
    def __init__(self,environment,timeout):
        self.environment,self.timeout=environment,timeout
        self.calls=[]

    def invoke(self,operation,inputs,expected_exit=0,messages=(),telemetry=None):
        packet={'operation':operation,'input':inputs,'messages':list(messages)}
        if telemetry is not None:packet['telemetry']=telemetry
        r=subprocess.run([sys.executable,'-m','harness','work'],input=json.dumps(packet),
            env=self.environment,text=True,capture_output=True,timeout=self.timeout)
        if r.returncode!=expected_exit:raise RuntimeError(r.stdout+r.stderr)
        view=json.loads(r.stdout)
        response=json.loads(Path(view['response_path']).read_text()) if 'response_path' in view else view
        self.calls.append({'operation':operation,'exit_code':r.returncode,'status':response['status']})
        return response

    def bootstrap(self,task,decision=None,feedback=None,rework_stage=None,expected_exit=0):
        return self.invoke('bootstrap',{'task':task,'decision':decision,'feedback':feedback,'rework_stage':rework_stage},expected_exit)

    def verify(self,result,artifacts,expected_exit=0):
        return self.invoke('verify',{'result':result,'artifacts':artifacts},expected_exit)

    def accept(self,expected_exit=0):return self.invoke('accept',{},expected_exit)

    def content(self,expected_exit=0):
        return self.invoke('show',{'queries':[{'id':'content','kind':'content'}]},expected_exit)['results'][0]['value']
