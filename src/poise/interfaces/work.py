"""A single JSON packet through stdin; no user-edited operational files."""
import json
import uuid
from ..application.work import WorkTools
from ..common import PoiseError,descendant
from ..modules.work.domain import BUSINESS_INCOMPLETE_STATUSES, parse_request
from ..infrastructure.goal_config import strict_json,atomic_write
from ..infrastructure.task_paths import sprint_root, task_root
from ..modules.result_views.domain import bounded_envelope, response_required


def _task_root(h, task):
    saved = h.task_queries.record(task['id'])
    sprint_id = task.get('sprint_id') if saved is None else saved['sprint_id']
    return task_root(h.state, h.paths, h._identifier(task['id']), sprint_id)


def execute(runtime,stream,output):
    h=runtime
    category='rejected'
    try:
        raw=stream.read(h.cfg['batch']['max_input_bytes']+1)
        if len(raw)>h.cfg['batch']['max_input_bytes']:
            raise PoiseError('Packet exceeds configured max_input_bytes')
        request=parse_request(strict_json(raw.decode('utf-8')),h.cfg['batch'])
        current=h.current_task()
        root=h.runtime if current is None else _task_root(h, current)
        if request['operation']=='bootstrap' and request['input']['task'] is not None:
            task=request['input']['task']
            if 'id' in task:
                root=_task_root(h, task)
        predicted=descendant(root,h.paths['runs'])/str(uuid.uuid4())/h.paths['response']
        minimal={'status':'content_requirements_failed','response_path':str(predicted)}
        if len(json.dumps(minimal,ensure_ascii=False,separators=(',',':'))+'\n')>h.cfg['limits']['output_chars']:
            raise PoiseError('Configured output_chars cannot fit the result receipt; action not started')
        result=WorkTools(h).invoke(request)
        category = (
            'business_incomplete'
            if result['status'] in BUSINESS_INCOMPLETE_STATUSES
            else 'success'
        )
    except (PoiseError,UnicodeError,RecursionError) as exc:
        result={'status':'rejected','reason':str(exc)}
        current=h.store.current(h.session)
        h.store.event(h.session,None if current is None else current['id'],'work.blocked',result)
    write_result(h,result,output)
    return {'success':0,'business_incomplete':1,'rejected':2}[category]


def write_result(h,result,output):
    current=h.report_task(result)
    # Task reports survive cleanup. A small taskless finalization needs no file.
    response=None
    if current is not None or isinstance(result.get('sprint'),str) or response_required(result,h.cfg['limits']['output_chars']):
        root = (_task_root(h, current) if current is not None else
                (sprint_root(h.state, h.paths, h._identifier(result['sprint']))
                 if isinstance(result.get('sprint'), str) else h.runtime))
        response=descendant(root,h.paths['runs'])/str(uuid.uuid4())/h.paths['response']
        atomic_write(response,(json.dumps(result,ensure_ascii=False,indent=2)+'\n').encode(),h.cfg['batch']['file_mode'])
    metadata={'details':'full_result','session':h.session}
    metadata.update({key:result[key] for key in ('task','stage','iteration','next_work') if key in result})
    if 'results' in result:metadata['result_count']=len(result['results'])
    command_views=h.result_views.command_views(result.get('checks',[]))
    output.write(bounded_envelope(result,response,h.cfg['limits']['output_chars'],metadata,{},command_views))
