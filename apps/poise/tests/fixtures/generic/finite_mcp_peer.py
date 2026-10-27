"""Independent finite MCP peer for a focused complete-capture diagnostic."""
import json
import sys

for line in sys.stdin:
    request = json.loads(line)
    method = request['method']
    if method == 'notifications/initialized':
        continue
    if method == 'initialize':
        for index in range(400):
            print(json.dumps({'jsonrpc':'2.0','method':'notifications/progress',
                              'params':{'index':index,'data':'v' * 2048}}), flush=True)
        result = {'protocolVersion':'2024-11-05','capabilities':{'tools':{}},
                  'serverInfo':{'name':'finite-independent-peer','version':'1'}}
    elif method == 'tools/list':
        result = {'tools':[{'name':'find_symbol','inputSchema':{'type':'object'}}]}
    else:
        raise ValueError(method)
    print(json.dumps({'jsonrpc':'2.0','id':request['id'],'result':result}), flush=True)
print('FINITE-PEER-FINISHED', file=sys.stderr, flush=True)
