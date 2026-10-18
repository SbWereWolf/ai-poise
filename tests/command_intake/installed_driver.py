"""Standalone consumer script: imports only the wheel installed under -I."""
import io
import json
from pathlib import Path
import sys

from poise.infrastructure.hook_transport import HookService
from poise.infrastructure.clock import SystemClock
from poise.interfaces.content_reader import execute


def main():
    packet=json.load(sys.stdin)
    action=packet['action']
    if action=='revision':
        return {'revision':HookService(packet['settings']).revision()}
    if action=='install':
        return HookService(packet['settings']).install(packet['request'])
    if action=='event':
        service=HookService(packet['settings'])
        event=packet['event']
        native=service.event(packet['definition'],event)
        binding=service.latest_binding(event['session_id'],'primary')
        with service.registry.read_transaction() as db:
            row=db.execute('SELECT id FROM hook_events WHERE binding=? AND event=? ORDER BY id DESC LIMIT 1',
                           (binding['session_id'],event['hook_event_name'])).fetchone()
        return {'native':native,'binding':binding,'event_id':None if row is None else row[0]}
    if action=='read':
        out,err=io.StringIO(),io.StringIO()
        code=execute(Path(packet['config']),Path(packet['checkout']),Path(packet['policy']),
                     io.BytesIO(json.dumps(packet['request']).encode()),out,err,SystemClock())
        return {'exit_code':code,'stdout':out.getvalue(),'stderr':err.getvalue()}
    raise ValueError(action)


if __name__=='__main__':
    print(json.dumps(main(),ensure_ascii=False))
