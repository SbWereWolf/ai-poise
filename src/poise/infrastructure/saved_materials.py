"""Resolve saved logical evidence through explicit registry associations."""
from copy import deepcopy
import json
from pathlib import Path

from ..common import PoiseError, file_digest
from ..artifacts import inspect_paths
from ..modules.evidence.materials import receipt_materials
from .diagnostic_io import readonly_database
from .sqlite.artifacts import SqliteArtifactRepository
from .sqlite.tasks import SqliteExecutionRepository


def saved_receipts(db, task_id):
    """Read each native receipt from its existing evidence or integration owner."""
    receipts = [json.loads(row['data']) for row in db.execute(
        'SELECT data FROM evidence WHERE task_id=? ORDER BY rowid', (task_id,))]
    execution = SqliteExecutionRepository(db)
    if execution.exists(task_id):
        state, _ = execution.load(task_id)
        pending = state['pending']
        if pending is not None and pending.get('kind') == 'result_integration':
            receipts.extend(pending['checks'])
    return receipts


def material_specs(receipt):
    """The binding owner selects exactly the receipt's complete material set."""
    specs = receipt_materials(receipt)
    manifest = specs.get('acceptance_manifest')
    if manifest is not None:
        if file_digest(Path(manifest['path'])) != manifest['digest']:
            raise PoiseError('Acceptance manifest digest changed before material binding')
        for item in json.loads(Path(manifest['path']).read_bytes())['inputs']:
            specs['input/' + item['id']] = {'path': item['path'], 'digest': item['digest']}
    return specs


def bind_receipt_materials(db, task_id, receipt, roots, owners, actor):
    specs = material_specs(receipt)
    records = inspect_paths([spec['path'] for spec in specs.values()], roots, owners)
    by_path = {record['path']: record for record in records}
    bindings = []
    for selector, spec in specs.items():
        record = by_path[spec['path']]
        if record['digest'] != spec['digest']:
            raise PoiseError('Saved material changed before binding: ' + selector)
        bindings.append({'selector': selector, 'artifact_id': record['id']})
    registry = SqliteArtifactRepository(db)
    registry.link_task(task_id, records)
    registry.bind_materials(task_id, receipt['id'], bindings, actor)


class SavedMaterialReader:
    def __init__(self, database):
        self.database = database

    def _context(self, db, receipt):
        bindings = db.execute("SELECT task_id,data FROM journal WHERE event='artifact.materials_bound' "
                              "AND json_extract(data,'$.receipt_id')=?", (receipt['id'],)).fetchall()
        if len(bindings) != 1:
            raise PoiseError('Saved receipt has no explicit material association')
        binding = bindings[0]
        task_id = binding['task_id']
        canonical = [item for item in saved_receipts(db, task_id) if item['id'] == receipt['id']]
        if len(canonical) != 1 or canonical[0] != receipt:
            raise PoiseError('Saved receipt identity or immutable provenance differs')
        value = json.loads(binding['data'])
        if value['schema'] != 'artifact-material-bindings-1':
            raise PoiseError('Unsupported saved material association')
        records = {record['id']: record for record in SqliteArtifactRepository(db).records(task_id)}
        task = db.execute('SELECT metadata FROM tasks WHERE id=?', (task_id,)).fetchone()
        sprint = json.loads(task['metadata'])['sprint_id']
        bindings = {}
        for item in value['bindings']:
            record = records.get(item['artifact_id'])
            if (record is None or record['scope'] not in ('task', 'sprint') or
                    record['owner'] != (task_id if record['scope'] == 'task' else sprint)):
                raise PoiseError('Saved material registry owner/scope differs')
            if item['selector'] in bindings:
                raise PoiseError('Conflicting saved material selectors')
            bindings[item['selector']] = record
        return bindings

    def resolve(self, receipt, selector, expected):
        with readonly_database(self.database) as db:
            record = self._context(db, receipt).get(selector)
        if record is None or record['digest'] != expected:
            raise PoiseError('Saved material selector/digest is not authorized: ' + selector)
        path = Path(record['path'])
        if not path.is_file() or file_digest(path) != expected:
            raise PoiseError('Changed or missing saved material digest: ' + selector)
        return path

    def physical_receipt(self, receipt):
        result = deepcopy(receipt)
        for stream in ('stdout', 'stderr'):
            result[stream] = str(self.resolve(receipt, stream, receipt[stream + '_digest']))
        for output in result.get('outputs', []):
            if output['status'] == 'captured':
                output['path'] = str(self.resolve(receipt, 'output/' + output['id'], output['digest']))
        for system, spec in result.get('systems', {}).items():
            for index, config in enumerate(spec['configs']):
                config['captured_path'] = str(self.resolve(receipt, f'configuration/{system}/{index}', config['digest']))
        for key in ('timeout_maintenance', 'acceptance_manifest'):
            if result.get(key) is not None:
                result[key]['path'] = str(self.resolve(receipt, key, result[key]['digest']))
        return result

    def reference(self, task_id, path, checksum):
        """Resolve an exact logical reference in this Task's canonical receipts."""
        with readonly_database(self.database) as db:
            receipts = saved_receipts(db, task_id)
        for receipt in receipts:
            for selector, spec in receipt_materials(receipt).items():
                if spec['path'] == str(path) and spec['digest'] == checksum:
                    return self.resolve(receipt, selector, checksum)
        # Newly prepared files have no receipt yet. Only an exact existing
        # task-owned registration can authorize the otherwise current path.
        with readonly_database(self.database) as db:
            records = SqliteArtifactRepository(db).records(task_id)
        matches = [r for r in records if r['scope'] == 'task' and r['owner'] == task_id
                   and r['path'] == str(path) and r['digest'] == checksum]
        if len(matches) != 1 or not Path(path).is_file() or file_digest(Path(path)) != checksum:
            raise PoiseError('Task material reference has no registered authority')
        return Path(path)
