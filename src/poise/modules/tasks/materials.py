"""Task-owned append-only comments; independent of execution and review feedback."""
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import uuid

from ..artifact_factory.domain import FileArtifactSource, exact
from ..foundation.errors import DomainError
from .domain import is_terminal_task_status


def required_text(value, label):
    if not isinstance(value, str) or not value.strip() or '\0' in value:
        raise DomainError(f'{label}: provide a nonempty text value without NUL')


@dataclass(frozen=True)
class TaskMaterialRequest:
    value: dict

    @classmethod
    def parse(cls, raw, max_items):
        if not isinstance(raw, dict) or raw.get('action') not in (
                'append', 'read', 'read_attachment'):
            raise DomainError('task_feedback action: provide append/read/read_attachment')
        action = raw['action']
        fields = {'action', 'task_id'} | ({'request_id', 'items'} if action == 'append'
                    else {'attachment_id'} if action == 'read_attachment' else set())
        try:
            exact(raw, fields, 'task_feedback')
            required_text(raw['task_id'], 'task_id')
            if action == 'read_attachment':
                required_text(raw['attachment_id'], 'attachment_id')
            if action == 'append':
                required_text(raw['request_id'], 'request_id')
                items = raw['items']
                if not isinstance(items, list) or not 0 < len(items) <= max_items:
                    raise DomainError('items: provide a nonempty list within max_items')
                for item in items:
                    exact(item, {'text', 'attachments'}, 'comment item')
                    required_text(item['text'], 'comment text')
                    attachments = item['attachments']
                    if not isinstance(attachments, list) or len(attachments) > max_items:
                        raise DomainError('attachments: provide a list within max_items')
                    names = set()
                    for attachment in attachments:
                        exact(attachment, {'name', 'source'}, 'attachment')
                        required_text(attachment['name'], 'attachment name')
                        if attachment['name'] in names:
                            raise DomainError('attachment name: provide unique names per comment')
                        names.add(attachment['name'])
                        source = attachment['source']
                        exact(source, {'kind', 'path', 'digest'}, 'attachment source')
                        if source['kind'] != 'file':
                            raise DomainError('attachment source.kind: provide file')
                        FileArtifactSource.parse(source)
        except DomainError as exc:
            raise DomainError(f'{exc}; correct the task_feedback request') from exc
        return cls(deepcopy(raw))

    @property
    def task_id(self):
        return self.value['task_id']

    @property
    def request_id(self):
        return self.value['request_id']

    def digest(self):
        return hashlib.sha256(json.dumps(self.value, sort_keys=True,
            ensure_ascii=False, separators=(',', ':')).encode('utf-8')).hexdigest()

    def namespace(self):
        return uuid.uuid5(uuid.NAMESPACE_URL,
                         json.dumps([self.task_id, self.request_id])).hex

    def result(self, actor, at, records):
        comments = []
        position = 0
        for index, item in enumerate(self.value['items']):
            attachments = []
            for source in item['attachments']:
                record = records[position]
                attachments.append({key: record[key] for key in ('id', 'digest', 'size')}
                                   | {'name': source['name']})
                position += 1
            comments.append({'id': str(uuid.uuid5(uuid.NAMESPACE_URL,
                            f'{self.namespace()}:comment:{index}')),
                'text': item['text'], 'author': actor, 'created_at': at,
                'attachments': attachments})
        return {'status': 'feedback_appended', 'task_id': self.task_id,
                'request_id': self.request_id, 'comments': comments}


def require_appendable(status):
    if is_terminal_task_status(status):
        raise DomainError('Task is terminal and immutable; read its materials or create a new Task')


def require_same_request(saved, intent):
    if saved is not None and saved['digest'] != intent.digest():
        raise DomainError('Task material request conflict; retry the original request or use a new request_id')


def task_material_view(header, content, results):
    metadata = header['metadata']
    newborn = header['status'] == 'newborn'
    contract = metadata['newborn']['draft'] if newborn else metadata['contract']
    stage = None if newborn else metadata['workflow']['stages'][header['stage_index']]['id']
    return {'id': header['id'], 'status': header['status'],
            'goal': contract.get('goal', metadata.get('goal', '')),
            'requirements': contract.get('requirements', []),
            'definition_of_done': contract.get('definition_of_done', []),
            'stage': stage, 'content': content, 'results': results}
