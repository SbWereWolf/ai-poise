"""Live read-only observations using exact commands or the configured MCP stdio peer."""
import json
import time
import uuid
from pathlib import Path
from datetime import datetime,timezone
from ..common import PoiseError
from ..execution import RegisteredCheckRunner, run_command
from ..modules.capabilities.domain import CapabilitySummary,bind_workspace,satisfies
from .goal_config import atomic_write,strict_json
from .mcp_probe import StdioProbe


class FileCapabilitySummary:
    """A bounded complete snapshot protected only during its filesystem effects."""

    def __init__(self, path, file_mode, max_input_bytes, fence):
        self.path, self.file_mode = Path(path), file_mode
        self.max_input_bytes, self.fence = max_input_bytes, fence

    def load(self):
        with self.fence.locked():
            with self.path.open('rb') as stream:
                raw = stream.read(self.max_input_bytes + 1)
            if len(raw) > self.max_input_bytes:
                raise PoiseError('Capability summary exceeds configured input limit')
            return CapabilitySummary.from_mapping(strict_json(raw.decode('utf-8')))

    def save(self, summary):
        raw = (json.dumps(summary.to_dict(), ensure_ascii=False, indent=2) + '\n').encode('utf-8')
        if len(raw) > self.max_input_bytes:
            raise PoiseError('Capability summary exceeds configured input limit')
        with self.fence.locked():
            atomic_write(self.path, raw, self.file_mode)


class LocalProbeExecutor:
    def __init__(self,root,file_mode,files, *, deadline=None, cleanup_seconds=None, poll_seconds=None):
        self.root=Path(root);self.file_mode=file_mode;self.files=files
        self.deadline=deadline;self.cleanup_seconds=cleanup_seconds;self.poll_seconds=poll_seconds

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
            if not Path(p['cwd']).is_absolute():raise PoiseError('Probe cwd must resolve to an absolute directory')
            if self.deadline is not None:
                remaining=self.deadline-time.monotonic()-self.cleanup_seconds
                if remaining<=0:raise PoiseError('Optional capability probe budget exhausted; retry configured readiness separately')
                p['timeout_seconds']=min(p['timeout_seconds'],remaining)
            if p['kind']=='command':
                if self.deadline is None:
                    result=run_command(p['argv'],Path(p['cwd']),p['environment'],p['timeout_seconds'],directory/self.files['stdout'],directory/self.files['stderr'])
                else:
                    result=RegisteredCheckRunner().run(directory.name,p['argv'],Path(p['cwd']),p['environment'],
                        p['timeout_seconds'],directory/self.files['stdout'],directory/self.files['stderr'],
                        poll_seconds=self.poll_seconds,cleanup_seconds=self.cleanup_seconds)
                detail.update(result)
                if result['timed_out']:raise PoiseError('Capability probe timed out')
                if result['actual_exit_code']!=p['expected_exit_code']:raise PoiseError('Probe exit code did not match')
                files=[directory/self.files['stdout'],directory/self.files['stderr']]
                if sum(f.stat().st_size for f in files)>p['max_output_bytes']:raise PoiseError('Probe result exceeds configured output bound')
                stdout,stderr=[f.read_text(encoding='utf-8') for f in files]
                if not all(x in stdout for x in p['stdout_contains']) or not all(x in stderr for x in p['stderr_contains']):
                    raise PoiseError('Probe output did not match explicit predicates')
                if p['json_assertions'] and not satisfies(strict_json(stdout),p['json_assertions']):
                    raise PoiseError('Probe JSON/project binding assertion failed')
                obs['observation']='command_executed'
            else:
                result=StdioProbe(p,directory,self.files,deadline=self.deadline).run();detail['protocol']=result
                if p['json_assertions'] and not satisfies(result['smoke'],p['json_assertions']):
                    raise PoiseError('MCP smoke/project binding assertion failed')
                obs.update(observation=result['observation'],tools=result['tools'],version=result['server']['version'])
            obs['status']='available'
        except (PoiseError,OSError,UnicodeError,ValueError) as exc:
            # Expected unavailability is not a Poise Incident; raw replies stay local.
            detail['error']=str(exc);obs['reason']=str(exc)
        detail['status']=obs['status'];detail['duration_seconds']=time.monotonic()-started
        atomic_write(directory/self.files['receipt'],(json.dumps(detail,ensure_ascii=False,indent=2)+'\n').encode(),self.file_mode)
        return obs
