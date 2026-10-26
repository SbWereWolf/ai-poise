"""Independent physical-owner CLI used only by cleanup boundary tests."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

DATA = json.loads((Path(__file__).parents[1] / 'fixtures/cleanup-contract.json').read_text())


def bind(value, mapping):
    if isinstance(value, dict):
        return {key: bind(item, mapping) for key, item in value.items()}
    if isinstance(value, list):
        return [bind(item, mapping) for item in value]
    return mapping.get(value, value) if isinstance(value, str) else value


def git(root, *args):
    result = subprocess.run(['git', '-C', str(root), *args], capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(result.stderr)
    return result.stdout.strip()


def owner(root, work):
    path = root / 'owner-state.json'
    state = json.loads(path.read_text())
    attempt = work['attempt_id']
    state['calls'].append({'attempt_id': attempt, 'mode': work['mode']})
    previous = state['attempts'].get(attempt)
    if previous is not None:
        result = previous
    elif work['mode'] == 'reconcile':
        result = deepcopy(DATA['owner_recovery']['not_started'])
    elif state['unknown']:
        result = deepcopy(DATA['owner_recovery']['unknown'])
    elif state['fail_once'] == work['resource']['resource_id']:
        state['fail_once'] = None
        result = bind(DATA['owner_recovery']['failed'], {'@RECEIPT': 'receipt:' + attempt})
        state['attempts'][attempt] = result
    else:
        resource = work['resource']
        ref = resource['reference']
        repository = root / 'repository'
        operation = work['operation']
        mapping = {'@RECEIPT': 'receipt:' + attempt}
        if operation == 'remove':
            if resource['kind'] == 'worktree':
                git(repository, 'worktree', 'remove', ref['path'])
            elif resource['kind'] == 'branch':
                git(repository, 'update-ref', '-d', 'refs/heads/' + ref['name'], ref['commit'])
            else:
                Path(ref['path']).unlink()
        elif operation == 'preserve':
            target = root / 'preserved.bundle'
            git(repository, 'bundle', 'create', str(target), 'refs/heads/' + ref['name'])
            git(repository, 'bundle', 'verify', str(target))
            mapping['@PRESERVATION'] = str(target)
        elif operation == 'integration_status':
            disposition = work['disposition']
            git(repository, 'merge-base', '--is-ancestor', ref['commit'], disposition['target_commit'])
            mapping.update({'@COMMIT': ref['commit'], '@TARGET': disposition['target_commit'],
                            '@INTEGRATION': disposition['integration_request_id']})
        else:
            raise AssertionError(operation)
        result = bind(DATA['owner_success'][operation], mapping)
        state['attempts'][attempt] = result
        key = operation + ':' + resource['resource_id']
        state['effects'][key] = state['effects'].get(key, 0) + 1
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + '\n')
    return result


def main():
    assert len(sys.argv) == 3 and sys.argv[1] == '--root'
    work = json.loads(sys.stdin.read())
    print(json.dumps(owner(Path(sys.argv[2]), work), ensure_ascii=False))


if __name__ == '__main__':
    main()
