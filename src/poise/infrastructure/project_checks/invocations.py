"""Atomic current invocation coordination in the named-check ledger's state."""
from contextlib import contextmanager
from copy import deepcopy
import json
from pathlib import Path
import uuid

from ...modules.project_checks.contracts import CheckFailure, UnstartedCheck, require
from ..goal_config import atomic_write, strict_json
from ..locking import exclusive_lock


def unconfirmed(cause):
    return CheckFailure('invocation_unconfirmed', '_invocation', cause,
                        'Передайте исходный запрос из соответствующего PreToolUse или начните самостоятельный вызов.')


class FileCheckInvocationStore:
    def __init__(self, ledger, policy):
        self.ledger, self.policy = ledger, policy
        self.configuration = ledger.configuration
        self.root = self.configuration.state / policy['invocation']['directory']
        self.names = policy['invocation']

    def folder(self, identity):
        try:
            require(uuid.UUID(identity).hex == identity, '_invocation', 'Неверный UUID.')
        except (ValueError, TypeError, AttributeError, CheckFailure) as exc:
            raise unconfirmed('Неверный адрес текущего вызова.') from exc
        return self.root / identity

    def path(self, identity):
        return self.folder(identity) / self.names['record_file']

    @contextmanager
    def locked(self, identity):
        limits = self.configuration.bound.config['limits']
        with exclusive_lock(self.folder(identity) / self.names['lock_file'],
                            limits['lock_seconds'], limits['lock_poll_seconds']):
            yield

    def write(self, value):
        atomic_write(self.path(value['id']),
                     (json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2) + '\n').encode(),
                     self.ledger.file_mode)

    def read(self, identity):
        value = strict_json(self.path(identity).read_text())
        require(isinstance(value, dict) and value.get('schema') == 'poise-check-invocation-1'
                and value.get('id') == identity and value.get('project') == self.ledger.project,
                '_invocation', 'Не подтверждены сохранённые метаданные вызова.')
        for key in ('caller_key', 'checkout', 'state', 'request', 'transport', 'references', 'termination', 'outcome'):
            require(key in value, '_invocation', 'Неполные метаданные вызова.')
        require(value['state'] in ('prepared', 'running', 'terminal', 'not_active'),
                '_invocation', 'Неизвестное состояние вызова.')
        require(isinstance(value['references'], dict) and isinstance(value['termination'], dict),
                '_invocation', 'Непригодные факты результата.')
        require(value['outcome'] is None if value['state'] != 'terminal' else
                isinstance(value['outcome'], str) and bool(value['outcome']),
                '_invocation', 'Не подтверждён фактический конечный исход вызова.')
        return value

    def allocate(self, caller, request, transport, *, source=None):
        identity = uuid.uuid4().hex
        value = {'schema': 'poise-check-invocation-1', 'id': identity, 'project': self.ledger.project,
                 'checkout': str(self.configuration.bound.checkout), 'caller_key': caller.key,
                 'request': deepcopy(request), 'transport': transport,
                 'checks_policy_path': str(self.configuration.policy_path),
                 'policy_sha256': self.configuration.policy_sha256,
                 'source': deepcopy(source), 'state': 'prepared', 'outcome': None, 'run_ids': [],
                 'references': {'response_path': None, 'record_paths': [], 'stdout_paths': [], 'stderr_paths': []},
                 'termination': {'graceful_requested': False, 'force_requested': False,
                                 'process_group_stopped': None, 'child_reaped': None, 'capture_complete': None}}
        with self.locked(identity):
            self.write(value)
        return value

    def update(self, identity, change):
        with self.locked(identity):
            value = self.read(identity)
            change(value)
            self.write(value)
            return value

    def owned(self, caller):
        values = []
        if self.root.exists():
            for folder in self.root.iterdir():
                if not folder.is_dir():
                    continue
                # Invalid saved state is not an empty set of active invocations.
                value = self.read(folder.name)
                if value['caller_key'] == caller.key and value['checkout'] == str(self.configuration.bound.checkout):
                    values.append(value)
        return values

    def request_cancel(self, identity, reason):
        with self.locked(identity):
            value = self.read(identity)
            if value['state'] not in ('prepared', 'running'):
                return value
            path = self.folder(identity) / self.names['cancel_file']
            if not path.exists():
                atomic_write(path, (json.dumps({'schema': 'poise-check-cancel-1', 'id': identity,
                             'caller_key': value['caller_key'], 'reason': reason}, ensure_ascii=False) + '\n').encode(),
                             self.ledger.file_mode)
            return value

    def cancellation_requested(self, identity):
        path = self.folder(identity) / self.names['cancel_file']
        if not path.exists():
            return False
        value = self.read(identity)
        request = strict_json(path.read_text())
        require(request.get('schema') == 'poise-check-cancel-1' and request.get('id') == identity
                and request.get('caller_key') == value['caller_key'], '_invocation', 'Не подтверждён запрос отмены.')
        return True

    def source_admission(self, value):
        source = value['source']
        if source is None:
            return 'allowed'
        from ..hook_dispatch import FileDispatchDeliveryStore
        # read() is the existing delivery owner; it does not need a writer settings object.
        saved = FileDispatchDeliveryStore(None).read(source['delivery_path'])
        require(all(saved.get(k) == source[k] for k in ('event_receipt', 'definition_path', 'settings_path')),
                '_invocation', 'Источник подготовки не соответствует сохранённой доставке.')
        if not saved.get('chain_complete') or saved.get('chain_failed'):
            return 'unknown'
        denied = saved.get('response', {}).get('hookSpecificOutput', {}).get('permissionDecision') == 'deny'
        return 'denied' if denied else 'allowed'


class InvocationCancellation:
    """A mailbox observer; signals are exclusively the runner's responsibility."""
    def __init__(self, store, identity):
        self.store, self.identity = store, identity
        self.policy = store.policy['cancellation']

    def requested(self):
        return self.store.cancellation_requested(self.identity)

    def before_step(self):
        if self.requested():
            raise UnstartedCheck('cancelled', 'invocation', 'Запрошена отмена до следующего запуска.',
                                 'Прочитайте полный результат и состояние очистки.', status='cancelled')

    def allocated(self, addresses):
        def change(value):
            value['run_ids'].append(addresses['id'])
            for name, address in (('record_paths', addresses['record_path']),
                                  ('stdout_paths', addresses['stdout']), ('stderr_paths', addresses['stderr'])):
                value['references'][name].append(str(address))
        self.store.update(self.identity, change)

    def observed(self, raw):
        def change(value):
            facts = value['termination']
            facts['graceful_requested'] |= raw.get('graceful_requested', False)
            facts['force_requested'] |= raw.get('force_requested', False)
            for key in ('process_group_stopped', 'child_reaped', 'capture_complete'):
                current = raw['termination'][key]
                facts[key] = current if facts[key] is None else facts[key] and current
        self.store.update(self.identity, change)
