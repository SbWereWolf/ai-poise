"""Command intent, typed replay equivalence and complete public values."""
from pathlib import Path

from domain.generic import Problem, exact_fields, invalid, positive_count, text


def command_id(value,settings,path='/input/command_id'):
    text(value,path)
    if len(value.encode('utf-8'))>settings.limits['id_bytes']:
        invalid(path)
    return value


def identity_input(value,settings):
    exact_fields(value,{'command_id'},'/input')
    return command_id(value['command_id'],settings)


def request_input(value,settings):
    exact_fields(value,{'schema','command_id','transport','argv','cwd','env',
                        'execution_budget_ms','cleanup_budget_ms','mcp'},'/input')
    if not isinstance(value['schema'],str): invalid('/input/schema')
    if value['schema']!='poise-command-request/v1':
        raise Problem('unsupported_schema',{'schema':value['schema']},'select:schema')
    command_id(value['command_id'],settings)
    if value['transport'] not in ('command','mcp_stdio'): invalid('/input/transport')
    args=value['argv']
    if not isinstance(args,list) or not args: invalid('/input/argv')
    for index,arg in enumerate(args):
        if not isinstance(arg,str) or '\0' in arg: invalid('/input/argv/'+str(index))
    if not args[0] or not Path(args[0]).is_absolute(): invalid('/input/argv/0')
    text(value['cwd'],'/input/cwd')
    if not Path(value['cwd']).is_absolute(): invalid('/input/cwd')
    env=value['env']
    if not isinstance(env,dict): invalid('/input/env')
    for key,item in env.items():
        if not key or '=' in key or '\0' in key or not isinstance(item,str) or '\0' in item:
            invalid('/input/env/'+key)
    for key in ('execution_budget_ms','cleanup_budget_ms'):
        if not positive_count(value[key]): invalid('/input/'+key)
    limits=settings.limits
    if value['cleanup_budget_ms']<=limits['terminate_grace_ms']: invalid('/input/cleanup_budget_ms')
    if value['execution_budget_ms']+value['cleanup_budget_ms']+limits['delivery_margin_ms']>limits['completion_budget_ms']:
        raise Problem('command_budget_incompatible',{key:value[key] for key in ('execution_budget_ms','cleanup_budget_ms')}|
            {key:limits[key] for key in ('completion_budget_ms','delivery_margin_ms')},'configure:commands')
    if value['transport']=='command':
        if value['mcp'] is not None: invalid('/input/mcp')
    else:
        mcp=exact_fields(value['mcp'],{'protocol_version','client_info','tool_name','arguments'},'/input/mcp')
        for key in ('protocol_version','tool_name'): text(mcp[key],'/input/mcp/'+key)
        client=exact_fields(mcp['client_info'],{'name','version'},'/input/mcp/client_info')
        for key in ('name','version'): text(client[key],'/input/mcp/client_info/'+key)
        if not isinstance(mcp['arguments'],dict): invalid('/input/mcp/arguments')
    return value


def same_json(left,right):
    if type(left) in (int,float) and type(right) in (int,float): return left==right
    if type(left) is not type(right): return False
    if isinstance(left,dict):
        return left.keys()==right.keys() and all(same_json(v,right[k]) for k,v in left.items())
    if isinstance(left,list):
        return len(left)==len(right) and all(same_json(a,b) for a,b in zip(left,right))
    return left==right


def metadata(id,transport):
    return {'schema':'poise-command/v1','operation':'request','command_id':id,'transport':transport,
            'state':'ADMITTED','termination':None,'deadline_exceeded':False,'business_error':None,
            'stdout':None,'stderr':None,'full_result_ref':None,'error':None,'replayed':False}


def full_result(value,mcp_response):
    return {'schema':'poise-command-result/v1',**{key:value[key] for key in (
        'command_id','transport','state','termination','deadline_exceeded','business_error',
        'stdout','stderr','error')},'mcp_response':mcp_response}


def absent(id):
    return {'schema':'poise-command-delete/v1','command_id':id,'state':'absent'}


def error(code,details,action):
    return {'code':code,'details':details,'action':action}
