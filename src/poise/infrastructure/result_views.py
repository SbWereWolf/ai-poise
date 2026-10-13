"""One parser in primary/materialize modes; bounded control, streaming full output."""
from __future__ import annotations
import json
import os
import re
import signal
import subprocess
import sys
import uuid
from pathlib import Path
from ..common import PoiseError, descendant, file_digest, encoded, read_json
from ..modules.result_views.domain import (
    OutputPolicy, primary_text, command_receipts, terminal_materials_notice,
    require_result_budget, result_view,
)
from ..modules.tasks.domain import is_terminal_task_status
from ..modules.work.domain import BUSINESS_INCOMPLETE_STATUSES
from .goal_config import atomic_write
from .task_paths import task_root, sprint_root


def task_response_root(h, task):
    """Use the existing live/retired Task owner, never resurrect its materials."""
    saved = h.task_queries.record(task['id'])
    sprint_id = task.get('sprint_id') if saved is None else saved['sprint_id']
    if ((saved is not None and is_terminal_task_status(saved['status']))
            or h.delivery_tools.unavailable(task['id'])):
        return h.runtime
    return task_root(h.state, h.paths, h._identifier(task['id']), sprint_id)


def preflight_work_result(h, request):
    """Validate actual admissible addresses before requested Work effects."""
    operation, args = request['operation'], request['input']
    current = h.current_task()
    roots = [h.runtime if current is None else task_response_root(h, current)]
    if (operation in ('accept', 'cancel', 'integrate', 'cleanup', 'recover_ownership')
            or operation == 'bootstrap' and args['decision'] == 'continue'):
        roots.append(h.runtime)
    if operation == 'bootstrap':
        task = args['task']
        if isinstance(task, dict) and set(task) == {'request_id', 'task'}:
            allocation = h.task_commands.preview_creation_allocation(task, h.cfg.get('task_ids'))
            if allocation.replayed:
                roots.append(task_response_root(h, {'id': allocation.task_id}))
            else:
                # A concurrent fresh candidate has the same configured width.
                # Do not reinterpret this unconsumed ID as a newly saved owner.
                roots.append(task_root(h.state, h.paths, allocation.task_id, None))
        elif task is not None and 'id' in task:
            if h.sprint_tools.known(task['id']):
                roots.append(sprint_root(h.state, h.paths, h._identifier(task['id'])))
            else:
                roots.append(task_response_root(h, task))
    if isinstance(args.get('task_id'), str):
        roots.append(task_response_root(h, {
            'id': args['task_id'], 'sprint_id': args.get('sprint_id'),
        }))
        if operation == 'task' and args.get('action') == 'recover_cancelled':
            saved = h.task_queries.record(args['task_id'])
            if (saved is not None and saved['status'] == 'cancelled'
                    and not h.delivery_tools.unavailable(args['task_id'])):
                roots.append(task_root(h.state, h.paths, h._identifier(args['task_id']),
                                       saved['sprint_id']))
    if (operation == 'sprint' or operation == 'bootstrap'
            and args['task'] is None and args['decision'] is None and current is None):
        sprint_id = args.get('sprint_id')
        if sprint_id is None:
            selected = h.sprint_tools.commands.read(None, 'current')
            if selected is not None:
                sprint_id = selected['sprint']
        if sprint_id is not None and current is None:
            roots.append(sprint_root(h.state, h.paths, h._identifier(sprint_id)))
        if (current is not None and current['status'] == 'newborn'
                and current['sprint_id'] == sprint_id
                and not h.delivery_tools.unavailable(current['id'])
                and (args.get('action') == 'cancel'
                     or (args.get('action') == 'draft'
                         and isinstance(args.get('changes'), list)
                         and any(change.get('kind') == 'remove_tasks'
                                 and isinstance(change.get('ids'), list)
                                 and current['id'] in change.get('ids', [])
                                 for change in args.get('changes', [])
                                 if isinstance(change, dict))))):
            roots.append(task_root(h.state, h.paths, current['id'], None))
    for root in roots:
        predicted = descendant(root, h.paths['runs']) / str(uuid.uuid4()) / h.paths['response']
        # Reserve the actual longest Work outcome, including the architecture
        # gate; do not assume a shorter success/rejection status will be returned.
        outcome = max(BUSINESS_INCOMPLETE_STATUSES | {'architecture_boundaries_failed'}, key=len)
        require_result_budget(predicted, h.cfg['limits']['output_chars'],
                              outcome)


