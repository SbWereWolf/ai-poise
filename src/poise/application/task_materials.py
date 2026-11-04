"""Short Task-owned commits around permanent, non-overwriting file publication."""
import base64

from ..modules.tasks.materials import (
    TaskMaterialRequest, require_appendable, require_same_request,
)
from ..modules.tasks.material_ports import TaskMaterialQueries, TaskMaterialFiles
from ..modules.foundation.errors import PoiseError


class TaskMaterialCommands:
    def __init__(self, unit_of_work, queries: TaskMaterialQueries,
                 files: TaskMaterialFiles, actor, clock, max_items):
        self.unit_of_work = unit_of_work
        self.queries = queries
        self.files = files
        self.actor = actor
        self.clock = clock
        self.max_items = max_items

    def apply(self, raw):
        intent = TaskMaterialRequest.parse(raw, self.max_items)
        self.files.require_configured()
        if intent.value['action'] == 'read':
            return self.queries.materials_view(intent.task_id)
        if intent.value['action'] == 'read_attachment':
            return self.read_attachment(intent)
        return self.append(intent)

    def read_attachment(self, intent):
        attachment, record = self.queries.material_attachment(
            intent.task_id, intent.value['attachment_id'])
        content = self.files.read(intent.task_id, record)
        if len(content) != attachment['size']:
            raise PoiseError('Attachment size changed; restore its original registered material')
        return {'status': 'read_only', 'task_id': intent.task_id,
                'attachment_id': attachment['id'],
                **{key: attachment[key] for key in ('name', 'digest', 'size')},
                'encoding': 'base64', 'data': base64.b64encode(content).decode('ascii')}

    def _discard(self, task_id, saved):
        # A terminal Task cannot acquire a new logical result. Recheck registry
        # ownership before the file owner compensates only this prepared set.
        with self.unit_of_work() as unit:
            current = unit.task_materials.request(task_id, saved['request_id'])
            if current is not None and current['committed']:
                return current['result']
            records = [r for r in saved['records'] if unit.artifacts.record(r['id']) is None]
        self.files.discard(task_id, records)
        return None

    def append(self, intent):
        status, saved = self.queries.materials_request(intent.task_id, intent.request_id)
        require_same_request(saved, intent)
        if saved is not None and saved['committed']:
            return saved['result']
        try:
            require_appendable(status)
        except PoiseError:
            if saved is not None:
                replay = self._discard(intent.task_id, saved)
                if replay is not None:
                    return replay
            raise
        prepared, records = self.files.prepare(intent)
        with self.unit_of_work() as unit:
            saved = unit.task_materials.request(intent.task_id, intent.request_id)
            require_same_request(saved, intent)
            if saved is not None and saved['committed']:
                return saved['result']
            require_appendable(unit.tasks.load_state(intent.task_id).status)
            if saved is None:
                at = self.clock.observe().audit_utc
                saved = {'request_id': intent.request_id, 'digest': intent.digest(),
                         'actor': self.actor, 'at': at, 'records': records,
                         'result': intent.result(self.actor, at, records)}
                unit.task_materials.prepare(intent.task_id, saved)
            elif saved['records'] != records:
                raise PoiseError('Prepared material placement conflict; restore the configured root and retry')
        self.files.publish(prepared)
        terminal = None
        with self.unit_of_work() as unit:
            current = unit.task_materials.request(intent.task_id, intent.request_id)
            require_same_request(current, intent)
            if current is not None and current['committed']:
                return current['result']
            try:
                status = unit.tasks.load_state(intent.task_id).status
                require_appendable(status)
            except PoiseError:
                terminal = status
            if not terminal:
                unit.artifacts.register_materials(records)
                unit.task_materials.commit(intent.task_id, saved)
        if terminal:
            replay = self._discard(intent.task_id, saved)
            if replay is not None:
                return replay
            require_appendable(terminal)
        return saved['result']
