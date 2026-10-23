"""Run the documented freeze/entry sequence through the existing public CLI."""
import json
import os
from pathlib import Path
import subprocess
import sys

root = Path(__file__).resolve().parents[4]
config = Path(os.environ['EXECUTION_CONFIG']).resolve()
snapshot = json.loads(Path(os.environ['REQUIREMENTS_SNAPSHOT']).read_text())
task_id = os.environ['EXECUTION_TASK_ID']
actor = os.environ['EXECUTION_ACTOR_ID']


def call(operation, value):
    result = subprocess.run(
        [sys.executable, '-B', str(root / 'apps/execution/bin/execution'), '--config', str(config)],
        input=json.dumps({'schema': 'stride-execution/v1', 'operation': operation, 'input': value}) + '\n',
        capture_output=True, text=True, check=True,
    )
    return json.loads(result.stdout)


def query(kind):
    return call('query', {'queries': [{'id': 'current', 'kind': kind, 'task_id': task_id}]})


def act(action, **fields):
    current = query('execution')
    execution = current['results'][0]['value']
    return call('lifecycle', {
        'action': action, 'request_id': f"example-{execution['generation']}-{action}",
        'expected_revision': current['revision'], 'task_id': task_id,
        'expected_generation': execution['generation'],
        'expected_contract_revision': execution['contract_revision'], 'actor_id': actor,
        **fields,
    })


frozen = act('freeze', requirements_snapshot=snapshot)
entered = act('enter', entry_tree=os.environ['EXECUTION_ENTRY_TREE'])
print(json.dumps({'freeze': frozen, 'enter': entered,
                  'lifecycle': query('lifecycle'), 'restart_history': query('restart_history')},
                 ensure_ascii=False))
