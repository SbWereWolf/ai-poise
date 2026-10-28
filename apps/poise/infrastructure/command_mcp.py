"""One stdio tool invocation, retaining provider data and cumulative inbound bounds."""
import json

from domain.command import error
from infrastructure.json_io import loads


class McpCall:
    def __init__(self,spec,limits):
        self.spec,self.limits=spec,limits
        self.buffer=bytearray()
        self.outgoing=bytearray()
        self.messages=0
        self.phase='initialize'
        self.pending_id=1
        self.finished=False
        self.failed=False
        self.result=None
        self.business=None
        self.error=None
        self.queue({'jsonrpc':'2.0','id':1,'method':'initialize',
                    'params':{'protocolVersion':spec['protocol_version'],'capabilities':{},
                              'clientInfo':spec['client_info']}})

    def queue(self,value):
        self.outgoing.extend((json.dumps(value,ensure_ascii=False,allow_nan=False)+'\n').encode())

    def fail(self,reason):
        self.failed,self.finished=True,True
        self.error=error('command_protocol_failed',{'phase':self.phase,'reason':reason},'configure:command')
        self.buffer.clear()

    def receive(self,chunk):
        if self.failed: return
        self.buffer.extend(chunk)
        while b'\n' in self.buffer:
            line,_,remaining=self.buffer.partition(b'\n')
            self.buffer=bytearray(remaining)
            self.messages+=1
            if len(line)>self.limits['mcp_max_message_bytes'] or self.messages>self.limits['mcp_max_messages']:
                self.fail('message_limit');return
            try: message=loads(bytes(line))
            except (ValueError,UnicodeError,RecursionError):
                self.fail('invalid_response');return
            self.message(message)
            if self.failed: return
        if len(self.buffer)>self.limits['mcp_max_message_bytes']:
            self.fail('message_limit')

    def message(self,value):
        if not isinstance(value,dict) or value.get('jsonrpc')!='2.0':
            self.fail('invalid_response');return
        if 'id' not in value:
            if (not isinstance(value.get('method'),str) or not value['method']
                    or 'result' in value or 'error' in value
                    or 'params' in value and not isinstance(value['params'],dict)):
                self.fail('invalid_response')
            return
        if ('method' in value or type(value['id']) is not type(self.pending_id)
                or value['id']!=self.pending_id or self.pending_id is None
                or ('result' in value)==('error' in value)):
            self.fail('invalid_response');return
        if 'error' in value:
            fault=value['error']
            if not isinstance(fault,dict) or type(fault.get('code')) is not int or not isinstance(fault.get('message'),str):
                self.fail('invalid_response');return
            if self.phase=='initialize':
                self.fail('invalid_response');return
            self.result,self.business=value,True
            self.error=error('mcp_response_error',{'code':fault['code']},'inspect:command')
            self.pending_id=None
            self.finished=True
            return
        result=value['result']
        if not isinstance(result,dict):
            self.fail('invalid_response');return
        if self.phase=='initialize':
            if result.get('protocolVersion')!=self.spec['protocol_version']:
                self.fail('protocol_version');return
            server=result.get('serverInfo')
            if (not isinstance(result.get('capabilities'),dict) or not isinstance(server,dict)
                    or not isinstance(server.get('name'),str) or not isinstance(server.get('version'),str)):
                self.fail('invalid_response');return
            self.phase='tools_call'
            self.pending_id=2
            self.queue({'jsonrpc':'2.0','method':'notifications/initialized'})
            self.queue({'jsonrpc':'2.0','id':2,'method':'tools/call',
                        'params':{'name':self.spec['tool_name'],'arguments':self.spec['arguments']}})
        else:
            if (not isinstance(result.get('content'),list)
                    or not all(self.content(item) for item in result['content'])
                    or type(result.get('isError',False)) is not bool):
                self.fail('invalid_response');return
            self.result,self.business=value,result.get('isError',False)
            self.pending_id=None
            self.finished=True

    @staticmethod
    def content(value):
        if not isinstance(value,dict): return False
        kind=value.get('type')
        if kind=='text': return isinstance(value.get('text'),str)
        if kind=='image':
            return isinstance(value.get('data'),str) and isinstance(value.get('mimeType'),str)
        if kind=='resource':
            resource=value.get('resource')
            return (isinstance(resource,dict) and isinstance(resource.get('uri'),str)
                    and (isinstance(resource.get('text'),str) or isinstance(resource.get('blob'),str)))
        return False

    def eof(self):
        if self.failed: return
        if self.buffer:
            self.fail('invalid_response')
        elif not self.finished:
            self.finished=True
            self.error=error('command_observation_lost',{'phase':'mcp_response'},'inspect:command')
