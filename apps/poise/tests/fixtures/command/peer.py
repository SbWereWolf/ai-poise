"""Independent finite CLI/MCP child: no subject imports or subject serializers."""
import copy
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

mode, directory = sys.argv[1:3]
root = Path(directory)
setup = json.loads(Path(__file__).with_name('setup.json').read_text())


def append(name, value):
    with (root / name).open('ab', buffering=0) as target:
        target.write((json.dumps(value, ensure_ascii=False) + '\n').encode())


def wait_gate(name, seconds=12):
    end = time.monotonic() + seconds
    while not (root / name).exists():
        if time.monotonic() >= end:
            raise RuntimeError('Independent peer gate expired')
        time.sleep(.005)


if mode == 'hold_pipe':
    os.close(2)
    (root / 'descendant-ready').write_text(str(os.getpid()))
    wait_gate('descendant-release')
    (root / 'descendant-finished').touch()
    raise SystemExit(0)

append('dispatch.jsonl', {'pid': os.getpid(), 'argv': sys.argv[1:],
                         'cwd': os.getcwd(), 'env': dict(os.environ)})

if mode == 'file-limit-control':
    with (root/'limited.bin').open('wb',buffering=0) as target:
        first=target.write(b'x'*2097152)
        try:
            target.write(b'x')
        except OSError as error:
            append('limit.jsonl',{'errno':error.errno,'saved':first})
        else:
            raise AssertionError('Actual file-size limit did not refuse the next byte')
elif mode in ('term', 'ignore'):
    signal.signal(signal.SIGTERM, (lambda *_: sys.exit(7)) if mode == 'term' else signal.SIG_IGN)
    os.write(1, b'READY\n')
    (root / 'ready').write_text('ready')
    while True:
        time.sleep(.01)
elif mode == 'gate':
    (root / 'ready').write_text('ready')
    wait_gate('release')
    os.write(1, b'OUT\n')
elif mode == 'pipe':
    os.write(1, b'READY\n')
    subprocess.Popen([sys.executable, '-I', '-S', '-B', __file__, 'hold_pipe', str(root)])
    wait_gate('descendant-ready')
elif mode in ('large', 'capture'):
    out = ('Ж🙂\n'.encode() * 100000) if mode == 'large' else b'x' * 2097152
    with os.fdopen(os.dup(1), 'wb', buffering=0) as stream:
        stream.write(out)
    if mode == 'large':
        os.write(2, b'err\n' * 25000)
elif mode == 'exit7':
    os.write(1, b'OUT\n')
    os.write(2, b'ERR\n')
    raise SystemExit(7)
elif mode == 'binary':
    os.write(1, bytes.fromhex('00ff4f5554d0960a'))
    os.write(2, bytes.fromhex('45525200800a'))
    raise SystemExit(7)
elif mode == 'plain':
    os.write(1, b'OUT\n')
    os.write(2, b'ERR\n')
elif mode.startswith('mcp:'):
    variant = mode[4:]

    def receive():
        raw = sys.stdin.buffer.readline()
        if not raw:
            return None
        with (root / 'received.bin').open('ab') as audit:
            audit.write(raw)
        value = json.loads(raw)
        append('received.jsonl', value)
        return value

    def emit(value):
        raw = (json.dumps(value, ensure_ascii=False, separators=(',', ':')) + '\n').encode()
        # Audit exactly the bytes emitted by this independent producer.
        written = 0
        while written < len(raw):
            count = os.write(1, raw[written:])
            with (root / 'emitted.bin').open('ab') as audit:
                audit.write(raw[written:written + count])
            written += count
        append('emitted.jsonl', value)

    initial = receive()
    assert initial['method'] == 'initialize'
    initialized = copy.deepcopy(setup['initialize_result'])
    if variant == 'version':
        initialized['protocolVersion'] = '2024-10-07'
    emit({'jsonrpc': '2.0', 'id': initial['id'], 'result': initialized})
    if variant != 'version':
        notification = receive()
        assert notification in ({'jsonrpc':'2.0','method':'notifications/initialized'},
                                {'jsonrpc':'2.0','method':'notifications/initialized','params':{}})
        called = receive()
        assert called['method'] == 'tools/call'
        append('effects.jsonl', called['params'])
        if variant != 'eof':
            if variant in ('notification', 'count-ok', 'count-excess', 'bytes-ok', 'bytes-excess'):
                notice = copy.deepcopy(setup['notification'])
                if variant.startswith('bytes'):
                    target = 1024 if variant == 'bytes-ok' else 1025
                    notice['params']['data'] = ''
                    base = len(json.dumps(notice, ensure_ascii=False, separators=(',', ':')).encode())
                    notice['params']['data'] = 'x' * (target - base)
                    assert len(json.dumps(notice, ensure_ascii=False, separators=(',', ':')).encode()) == target
                emit(notice)
                if variant == 'count-excess':
                    emit(notice)
            result = copy.deepcopy(setup['rpc_error' if variant == 'rpc-error' else 'peer_result'])
            wrong = 'unmatched-2' if called['id']=='unmatched-1' else 'unmatched-1'
            result['id'] = wrong if variant == 'wrong-id' else called['id']
            if variant == 'business':
                result['result']['isError'] = True
            if variant == 'large':
                result['result']['content'][0]['text'] = Path(__file__).with_name('large-mcp-text.txt').read_text()
            emit(result)
    # Finite protocol peers exit on real producer EOF, not on a test-side fake exit.
    if variant != 'eof':
        assert sys.stdin.buffer.read() == b''
    raise SystemExit(7 if variant == 'exit7' else 0)
else:
    raise ValueError(mode)
