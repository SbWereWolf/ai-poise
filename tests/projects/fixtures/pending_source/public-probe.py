"""Observe real writer/reader returns; delegate the unchanged accepted CLI probe."""
import base64
import json
import os
from pathlib import Path
import runpy
import sys

control = json.loads(Path(sys.argv[1]).read_text())
events = os.fdopen(os.dup(control['event_fd']), 'w', buffering=1)
worktree = Path(__file__).resolve().parents[4]
canonical_io = str(worktree / 'src/poise/infrastructure/goal_config.py')
pending_paths = set()
observing = False


def emit(event, **data):
    events.write(json.dumps({'event': event, 'pid': os.getpid(), **data}) + '\n')
    events.flush()


def audit(event, args):
    if observing or event != 'open':
        return
    path, mode, flags = args
    if (isinstance(path, (str, bytes))
            and os.fsdecode(path).endswith('.publication.json.pending')
            and flags & os.O_ACCMODE == os.O_RDONLY):
        emit('source_pending_read_open', path=os.fsdecode(path), mode=mode, flags=flags)


def profile(frame, event, returned):
    global observing
    if frame.f_code.co_filename != canonical_io:
        return
    name = frame.f_code.co_name
    if name not in ('atomic_write', 'read_document') or event not in ('call', 'return'):
        return
    path = frame.f_locals.get('path')
    if not isinstance(path, Path):
        return
    previous = observing
    observing = True
    try:
        if name == 'read_document':
            if (event == 'return' and str(path).endswith('.publication.json.pending')
                    and isinstance(returned, dict)):
                emit('source_pending_document_return', path=str(path), document=returned)
            return
        if event == 'call':
            visible = {str(p): base64.b64encode(p.read_bytes()).decode('ascii')
                       for p in pending_paths if p.is_file()}
            emit('source_atomic_write_call', path=str(path),
                 content_base64=base64.b64encode(frame.f_locals['content']).decode('ascii'),
                 pending_visible_at_entry=visible)
        else:
            raw = path.read_bytes() if path.is_file() else None
            if str(path).endswith('.publication.json.pending') and raw is not None:
                pending_paths.add(path)
            emit('source_atomic_write_return', path=str(path),
                 stored_base64=None if raw is None else base64.b64encode(raw).decode('ascii'))
    finally:
        observing = previous


sys.addaudithook(audit)
sys.setprofile(profile)
try:
    runpy.run_path(str(Path(__file__).resolve().parents[1]
                      / 'registry_publication/public-probe.py'), run_name='__main__')
finally:
    sys.setprofile(None)
    events.close()
