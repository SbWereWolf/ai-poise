"""The public packet grammar; no I/O and no caller-supplied current task identity."""
from copy import deepcopy
from ..artifact_factory.domain import exact, relative
from ..foundation.errors import DomainError


SPRINT_OVERVIEW_STATUSES = frozenset({'planned','active','completed','cancelled','blocked'})
STANDALONE_TASK_STATUSES = frozenset({'newborn','available','active','verified','accepted','completed','cancelled'})


def status_filter(value, allowed, name):
    if value is None:return
    if not isinstance(value,list) or any(not isinstance(item,str) or item not in allowed for item in value):
        raise DomainError(f'Unknown {name} status')
    if len(value)!=len(set(value)):
        raise DomainError(f'{name} statuses must be unique')


def parse_request(value, config):
    exact(value,{'operation','input','messages','telemetry'} if isinstance(value,dict) and 'telemetry' in value else {'operation','input','messages'},'work packet')
    shapes={'bootstrap':{'task','decision','feedback','rework_stage'},
            'verify':{'result','artifacts'},'show':{'queries'},'accept':set(),
            'recover_empty_rework':{'task_id','reason'},
            'handoff':{'request_id','reason','result','commit_message','artifact_paths'},
            'cancel':{'reason'},'artifacts':{'items'},'integrate':None,
            'cleanup':{'request_id','task_id','commit_disposition','authorization'},
            'task':None,'sprint':None,'transfer':None}
    op=value['operation']
    if not isinstance(op,str) or op not in shapes:
        raise DomainError('Unknown work operation')
    if op not in ('task','sprint','transfer','integrate'):exact(value['input'],shapes[op],f'{op} input')
    elif not isinstance(value['input'],dict):raise DomainError('sprint input must be an object')
    if not isinstance(value['messages'],list) or len(value['messages'])>config['max_items']:
        raise DomainError('messages requires a bounded list')
    if op=='bootstrap':
        if value['input']['task'] is not None and not isinstance(value['input']['task'],dict):
            raise DomainError('task must be an object or explicit null; files are not input')
    if op=='verify':
        if value['input']['result'] is not None and not isinstance(value['input']['result'],dict):
            raise DomainError('result must be an object or explicit null')
        if not isinstance(value['input']['artifacts'],list):
            raise DomainError('artifacts must be a list')
    if op=='handoff':
        if not isinstance(value['input']['artifact_paths'],list):raise DomainError('handoff paths must be a list')
        if value['input']['result'] is not None and not isinstance(value['input']['result'],dict):raise DomainError('handoff result must be an object or null')
    if op=='artifacts' and not isinstance(value['input']['items'],list):
        raise DomainError('items must be a list')
    if op=='cleanup':
        from ..task_cleanup.domain import CleanupIntent
        CleanupIntent.parse(value['input'])
    if op=='show':
        queries=value['input']['queries']
        if not isinstance(queries,list) or not queries or len(queries)>config['max_items']:
            raise DomainError('queries requires a nonempty bounded list')
        ids=set()
        for query in queries:
            if not isinstance(query,dict) or not isinstance(query.get('id'),str) or not query['id'] or query['id'] in ids:
                raise DomainError('Query IDs must be unique nonempty strings')
            ids.add(query['id'])
            shapes_q={'accounting':{'id','kind','scope','group_by','from','to'},'tool_result':{'id','kind','receipt_id','representation','range'},'sprint':{'id','kind','sprint_id','view'},'work_overview':{'id','kind','sprint_statuses','standalone_task_statuses'},'task':{'id','kind'},'integration':{'id','kind','task_id','request_id'},'task_cleanup':{'id','kind','task_id','request_id'},'messages':{'id','kind'},'content':{'id','kind'},'evidence':{'id','kind'},'verification_registry':{'id','kind'},
               'section':{'id','kind','name','stage','submission','range'},
               'trace':{'id','kind','route','point','submission'}}
            kind=query.get('kind')
            if not isinstance(kind,str) or kind not in shapes_q:
                raise DomainError('Unknown batch query kind')
            exact(query,shapes_q[kind],'query')
            if kind=='work_overview':
                status_filter(query['sprint_statuses'],SPRINT_OVERVIEW_STATUSES,'sprint')
                status_filter(query['standalone_task_statuses'],STANDALONE_TASK_STATUSES,'standalone task')
            if kind in ('section','tool_result') and query['range'] is not None:
                r=query['range'];exact(r,{'unit','start','end'},'range')
                if r['unit'] not in ('lines','bytes') or type(r['start']) is not int or type(r['end']) is not int:
                    raise DomainError('Invalid explicit range')
                minimum=1 if r['unit']=='lines' else 0
                if r['start']<minimum or r['end']<r['start']:
                    raise DomainError('Range must be ordered and within the selected unit')
    if op == 'task':
        task_shapes = {
            'create': {'action','request_id','task_id','sprint_id'},
            'edit': {'action','request_id','task_id','expected_revision','patch'},
            'ready': {'action','request_id','task_id','expected_revision'},
        }
        action = value['input'].get('action') if isinstance(value['input'],dict) else None
        if action not in task_shapes:
            raise DomainError('Unknown Task action')
        exact(value['input'], task_shapes[action], f'task {action} input')
    # Bound all object collections, not just top-level operations.
    def check(node):
        if isinstance(node,(list,dict)):
            if len(node)>config['max_items']:
                raise DomainError('Packet collection exceeds configured max_items')
            for child in (node.values() if isinstance(node,dict) else node):check(child)
    check(value)
    return deepcopy(value)


