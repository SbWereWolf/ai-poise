"""Explicit request fixtures; no runtime/task file edits in the new client flow."""
from copy import deepcopy
from conftest import write_json


def batch_config():
    return {'max_input_bytes':1048576,'max_items':1000,'max_artifact_bytes':262144,
            'file_mode':384,'artifact_lock':'artifact.lock',
            'artifact_directories':{'runtime':'generated','task':'artifacts','sprint':'artifacts'},
            'templates':{'note':{'version':'1','text':'# ${title}\n\n${body}\n','parameters':['title','body']}},
            'message_source':{'id':'chat-visible','mode':'agent_reported'},
            'message_reasons':['initial','continue','feedback','requirement_change','clarification','authorization','cancel'],
            'initial_read_lines':50}


def configure(project):
    project['cfg']['schema']='ddd-accounting-12'
    project['cfg']['batch']=batch_config()
    project['cfg']['paths'].pop('result',None)  # fixture evolution, not product migration
    write_json(project['config_path'],project['cfg'])


def message(identifier='u1',reason='initial'):
    return {'conversation_id':'conversation-A','message_id':identifier,
            'occurred_at':'2026-09-06T15:00:00+00:00','reason':reason,'subject':None}


def request(operation, args, messages=()):
    return {'operation':operation,'input':args,'messages':list(messages)}


def bootstrap(tools, project, messages=()):
    return tools.invoke(request('bootstrap',{'task':deepcopy(project['task']), 'decision':None,
                    'feedback':None,'rework_stage':None},messages))


def result(context, text='Работа выполнена.'):
    value=deepcopy(context['result_template'])
    value['sections']['report']=text
    value['commit_message']='test: verified batch'
    return value


def verify(tools, value, artifacts=(), messages=()):
    return tools.invoke(request('verify',{'result':value,'artifacts':list(artifacts)},messages))


def text_artifact(scope='task', path='report.md', text='Результат'):
    return {'scope':scope,'path':path,'source':{'kind':'text','text':text}}
