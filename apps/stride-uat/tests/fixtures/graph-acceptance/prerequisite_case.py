"""Test-only non-graph runtime probe, never an Execution acceptance case."""
import json
import os
from pathlib import Path
import sys

context = json.loads(Path(sys.argv[1]).read_text())
assert context['graph'] is None
Path(context['result_path']).write_text(json.dumps({
    'schema': 'stride-uat-case-result/v1',
    'run_id': context['run_id'],
    'step_id': context['step_id'],
    'application_id': context['application_id'],
    'status': 'PASS',
    'reason': None,
    'graph': None,
}) + '\n')
print(json.dumps({'uid': os.geteuid(), 'gid': os.getegid(), 'graph_acceptance': False}))
