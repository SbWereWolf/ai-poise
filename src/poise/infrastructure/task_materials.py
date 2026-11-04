"""Explicit permanent placement for Task user materials, outside disposable roots."""
import hashlib

from ..artifacts import artifact_identity
from ..common import configured_storage_path, PoiseError
from .artifact_factory import FileArtifactFactory


class FileTaskMaterials:
    def __init__(self, state, paths, project, config):
        self.state = state
        self.paths = paths
        self.project = project
        self.config = config

    def require_configured(self):
        if 'task_materials' not in self.paths:
            raise PoiseError('paths.task_materials is required; configure an explicit permanent material root')

    def _factory(self, task_id):
        self.require_configured()
        root = configured_storage_path(self.state, self.paths['task_materials'])
        namespace = lambda text: hashlib.sha256(text.encode('utf-8')).hexdigest()
        return FileArtifactFactory({'task': root / namespace(self.project) / namespace(task_id)},
                                   self.config)

    def prepare(self, intent):
        factory = self._factory(intent.task_id)
        prepared = []
        records = []
        for ci, comment in enumerate(intent.value['items']):
            for ai, attachment in enumerate(comment['attachments']):
                name = f'{intent.namespace()}/{ci}/{ai}'
                try:
                    item, = factory.prepare([{'scope': 'task', 'path': name,
                                              'source': attachment['source']}])
                except PoiseError as exc:
                    raise PoiseError(f'{exc}; correct the file source and retry the original batch') from exc
                relative = item.path.relative_to(factory.roots['task']).as_posix()
                records.append({'id': artifact_identity('task', intent.task_id, relative),
                    'scope': 'task', 'owner': intent.task_id, 'path': str(item.path),
                    'digest': attachment['source']['digest'], 'size': len(item.content)})
                prepared.append(item)
        return (factory, tuple(prepared)), records

    def publish(self, prepared):
        factory, items = prepared
        factory.materialize(items)

    def discard(self, task_id, records):
        factory = self._factory(task_id)
        try:
            for record in records:
                factory.discard_unregistered(record, task_id)
        except (OSError, PoiseError) as exc:
            raise PoiseError(f'Material cleanup incomplete; retry the identical request: {exc}') from exc

    def read(self, task_id, record):
        try:
            return self._factory(task_id).read_material(record, task_id)
        except (OSError, PoiseError) as exc:
            raise PoiseError(f'Attachment original material unavailable; restore its registered bytes: {exc}') from exc
