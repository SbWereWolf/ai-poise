"""Transparent external process; all decisions originate in test-owned data."""
import json
from pathlib import Path
import subprocess
import sys

operation, events, control_path, workspace, task, project, label = sys.argv[1:]
control = json.loads(Path(control_path).read_text())
root = Path.cwd()
observation = {
    'operation': operation, 'argv': sys.argv[1:], 'cwd': str(root),
    'git_root': subprocess.check_output(['git', 'rev-parse', '--show-toplevel'], text=True).strip(),
    'branch': subprocess.check_output(['git', 'symbolic-ref', '--short', 'HEAD'], text=True).strip(),
    'head': subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(),
    'workspace_argument': workspace, 'task': task, 'project': project, 'label': label,
    'input': (root / 'selected/input.txt').read_text(),
}
with Path(events).open('a', encoding='utf-8') as output:
    output.write(json.dumps(observation, ensure_ascii=False) + '\n')
(root / 'effect.txt').write_text('effect:' + operation, encoding='utf-8')
decision = control['operations'].get(operation, control['default'])
if decision.get('malformed'):
    sys.stdout.write('unrecognized external reply Ω\n')
else:
    packet = {'schema': 'fixture/environment/v2', 'status': decision['status'],
              'payload': control['payload_unit'] * control['repetitions']}
    sys.stdout.write(json.dumps(packet, ensure_ascii=False) + '\n')
sys.stderr.write(control['stderr_unit'] * control['repetitions'])
raise SystemExit(decision['exit_code'])