def saved_result_view(path, mode, budget):
    """Read-only retrieval: no runtime composition, Task claim or operation replay."""
    path = Path(path).resolve(strict=True)
    return result_view(read_json(path), path, budget, mode)


def _tail(path,limit):
    with Path(path).open('rb') as f:
        f.seek(0,os.SEEK_END);f.seek(max(0,f.tell()-limit))
        return f.read(limit)


def primary_stream(path, chars):
    """Ordinary display parser; complete raw files remain authoritative."""
    return primary_text(_tail(path, chars * 4).decode('utf-8', errors='replace'),
                        'tail', None, chars)


def _verify_raw(receipt):
    for stream in ('stdout','stderr'):
        p=Path(receipt[stream])
        if not p.is_file() or p.is_symlink() or file_digest(p)!=receipt[stream+'_digest']:
            raise PoiseError(f'Captured {stream} no longer equals execution receipt')


def _stats(path,chunk):
    size=lines=0;last=b''
    with path.open('rb') as f:
        while block:=f.read(chunk):size+=len(block);lines+=block.count(b'\n');last=block[-1:]
    return {'path':str(path),'bytes':size,'lines':lines+(1 if size and last!=b'\n' else 0),'digest':file_digest(path)}


class OutputParser:
    """Same configured parser for both modes. No business state mutations."""
    def __init__(self,profile,chunk_bytes):
        self.profile,self.chunk=profile,chunk_bytes

    def render(self,mode,receipt,directory):
        p=self.profile;directory=Path(directory)
        _verify_raw(receipt)
        if mode=='primary':
            # stderr first in the display; both tails are always bounded.
            tails=(_tail(receipt['stderr'],p['primary_chars']*4).decode('utf-8','replace')+'\n'+
                   _tail(receipt['stdout'],p['primary_chars']*4).decode('utf-8','replace'))
            return primary_text(tails,p['parser'],p['selection_pattern'],p['primary_chars'])
        if mode!='materialize':raise PoiseError('Unknown explicit parser mode')
        directory.mkdir(parents=True,exist_ok=True)
        views={}
        full=descendant(directory,p['files']['full'])
        full.parent.mkdir(parents=True,exist_ok=True)
        temp=full.with_name(full.name+'.pending')
        with temp.open('wb') as out:
            for name in ('stdout','stderr'):
                # Full is a transparent concatenation; exact raw files remain separate.
                with Path(receipt[name]).open('rb') as src:
                    while block:=src.read(self.chunk):out.write(block)
            out.flush();os.fsync(out.fileno())
        os.replace(temp,full)
        extended=descendant(directory,p['files']['extended'])
        extended.parent.mkdir(parents=True,exist_ok=True)
        extended.write_bytes(_tail(full,p['extended_bytes']))
        views['full']=_stats(full,self.chunk);views['extended']=_stats(extended,self.chunk)
        if p['files']['selected'] is not None:
            target=descendant(directory,p['files']['selected']);target.parent.mkdir(parents=True,exist_ok=True)
            remaining=p['selected_bytes']
            with target.open('wb') as out:
                for name in ('stdout','stderr'):
                    with Path(receipt[name]).open('rb') as src:
                        # readline limit prevents a single hostile line from exhausting memory.
                        while remaining and (line:=src.readline(self.chunk)):
                            if p['selection_pattern'] is not None and re.search(p['selection_pattern'],line.decode('utf-8','replace')):
                                fragment=line[:remaining];out.write(fragment);remaining-=len(fragment)
            views['selected']=_stats(target,self.chunk)
        _verify_raw(receipt)
        return views


