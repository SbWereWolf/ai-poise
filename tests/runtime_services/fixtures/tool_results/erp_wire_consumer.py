"""Independent Python-3.12 client for a separately selected provider runtime."""
import json
import subprocess
import sys

request = sys.stdin.read()
command = sys.argv[1:]
identity = subprocess.run([command[0], '-B', '-c',
    'import json,sys,poise;print(json.dumps({"version":list(sys.version_info[:2]),"module":poise.__file__}))'],
    text=True, capture_output=True, check=False)
assert identity.returncode == 0, identity.stderr
result = subprocess.run(command, input=request, text=True, capture_output=True, check=False)
print(json.dumps({'consumer_version': list(sys.version_info[:2]),
                  'client_imported_poise': 'poise' in sys.modules,
                  'provider_identity': json.loads(identity.stdout),
                  'provider_exit_code': result.returncode,
                  'response': json.loads(result.stdout), 'stderr': result.stderr}))
