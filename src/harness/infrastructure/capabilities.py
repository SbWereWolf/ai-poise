"""Live read-only observations using exact commands or the configured MCP stdio peer."""
import json
import time
import uuid
from pathlib import Path
from datetime import datetime,timezone
from ..common import HarnessError
from ..execution import run_command
from ..modules.capabilities.domain import bind_workspace,satisfies
from .goal_config import atomic_write,strict_json
from .mcp_probe import StdioProbe


class LocalProbeExecutor:
    def __init__(self,root,file_mode,files):
        self.root=Path(root);self.file_mode=file_mode;self.files=files

    def observe(self,spec,workspace):
        p=bind_workspace(spec.data,workspace)
        directory=self.root/uuid.uuid4().hex;directory.mkdir(parents=True)
        started=time.monotonic()
        detail={'id':p['id'],'checked_at':datetime.now(timezone.utc).isoformat(),
                'workspace':workspace,'kind':p['kind'],'timed_out':False}
        obs={'id':p['id'],'status':'unavailable','tool_ref':p['tool_ref'],
             'project_path':workspace if p['project_bound'] else None,'version':None,
             'observation':'probe_failed','tools':[],'receipt':str(directory/self.files['receipt']),
             'remediation':p['remediation']}
        try:
            if not Path(p['cwd']).is_absolute():raise HarnessError('Probe cwd must resolve to an absolute directory')
            if p['kind']=='command':
                result=run_command(p['argv'],Path(p['cwd']),p['environment'],p['timeout_seconds'],directory/self.files['stdout'],directory/self.files['stderr'])
                detail.update(result)
                if result['timed_out']:raise HarnessError('Capability probe timed out')
                if result['actual_exit_code']!=p['expected_exit_code']:raise HarnessError('Probe exit code did not match')
                files=[directory/self.files['stdout'],directory/self.files['stderr']]
                if sum(f.stat().st_size for f in files)>p['max_output_bytes']:raise HarnessError('Probe result exceeds configured output bound')
                stdout,stderr=[f.read_text(encoding='utf-8') for f in files]
                if not all(x in stdout for x in p['stdout_contains']) or not all(x in stderr for x in p['stderr_contains']):
                    raise HarnessError('Probe output did not match explicit predicates')
                if p['json_assertions'] and not satisfies(strict_json(stdout),p['json_assertions']):
                    raise HarnessError('Probe JSON/project binding assertion failed')
                obs['observation']='command_executed'
            else:
                result=StdioProbe(p,directory,self.files).run();detail['protocol']=result
                if p['json_assertions'] and not satisfies(result['smoke'],p['json_assertions']):
                    raise HarnessError('MCP smoke/project binding assertion failed')
                obs.update(observation=result['observation'],tools=result['tools'],version=result['server']['version'])
            obs['status']='available'
        except (HarnessError,OSError,UnicodeError,ValueError) as exc:
            # Expected unavailability is not a Harness Incident; raw replies stay local.
            detail['error']=str(exc);obs['reason']=str(exc)
        detail['status']=obs['status'];detail['duration_seconds']=time.monotonic()-started
        atomic_write(directory/self.files['receipt'],(json.dumps(detail,ensure_ascii=False,indent=2)+'\n').encode(),self.file_mode)
        return obs