def materialize_job(job_path):
    job_path=Path(job_path);job=json.loads(job_path.read_text())
    root=job_path.parent
    manifest=descendant(root,job['manifest'])
    try:
        parser=OutputParser(job['profile'],job['chunk_bytes'])
        views=parser.render('materialize',job['receipt'],root)
        for v in views.values():os.chmod(v['path'],job['file_mode'])
        result={'status':'ready','profile':job['profile']['id'],'receipt_id':job['receipt']['id'],
                'representations':views,'raw':{n:job['receipt'][n] for n in ('stdout','stderr')}}
    except Exception as exc:
        result={'status':'error','profile':job['profile']['id'],'receipt_id':job['receipt']['id'],
                'reason':str(exc),'representations':{}}
    atomic_write(manifest,(encoded(result)+'\n').encode(),job['file_mode'])
    return 0 if result['status']=='ready' else 1


class ResultViews:
    """Async post-command hook, synchronously drained before a result is handed back."""
    def __init__(self,config,journal,materials=None):
        self.policy=OutputPolicy.parse(config);self.config=self.policy.data
        self.journal=journal;self.pending=[];self.incidents=[]
        self.materials = materials

    def _incident(self,receipt_id,reason):
        item={'receipt_id':receipt_id,'component':'result_views','reason':reason}
        self.journal('incident.result_views',item);self.incidents.append(item)

    def primary(self, receipt, directory, *, prepared=False):
        """Read a configured agent view without a worker or receipt mutation."""
        profile = self.policy.select(receipt['argv'])
        if self.materials is not None and not prepared:
            receipt = self.materials.physical_receipt(receipt)
        return OutputParser(profile, self.config['chunk_bytes']).render('primary', receipt, directory)

    def command_views(self, receipts, *, retired_ids=()):
        """Agent-only records; rendering failure never changes command truth."""
        views = []
        for receipt in receipts:
            if not isinstance(receipt, dict) or not {
                'id', 'argv', 'stdout', 'stderr', 'stdout_digest', 'stderr_digest'
            }.issubset(receipt):
                continue  # Other JSON records are not file-backed command receipts.
            view = {key: receipt[key] for key in ('id', 'method', 'actual_exit_code', 'passed')
                    if key in receipt}
            if receipt['id'] in retired_ids:
                view.update(availability='retired', primary=terminal_materials_notice())
                views.append(view)
                continue
            try:
                view['primary'] = self.primary(receipt, Path(receipt['stdout']).parent)
            except (PoiseError, OSError, ValueError) as exc:
                view['presentation_error'] = str(exc)
            views.append(view)
        return views

    def present_result(self, result, retired_receipt, receipt_task):
        """Prepare shared saved/brief history through explicit state dependencies."""
        receipts = command_receipts(result)
        retired_ids, history, errors = set(), {}, []
        for receipt in receipts:
            try:
                if not retired_receipt(receipt):
                    continue
                task = receipt_task(receipt['id'])
                if task is None:
                    raise PoiseError('Historical receipt owner unavailable')
                history.setdefault(task, []).append({
                    key: receipt[key] for key in ('id', 'method', 'actual_exit_code', 'passed')
                    if key in receipt
                })
                retired_ids.add(receipt['id'])
            except Exception as exc:
                errors.append({'id': receipt['id'], 'presentation_error': str(exc)})
        required = {}
        if history:
            required.update(notice=terminal_materials_notice(), historical_receipts=[
                {'task': task, 'checks': checks} for task, checks in history.items()
            ])
        if errors:
            required['presentation_errors'] = errors
        return ({**result, **required}, required,
                self.command_views(receipts, retired_ids=retired_ids))

    def capture(self,receipt,run_dir,*,prepared=False):
        c=self.config;directory=descendant(Path(run_dir),c['directory'])
        manifest=descendant(directory,c['manifest']);profile=self.policy.select(receipt['argv'])
        self.journal('parser.primary.start',{'receipt_id':receipt['id'],'profile':profile['id']})
        try:
            if self.materials is not None and not prepared:
                receipt = self.materials.physical_receipt(receipt)
            directory.mkdir(parents=True,exist_ok=True)
            primary=self.primary(receipt,directory,prepared=True)
            job={'receipt':receipt,'profile':profile,'chunk_bytes':c['chunk_bytes'],'manifest':c['manifest'],'file_mode':c['file_mode']}
            jobpath=descendant(directory,c['job']);atomic_write(jobpath,(encoded(job)+'\n').encode(),c['file_mode'])
            atomic_write(manifest,(encoded({'status':'pending','receipt_id':receipt['id'],'profile':profile['id'],'representations':{}})+'\n').encode(),c['file_mode'])
            env={**os.environ,'PYTHONPATH':str(Path(__file__).resolve().parents[2])}
            outpath=descendant(directory,c['worker_stdout']);errpath=descendant(directory,c['worker_stderr'])
            outpath.parent.mkdir(parents=True,exist_ok=True);errpath.parent.mkdir(parents=True,exist_ok=True)
            try:
                with outpath.open('wb') as out,errpath.open('wb') as err:
                    child=subprocess.Popen([sys.executable,'-B','-m','poise.output_worker',str(jobpath)],
                        stdin=subprocess.DEVNULL,stdout=out,stderr=err,env=env,start_new_session=True)
            except OSError as exc:
                self._incident(receipt['id'],str(exc))
                atomic_write(manifest,(encoded({'status':'error','receipt_id':receipt['id'],'profile':profile['id'],
                              'reason':str(exc),'representations':{}})+'\n').encode(),c['file_mode'])
                return {'status':'error','primary':primary,'directory':str(directory),'manifest':str(manifest)}
            self.pending.append((child,manifest,receipt['id']))
            self.journal('parser.materialize.started',{'receipt_id':receipt['id'],'profile':profile['id']})
            return {'status':'scheduled','primary':primary,'directory':str(directory),'manifest':str(manifest)}
        except (PoiseError,OSError,ValueError) as exc:
            self._incident(receipt['id'],str(exc))
            try:
                atomic_write(manifest,(encoded({'status':'error','receipt_id':receipt['id'],'profile':profile['id'],
                    'reason':str(exc),'representations':{}})+'\n').encode(),c['file_mode'])
                location=str(manifest)
            except OSError:
                location=None  # explicit absence, never a fake ready link
            return {'status':'error','primary':'','directory':str(directory),'manifest':location}

    def finish(self):
        pending,self.pending=self.pending,[]
        for child,path,run_id in pending:
            try:
                code=child.wait(timeout=self.config['worker_timeout_seconds'])
                status=json.loads(path.read_text())
                if code!=0 or status['status']!='ready':
                    self._incident(run_id,status.get('reason','Materialization worker did not publish ready result'))
                self.journal('parser.materialize.finished',{'receipt_id':run_id,'status':status['status']})
            except (OSError,ValueError,subprocess.TimeoutExpired) as exc:
                if child.poll() is None:
                    try:os.killpg(child.pid,signal.SIGKILL)
                    except ProcessLookupError:pass
                    child.wait()
                self._incident(run_id,str(exc))
                atomic_write(path,(encoded({'status':'error','receipt_id':run_id,'reason':str(exc),'representations':{}})+'\n').encode(),self.config['file_mode'])
        return list(self.incidents)


