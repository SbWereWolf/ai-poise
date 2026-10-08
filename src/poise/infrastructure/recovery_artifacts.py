"""Physical placement preserves the logical identities of delivered artifacts."""
from pathlib import Path
from ..artifacts import artifact_identity
from ..common import PoiseError


def artifact_placement(records, delivery):
    if delivery is None:
        return records
    manifest = delivery['manifest']
    paths = {delivery['binding']['locations'][item['placement']]: item
             for item in manifest['files'] if 'placement' in item}
    for record in records:
        item = paths.get(record['path'])
        if item is None:
            continue
        tid = item['task_id']
        if record['scope'] != 'task' or record['owner'] != tid:
            raise PoiseError('Delivered artifact owner/scope differs from mapping')
        source_root = Path(manifest['owners']['task'][tid])
        relative = Path(item['placement']).relative_to(source_root).as_posix()
        record['id'] = artifact_identity('task', tid, relative)
        record['relative_path'] = relative
    return records
