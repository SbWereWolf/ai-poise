"""Local MCP protocol peer, not an actual JetBrains instance."""
import json,sys
mode=sys.argv[1]
for line in sys.stdin:
    m=json.loads(line)
    if 'id' not in m:continue
    method=m['method'];rid=m['id']
    if mode=='hang':
        import time;time.sleep(30)
    if method=='initialize':
        r={'protocolVersion':m['params']['protocolVersion'] if mode!='version' else 'bad',
           'capabilities':{'tools':{}},'serverInfo':{'name':'test-peer','version':'1'}}
    elif method=='tools/list':
        if 'cursor' not in m['params']:
            r={'tools':[{'name':'read_project','inputSchema':{'type':'object'}}], 'nextCursor':'page2'}
        else:
            r={'tools':[{'name':'rename_refactoring','inputSchema':{'type':'object'}}]}
            if mode=='loop':r['nextCursor']='page2'
    elif method=='tools/call':
        if mode=='error':
            print(json.dumps({'jsonrpc':'2.0','id':rid,'error':{'code':-32603,'message':'error'}}),flush=True);continue
        r={'content':[], 'isError':mode=='toolerror',
           'structuredContent':{'project':m['params']['arguments']['projectPath'] if mode!='wrong_project' else '/other'}}
    else:raise RuntimeError(method)
    if mode=='noisy':print(json.dumps({'jsonrpc':'2.0','method':'notifications/message','params':{'data':'note'}}),flush=True)
    print(json.dumps({'jsonrpc':'2.0','id':rid,'result':r}),flush=True)