def read_range(text, requested, initial_lines):
    """Lines are 1-based inclusive; bytes 0-based half-open, UTF-8 aligned."""
    data=text.encode('utf-8'); lines=text.splitlines(keepends=True)
    if requested is None:
        start,end=1,min(initial_lines,len(lines));unit='lines'
    else:
        start,end,unit=requested['start'],requested['end'],requested['unit']
    if unit=='lines':
        if not lines and start==1:
            selected='';bs=be=0;actual=[0,0]
        else:
            if start>len(lines):raise DomainError('Line range starts after section end')
            end=min(end,len(lines));selected=''.join(lines[start-1:end])
            bs=len(''.join(lines[:start-1]).encode('utf-8'));be=bs+len(selected.encode('utf-8'));actual=[start,end]
    else:
        if start>len(data):raise DomainError('Byte range starts after section end')
        bs,be=start,min(end,len(data))
        try:selected=data[bs:be].decode('utf-8')
        except UnicodeError as exc:raise DomainError('Byte range splits a UTF-8 character; use line range') from exc
        actual=None
    return {'text':selected,'total_lines':len(lines),'total_bytes':len(data),
            'returned_lines':actual,'returned_bytes':[bs,be]}


def validate_config(config):
    from string import Template
    exact(config,{'max_input_bytes','max_items','max_artifact_bytes','file_mode','artifact_lock',
                  'artifact_directories','templates','message_source','message_reasons','initial_read_lines'},'batch config')
    for key in ('max_input_bytes','max_items','max_artifact_bytes','initial_read_lines'):
        if type(config[key]) is not int or config[key]<=0:
            raise DomainError(f'batch.{key} must be an explicit positive integer')
    if type(config['file_mode']) is not int or not 0<=config['file_mode']<=0o777:
        raise DomainError('batch.file_mode is required')
    relative(config['artifact_lock'])
    exact(config['artifact_directories'],{'runtime','task','sprint'},'artifact_directories')
    for value in config['artifact_directories'].values():relative(value)
    if not isinstance(config['templates'],dict):raise DomainError('templates must be an explicit dictionary')
    for name,item in config['templates'].items():
        if not isinstance(name,str) or not name:raise DomainError('template ID is required')
        exact(item,{'version','text','parameters'},'template')
        if not isinstance(item['version'],str) or not item['version'] or not isinstance(item['text'],str):
            raise DomainError('template version/text are required')
        if not isinstance(item['parameters'],list) or any(not isinstance(x,str) or not x for x in item['parameters']):
            raise DomainError('template parameters must be explicit text names')
        tpl=Template(item['text'])
        if not tpl.is_valid() or set(tpl.get_identifiers())!=set(item['parameters']) or len(set(item['parameters']))!=len(item['parameters']):
            raise DomainError('Template placeholders must equal the explicit parameter set')
    exact(config['message_source'],{'id','mode'},'message_source')
    if not isinstance(config['message_source']['id'],str) or not config['message_source']['id']:
        raise DomainError('message source ID required')
    if config['message_source']['mode'] not in ('agent_reported','runtime_event'):
        raise DomainError('Unknown explicit message source mode')
    reasons=config['message_reasons']
    if not isinstance(reasons,list) or any(not isinstance(r,str) or not r for r in reasons) or len(reasons)!=len(set(reasons)):
        raise DomainError('message reasons must be unique text names')
