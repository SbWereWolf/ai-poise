"""Test-owned external client; only the selected installed distribution is imported."""
import io
import json
from pathlib import Path
import sys

from poise.common import PoiseError
from poise.infrastructure.hook_transport import HookService
from poise.modules.hook_transport.domain import HookDefinition


def main():
    request = json.load(sys.stdin)
    action = request['action']
    if action == 'capability':
        try:
            HookDefinition.parse(request['definition'])
            result = {'status': 'supported'}
        except PoiseError as exc:
            result = {'status': 'rejected', 'reason': str(exc)}
    elif action == 'reader':
        from poise.interfaces.content_reader import execute
        from poise.infrastructure.clock import SystemClock
        out, err = io.StringIO(), io.StringIO()
        code = execute(Path(request['config']), Path(request['checkout']), Path(request['policy']),
                       io.BytesIO(json.dumps(request['request']).encode()), out, err, SystemClock())
        result = {'exit_code': code, 'stdout': out.getvalue(), 'stderr': err.getvalue()}
    else:
        service = HookService(request['settings'])
        if action == 'revision':
            result = {'revision': service.revision()}
        elif action == 'install':
            result = service.install(request['request'])
        elif action == 'event':
            result = service.event(request['definition_path'], request['event'])
        else:
            raise AssertionError('Unknown test client action: ' + action)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == '__main__':
    main()
