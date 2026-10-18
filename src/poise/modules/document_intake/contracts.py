"""Strict public request and stable failure contract for document reading."""
from .catalog import ERRORS
from .domain import Selection

OPERATIONS = {'prepare_template', 'prepare', 'read', 'context', 'advance_context', 'recover', 'adopt', 'coverage'}


class ReadFailure(Exception):
    def __init__(self, code, key=None, field=None, *, details=None, status=None):
        self.code, self.key, self.details = code, key, details
        spec = ERRORS[code]
        self.field = field if field is not None else spec['field'].replace('@KEY@', key or '')
        self.exit = spec['single_failure_exit']
        self.status = status or ('rejected' if self.exit == 2 else 'failed')
        if code in ('result_save_failed', 'template_publication_unconfirmed'):
            self.status = 'unconfirmed'
        super().__init__(code)

    def as_dict(self):
        spec = ERRORS[self.code]
        return dict(code=self.code, key=self.key, field=self.field,
                    message=spec['message'], action=spec['action'])


class ContentReadRequest:
    @staticmethod
    def parse(value, max_items):
        if (not isinstance(value, dict) or not isinstance(value.get('operation'), str)
                or value['operation'] not in OPERATIONS):
            raise ReadFailure('invalid_request')
        op = value['operation']
        fields = {'operation'} | ({'reads'} if op == 'read' else
                                 {'request_id', 'reason'} if op == 'advance_context' else set())
        if set(value) != fields:
            raise ReadFailure('invalid_request')
        if op == 'advance_context' and (not isinstance(value['request_id'], str)
                or not value['request_id'].strip() or not isinstance(value['reason'], str) or not value['reason'].strip()):
            raise ReadFailure('invalid_request')
        if op != 'read':
            return value
        reads = value['reads']
        if not isinstance(reads, list) or not 1 <= len(reads) <= max_items:
            raise ReadFailure('invalid_request')
        keys = set()
        for item in reads:
            if not isinstance(item, dict) or set(item) != {'key', 'path', 'selection'}:
                raise ReadFailure('invalid_request')
            key = item['key']
            if not isinstance(key, str) or not key.strip() or key in keys:
                raise ReadFailure('invalid_request')
            keys.add(key)
            if not isinstance(item['path'], str) or not item['path'].strip() or '\x00' in item['path']:
                raise ReadFailure('invalid_path', key)
            try:
                Selection.parse(item['selection'])
            except ValueError as exc:
                raise ReadFailure('invalid_selection', key) from exc
        return value
