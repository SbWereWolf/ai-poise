"""Coordinate one delivery without taking ownership of Task or external chat."""
from ..modules.agent_handoff.domain import ProxyFailure, ProxyState, assess_handoff
from ..modules.agent_handoff.ports import HandoffPort, NotificationPort, ProxyJournal


class HandoffProxyCommands:
    def __init__(self, project, caller, journal: ProxyJournal, native: HandoffPort, notifier: NotificationPort):
        self.project, self.caller = project, caller
        self.journal, self.native, self.notifier = journal, native, notifier
        self.state = None

    def execute(self, request):
        with self.journal.locked():
            self.state = self.journal.load()
            if self.state is not None:
                self.state.require_identity(self.caller, request)
            elif request.operation != 'deliver':
                raise ProxyFailure('invalid_input', 'Не найдена исходная передача для разрешения.',
                                   'Выберите существующий request_id неизвестного уведомления.')
            if self.state is not None and self.state.status == 'delivered':
                if request.operation == 'resolve_notification' and request.value not in self.state.resolution_inputs:
                    raise ProxyFailure('invalid_input', 'Доставка уже подтверждена.',
                                       'Используйте сохранённый результат без повторного уведомления.')
                return self.journal.save(self.state, replayed=True)
            try:
                if request.operation == 'resolve_notification':
                    return self._resolve(request)
                return self._deliver(request)
            finally:
                self.notifier.close()

    def _deliver(self, request):
        if self.state is not None and self.state.send_started:
            self.state.mark_unknown('Предыдущая отправка не имеет сохранённого подтверждения.')
            return self.journal.save(self.state)
        if self.state is not None and self.state.notification and self.state.notification['status'] == 'unknown':
            return self.journal.save(self.state)
        native_needed = (self.state is None or self.state.handoff is None
                         or assess_handoff(self.state.handoff, self.state.intent) is not None)
        if native_needed:
            self.native.prepare()
        if self.state is None:
            self.notifier.prepare()
            self.state = ProxyState(self.caller, self.project, request.request_id, request.value)
            self.journal.save(self.state)
        if native_needed:
            self.state.handoff = self.native.run(self.state.intent['handoff'])
            failure = assess_handoff(self.state.handoff, self.state.intent)
            if failure:
                self.state.fail(failure)
                return self.journal.save(self.state)
            self.state.status, self.state.phase, self.state.error = 'pending', 'notification', None
            self.journal.save(self.state)
        self._prepare_notification()
        return self._notify()

    def _notify(self):
        self.state.begin_notification()
        self.journal.save(self.state)
        try:
            outcome = self.notifier.send(self.state.intent['recipient'], self.state.prompt(), self.caller)
        except ProxyFailure as exc:
            self.state.notification.update(exc.details)
            self.state.mark_unknown(str(exc))
            return self.journal.save(self.state)
        self.state.confirm_notification(outcome['response'])
        self.state.notification['transport'] = outcome['transport']
        return self.journal.save(self.state)

    def _prepare_notification(self):
        try:
            self.notifier.prepare()
        except ProxyFailure as exc:
            if self.state is not None and self.state.handoff is not None:
                exc.phase = 'notification'
                self.state.fail(exc)
                self.journal.save(self.state)
            raise

    def _resolve(self, request):
        if self.state.send_started:
            self.state.mark_unknown('Исход предыдущей отправки не подтверждён.')
        if not self.state.resolve(request.value):
            if self.state.notification['status'] == 'pending' and not self.state.send_started:
                self._prepare_notification()
                return self._notify()
            return self.journal.save(self.state, replayed=True)
        self.journal.save(self.state)
        if self.state.status == 'delivered':
            return self.journal.save(self.state)
        self._prepare_notification()
        return self._notify()
