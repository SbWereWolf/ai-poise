"""Independent modern external-command boundary, not a maintenance implementation.

Only the configured WorkTools/Bootstrap boundary is under test here. Separate
operational tests must use the actual installed SDK and application commands.
"""
import argparse
import json
from pathlib import Path
import subprocess
import sys


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False) + '\n')


def main():
    if sys.argv[1] == 'prepare':
        source, python, entry, project, worktree, task, profile, base, values = sys.argv[2:]
        root, profile = Path(worktree), Path(profile)
        selected = json.loads(Path(values).read_text())
        write(profile / 'app.json', {'operations': {}})
        write(profile / 'parameters.json', dict(selected, project=project,
            worktree=worktree, task=task, base=base, events=str(Path(task) / 'events.jsonl')))
        for relative in (
            'platform-infrastructure/config/dev/local-development.settings.json',
            'platform-infrastructure/config/dev/local-dev-host-runtime.settings.json',
            'platform-infrastructure/tests/playwright/cjm.local-development.settings.json',
        ):
            write(root / relative, {'fixture': 'independent configuration'})
        operations = ['infra', 'deps'] + (['adjust'] if selected['consumer'] == 'erp' else []) + ['check']
        for operation in operations:
            result = subprocess.run([entry, operation, '--config', str(profile / 'app.json'),
                '--parameters', str(profile / 'parameters.json'), '--base-dir', worktree],
                capture_output=True)
            if result.returncode:
                sys.stdout.buffer.write(result.stdout)
                sys.stderr.buffer.write(result.stderr)
                return result.returncode
        print(json.dumps({'operation': 'prepare', 'status': 'succeeded'}))
        return 0
    parser = argparse.ArgumentParser()
    parser.add_argument('operation', choices=['infra', 'deps', 'adjust', 'check', 'run'])
    parser.add_argument('--config', required=True, type=Path)
    parser.add_argument('--parameters', required=True, type=Path)
    parser.add_argument('--base-dir', required=True, type=Path)
    args = parser.parse_args()
    data = json.loads(args.parameters.read_text())
    event = {'operation': args.operation, 'cwd': str(Path.cwd()),
        'base_dir': str(args.base_dir), 'project': data['project'],
        'task': data['task'], 'base': data['base']}
    with Path(data['events']).open('a') as stream:
        stream.write(json.dumps(event, ensure_ascii=False) + '\n')
    ready = not (args.operation == 'check' and data['fail_check'])
    print(json.dumps({'operation': args.operation,
        'status': 'succeeded' if ready else 'failed', 'resources': []}))
    if not ready:
        print('Independent resource availability check failed', file=sys.stderr)
    return 0 if ready else 1


if __name__ == '__main__':
    sys.exit(main())
