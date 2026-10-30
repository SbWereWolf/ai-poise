"""Coordinate current check ownership; process and result owners remain separate."""
from copy import deepcopy

from ..modules.foundation.errors import PoiseError
from ..modules.project_checks.contracts import CheckFailure
from ..modules.project_checks.mcp import envelope


class CheckInvocations:
    def __init__(self, store, caller, cancellation_factory):
        self.store, self.caller = store, caller
        self.cancellation_factory = cancellation_factory

    def prepare(self, request, transport, *, source=None):
        return self.store.allocate(self.caller, request, transport, source=source)

    def confirm(self, request, transport, identity=None):
        if identity is None:
            value = self.prepare(request, transport)
        else:
            try:
                value = self.store.read(identity)
                valid = (value['caller_key'] == self.caller.key and value['request'] == request
                         and value['transport'] == transport and value['state'] == 'prepared'
                         and value['checkout'] == str(self.store.configuration.bound.checkout)
                         and value['policy_sha256'] == self.store.configuration.policy_sha256
                         and self.store.source_admission(value) == 'allowed')
            except (OSError, ValueError, KeyError, PoiseError) as exc:
                raise self.unconfirmed() from exc
            if not valid:
                raise self.unconfirmed()
        self.store.update(value['id'], lambda row: row.update(state='running'))
        return value['id'], self.cancellation_factory(self.store, value['id'])

    @staticmethod
    def unconfirmed():
        return CheckFailure('invocation_unconfirmed', '_invocation',
                            'Подготовленный вызов не соответствует вызывающему, проекту, рабочей копии или запросу.',
                            'Используйте неизменённый запрос соответствующего PreToolUse или новый самостоятельный вызов.')

    def finish(self, identity, result):
        def change(value):
            value['state'] = 'terminal'
            value['outcome'] = result['status']
            value['references']['response_path'] = result['response_path']
            # A known cancelled-before-spawn execution has no child or capture to clean.
            if not value['run_ids'] or (result['status'] == 'cancelled' and not result['items']):
                value['termination'].update(process_group_stopped=True, child_reaped=True, capture_complete=True)
        return self.store.update(identity, change)

    def request_cancel(self, identity, preview, reason):
        try:
            if identity is None:
                owned = [v for v in self.store.owned(self.caller) if v['state'] in ('prepared', 'running')]
                if not owned:
                    return self.cancel_result('not_active')
                if len(owned) > 1:
                    return self.cancel_result('ambiguous')
                value = owned[0]
            else:
                value = self.store.read(identity)
            if value['caller_key'] != self.caller.key or value['checkout'] != str(self.store.configuration.bound.checkout):
                return self.cancel_result('denied')
            if value['state'] in ('prepared', 'not_active'):
                admitted = self.store.source_admission(value)
                if admitted == 'unknown':
                    return self.cancel_result('unknown', value)
                return self.cancel_result('not_active', value)
            if value['state'] == 'terminal':
                return self.cancel_result('complete', value)
            if preview:
                return self.cancel_result('planned', value)
            self.store.request_cancel(value['id'], reason)
            return None, value['id']
        except (OSError, ValueError, KeyError, PoiseError):
            return self.cancel_result('unknown')

    def observe_cancel(self, identity):
        try:
            value = self.store.read(identity)
        except (OSError, ValueError, KeyError, PoiseError):
            return self.cancel_result('unknown')
        if value['state'] != 'terminal':
            return None
        facts = value['termination']
        status = 'cancelled' if value['outcome'] == 'cancelled' else 'complete'
        if not all(facts[k] is True for k in ('process_group_stopped', 'child_reaped', 'capture_complete')):
            status = 'incomplete'
        return self.cancel_result(status, value)

    @staticmethod
    def cancel_result(status, value=None):
        default_facts = {'graceful_requested': False, 'force_requested': False,
                         'process_group_stopped': None, 'child_reaped': None, 'capture_complete': None}
        refs = {'response_path': None, 'record_paths': [], 'stdout_paths': [], 'stderr_paths': []}
        body = {'schema': 'poise-mcp-check-cancel-result-1', 'status': status,
                'target': None if value is None else {'invocation_id': value['id'], 'project': value['project'],
                    'checkout': value['checkout'], 'run_ids': list(value['run_ids'])},
                'termination': default_facts if value is None else deepcopy(value['termination']),
                'references': refs if value is None else deepcopy(value['references']), 'error': None}
        if status in ('unknown', 'denied', 'ambiguous', 'unavailable', 'invalid', 'incomplete'):
            body['error'] = {'code': status, 'field': 'invocation_id', 'cause': 'Текущее состояние отмены: ' + status,
                             'reason': 'Отмена не подтверждена.',
                             'recovery': 'Прочитайте сохранённый результат либо укажите точный собственный invocation_id.',
                             'references': deepcopy(body['references'])}
        return envelope(body, status in ('unknown', 'denied', 'ambiguous', 'unavailable', 'invalid', 'incomplete'))
