"""Test transport gate: delay, then invoke the real public registration owner.

This process neither constructs business records nor stores the registry.
The socket is test control only. It is not a product queue or host witness.
"""
import json
from pathlib import Path
import socket
import subprocess
import sys

port, witness = int(sys.argv[1]), Path(sys.argv[2])
command = sys.argv[3:]
observation = json.load(sys.stdin)
with socket.create_connection(('127.0.0.1', port), timeout=5) as channel:
    channel.settimeout(10)
    channel.sendall(b'ready\n')
    assert channel.recv(1) == b'G'
    packet = {'schema': 'poise-tool-result-request-1', 'operation': 'register_unknown',
              'observation': observation}
    result = subprocess.run(command, input=json.dumps(packet, ensure_ascii=False),
                            text=True, capture_output=True, check=False)
    actual = {'observation': observation, 'exit_code': result.returncode,
              'stdout': result.stdout, 'stderr': result.stderr}
    witness.write_text(json.dumps(actual, ensure_ascii=False), encoding='utf-8')
    channel.sendall((str(result.returncode) + '\n').encode())
