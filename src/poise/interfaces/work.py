"""A single JSON packet through stdin; no user-edited operational files."""
import json
import uuid
from ..application.work import WorkTools
from ..modules.work.selection import SelectionRefusal
from ..common import PoiseError,configured_root,descendant
from ..modules.work.domain import BUSINESS_INCOMPLETE_STATUSES, parse_request
from ..infrastructure.goal_config import strict_json,atomic_write
from ..infrastructure.task_paths import sprint_root
from ..infrastructure.result_views import task_response_root, preflight_work_result, SavedResponse
from ..modules.result_views.domain import bounded_envelope


def read_request(config, stream):
    raw = stream.read(config['batch']['max_input_bytes'] + 1)
    if len(raw) > config['batch']['max_input_bytes']:
        raise PoiseError('Packet exceeds configured max_input_bytes')
    return parse_request(strict_json(raw.decode('utf-8')), config['batch'])


def execute(runtime,stream,output,request=None):
    h=runtime
    category='rejected'
    try:
        if request is None:
            request=read_request(h.cfg,stream)
        preflight_work_result(h, request)
        result=WorkTools(h).invoke(request)
        category = (
            'business_incomplete'
            if result['status'] in BUSINESS_INCOMPLETE_STATUSES
            else 'success'
        )
    except (PoiseError,UnicodeError,RecursionError) as exc:
        result=_rejection_result(exc)
        if not h.store.database.read_only:
            current=h.store.current(h.session)
            h.store.event(h.session,None if current is None else current['id'],'work.blocked',result)
    write_result(h,result,output)
    return {'success':0,'business_incomplete':1,'rejected':2}[category]


def write_result(h,result,output):
    current=h.report_task(result)
    presented, required, command_views = h.result_views.present_result(
        result, h.retired_receipt, lambda receipt_id: h.task_queries.receipt_task(receipt_id),
    )
    # Complete replies survive cleanup and also exist for tiny taskless results.
    root = (task_response_root(h, current) if current is not None else
            (sprint_root(h.state, h.paths, h._identifier(result['sprint']))
             if isinstance(result.get('sprint'), str) else h.runtime))
    metadata={'details':'full_result','session':h.session}
    metadata.update({key:result[key] for key in ('task','stage','iteration','next_work') if key in result})
    if 'results' in result:metadata['result_count']=len(result['results'])
    _write_response(h.cfg,root,presented,output,metadata,command_views,required)


def _rejection_result(reason):
    result = {'status': 'rejected', 'reason': str(reason)}
    if isinstance(reason, SelectionRefusal):
        result['continuation'] = reason.continuation
        if hasattr(reason, 'selection'):
            result['selection'] = reason.selection
    return result


def write_rejection(config_root,config,reason,output):
    """Persist a valid-config packet refusal without establishing a caller/store."""
    state=configured_root(config_root,config['paths']['state'])
    root=descendant(state,config['paths']['runtime'])
    _write_response(config,root,_rejection_result(reason),output,
                    {'details':'full_result'},(),None)


def _write_response(config,root,presented,output,metadata,command_views,required):
    paths=config['paths']
    response=descendant(root,paths['runs'])/str(uuid.uuid4())/paths['response']
    SavedResponse(response, config['batch']['file_mode']).save(presented, canonical=False)
    output.write(bounded_envelope(presented,response,config['limits']['output_chars'],
                                  metadata,{},command_views,required_fields=required))
