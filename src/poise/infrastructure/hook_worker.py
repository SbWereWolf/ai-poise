"""Invocation-owned detached capture process, using RegisteredCheckRunner only."""
import argparse
import json
import os
from pathlib import Path
import time
from uuid import UUID

from ..common import PoiseError
from ..modules.hook_transport.dispatch import AsyncResult
from .hook_dispatch import FileDispatchDeliveryStore, HandlerProcessExecutor
from .goal_config import strict_json


def run(settings_path, delivery_path, identifier, ack_fd):
    from .hook_transport import HookService
    service = HookService(settings_path)
    settings = service.settings
    path = Path(delivery_path).resolve()
    UUID(identifier)
    if not path.is_relative_to(settings.observations):
        raise PoiseError('Worker delivery is outside its configured owner')
    store = FileDispatchDeliveryStore(settings)
    delivery = store.read(path)
    if (delivery['schema'] != 'poise-hook-event-delivery-2'
            or delivery['settings_path'] != str(settings.path)):
        raise PoiseError('Worker settings do not match the admitted delivery')
    rows = [item for item in delivery['invocations'] if item['invocation_id'] == identifier]
    if len(rows) != 1 or rows[0]['mode'] != 'async' or rows[0]['status'] != 'launching':
        raise PoiseError('Worker invocation is not awaiting this launch; use the saved result')
    row = rows[0]
    directory = store.invocation_directory(path, identifier)
    packet = strict_json((directory / 'input.json').read_text(encoding='utf-8'))
    if (packet['invocation_id'] != identifier or packet['event_receipt'] != delivery['event_receipt']
            or packet['configuration'] != row['configuration'] or packet['event'] != delivery['event']):
        raise PoiseError('Worker input differs from its admitted invocation')
    service._dispatch_binding(service.definition(delivery['definition_path']),
                              delivery['event_receipt'], delivery['event'])
    if time.monotonic() >= row['launch_deadline_monotonic']:
        store.update_invocation(path, identifier, status='failed', error={'kind': 'launch_timeout',
            'detail': 'Worker admission exceeded the configured launch deadline; handler not started'})
        return

    def launched(pid):
        store.update_invocation(path, identifier, status='running', launched=True, process_id=pid)
        try:
            os.write(ack_fd, (json.dumps({'invocation_id': identifier, 'launched': True})+'\n').encode())
        except BrokenPipeError:
            pass  # The durable launch fact is retained; capture still has an owner.

    execution = None
    try:
        executor = HandlerProcessExecutor(settings, store)
        raw, execution = executor.execute(row['handler'], packet, directory,
                                          resources=row['resources'], launch_observer=launched)
        result = AsyncResult.parse(raw, identifier)
        store.update_invocation(path, identifier, status='completed', execution=execution, result=result)
    except (PoiseError, OSError, ValueError, TypeError, KeyError) as exc:
        store.update_invocation(path, identifier, status='failed',
            execution=getattr(exc, 'execution', execution),
            error={'kind': type(exc).__name__, 'detail': str(exc)})
    finally:
        os.close(ack_fd)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--settings', required=True)
    parser.add_argument('--delivery', required=True)
    parser.add_argument('--invocation', required=True)
    parser.add_argument('--ack-fd', required=True, type=int)
    args = parser.parse_args()
    run(args.settings, args.delivery, args.invocation, args.ack_fd)


if __name__ == '__main__':
    main()
