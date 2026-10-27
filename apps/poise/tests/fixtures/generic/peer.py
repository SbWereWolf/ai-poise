"""Independent real command/MCP/navigation peer; no Poise imports."""
import ast
import json
import os
from pathlib import Path
import sys
import time


def trace(value):
    if os.environ.get('TRACE'):
        with Path(os.environ['TRACE']).open('a') as stream:
            stream.write(json.dumps(value, ensure_ascii=False) + '\n')


def emit(value):
    print(json.dumps(value, ensure_ascii=False), flush=True)


def main():
    mode, *args = sys.argv[1:]
    trace({'mode': mode, 'arguments': args, 'pid': os.getpid()})
    if mode == 'mcp':
        for line in sys.stdin:
            request = json.loads(line)
            trace({'rpc': request})
            method = request['method']
            if method == 'notifications/initialized':
                continue
            if method == 'initialize':
                result = {'protocolVersion': '2024-11-05', 'capabilities': {'tools': {}},
                          'serverInfo': {'name': 'independent-fixture', 'version': '2.4'}}
            elif method == 'tools/list':
                if args == ['missing']:
                    result = {'tools': [{'name': 'unrelated', 'inputSchema': {'type': 'object'}}]}
                elif request.get('params', {}).get('cursor') == 'second':
                    result = {'tools': [{'name': 'describe_project', 'inputSchema': {'type': 'object'}}]}
                else:
                    result = {'tools': [{'name': 'find_symbol', 'inputSchema': {'type': 'object'}}],
                              'nextCursor': 'second'}
            elif method == 'tools/call':
                arguments = request['params']['arguments']
                result = {'structuredContent': {'checkout': arguments['checkout'],
                           'symbol': {'name': arguments['name'], 'line': 1}},
                          'isError': args == ['call-fails']}
            else:
                raise ValueError(method)
            emit({'jsonrpc': '2.0', 'id': request['id'], 'result': result})
        return 0
    if mode == 'timeout':
        print('START', flush=True)
        print('TIMEOUT-DETAIL', file=sys.stderr, flush=True)
        time.sleep(3)
        print('UNREACHED-END', flush=True)
        return 0
    if mode == 'large':
        emit({'body': 'Ж' * 3000, 'end': 'OUTPUT-END'})
        print('OUTPUT-DETAIL-END', file=sys.stderr)
        return 0
    if mode == 'refuse':
        emit({'status': 'ordinary-unavailable', 'detail': 'fixture refusal'})
        print('REFUSAL-DETAIL', file=sys.stderr)
        return 7
    if mode == 'navigation':
        checkout, operation, kind = args
        source = Path(checkout, 'sample.py').read_text()
        definitions = [node for node in ast.parse(source).body if isinstance(node, ast.FunctionDef)]
        candidate = next(node for node in definitions if node.name == 'candidate')
        emit({'checkout': checkout, 'operation': operation, 'provider_kind': kind,
              'symbol': {'name': candidate.name, 'line': candidate.lineno}})
        return 0
    if mode == 'command':
        emit({'checkout': args[0], 'message': 'READY', 'body': 'Данные' * 1200,
              'environment': os.environ.get('FIXTURE_VALUE')})
        print('COMMAND-DETAIL\n' + 'e' * 2000, file=sys.stderr)
        return 0
    raise ValueError(mode)


if __name__ == '__main__':
    raise SystemExit(main())
