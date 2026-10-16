"""A transparent stateless v2 action fixture, not an ERP installation."""
import argparse
import json
from pathlib import Path
import sys

parser = argparse.ArgumentParser()
parser.add_argument('--id', required=True)
parser.add_argument('--schema', required=True)
parser.add_argument('--role', choices=['check', 'install', 'repair'], required=True)
parser.add_argument('--stderr-file', required=True)
args = parser.parse_args()
sys.stderr.buffer.write(Path(args.stderr_file).read_bytes())
response = {'schema': args.schema, 'requirement_id': args.id, 'action': args.role}
response.update({'state': 'satisfied'} if args.role == 'check' else {'outcome': 'completed'})
print(json.dumps(response))
