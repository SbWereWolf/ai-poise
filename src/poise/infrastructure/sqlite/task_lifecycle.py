"""Canonical action lifecycle reconstruction from detached Task audit rows."""
import json
from ...modules.tasks.lifecycle import TaskLifecycle, invalid_audit


def restore_lifecycle(version, raw_metadata, events):
    try:
        metadata = json.loads(raw_metadata)
    except (ValueError, TypeError) as exc:
        raise invalid_audit('Task metadata must contain valid JSON') from exc
    if not isinstance(metadata, dict):
        raise invalid_audit('Task metadata must be an object')
    restart_versions = []
    for event_version, raw in events:
        try:
            data = json.loads(raw)
        except (ValueError, TypeError) as exc:
            raise invalid_audit('Task event must contain valid JSON') from exc
        if not isinstance(data, dict):
            raise invalid_audit('Task event must be an object')
        if data.get('event') == 'restarted_newborn':
            restart_versions.append(event_version)
    return TaskLifecycle.from_audit(version, metadata.get('restart_history', []), restart_versions)