def read_output_range(path,item,requested,initial_lines):
    """Addressed file read; does not load raw/full output merely to select a page."""
    size=item['bytes'];total_lines=item['lines']
    unit='lines' if requested is None else requested['unit']
    start=1 if requested is None else requested['start']
    end=min(initial_lines,total_lines) if requested is None else requested['end']
    with Path(path).open('rb') as stream:
        if unit=='bytes':
            if start>size:raise PoiseError('Byte range starts after output end')
            end=min(end,size);stream.seek(start);selected=stream.read(end-start)
            try:text=selected.decode('utf-8')
            except UnicodeError as exc:raise PoiseError('Output range is not UTF-8 aligned; use lines or raw file') from exc
            line_range=None;byte_range=[start,end]
        else:
            if not total_lines and start==1:
                return {'text':'','total_lines':0,'total_bytes':0,'returned_lines':[0,0],'returned_bytes':[0,0]}
            if start>total_lines:raise PoiseError('Line range starts after output end')
            end=min(end,total_lines);chunks=[];begin=0
            for number in range(1,end+1):
                if number==start:begin=stream.tell()
                line=stream.readline()
                if number>=start:chunks.append(line)
            text=b''.join(chunks).decode('utf-8','replace');line_range=[start,end];byte_range=[begin,stream.tell()]
    return {'text':text,'total_lines':total_lines,'total_bytes':size,'returned_lines':line_range,'returned_bytes':byte_range}
