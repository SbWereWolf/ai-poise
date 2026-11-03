"""Intent, confirmed outcomes and recovery of one cross-tool delivery, without IO."""
from copy import deepcopy
from dataclasses import asdict, dataclass, field
import json

from ..foundation.errors import PoiseError
from ..tasks.handoff import normal_progression, validate_handoff_input


class ProxyFailure(PoiseError):
    def __init__(self, code, message, continuation, *, status='blocked', phase='admission', details=None):
        super().__init__(message)
        self.code, self.continuation = code, continuation
        self.status, self.phase = status, phase
        self.details = {} if details is None else details

    def error(self):
        return {'code': self.code, 'message': str(self), 'continuation': self.continuation}


def required_text(value):
    return isinstance(value, str) and bool(value.strip())


@dataclass(frozen=True)
class ProxyRequest:
    operation: str
    value: dict
    request_id: str

    @classmethod
    def parse(cls, packet):
        try:
            if not isinstance(packet, dict) or set(packet) != {'operation', 'input'}:
                raise ValueError('Expected operation and input')
            operation, value = packet['operation'], packet['input']
            if not isinstance(value, dict):
                raise ValueError('input must be an object')
            if operation == 'deliver':
                if set(value) != {'handoff', 'recipient', 'message'}:
                    raise ValueError('deliver requires handoff, recipient and message')
                native = value['handoff']
                if (not isinstance(native, dict) or set(native) != {'operation', 'input', 'messages'}
                        or native['operation'] != 'handoff' or native['messages'] != []):
                    raise ValueError('Use a complete native handoff packet with messages=[]')
                validate_handoff_input(native['input'])
                recipient = value['recipient']
                if (not isinstance(recipient, dict) or 'thread_id' not in recipient
                        or set(recipient) - {'thread_id', 'host_id'}
                        or any(not required_text(item) for item in recipient.values())):
                    raise ValueError('An explicit recipient.thread_id and valid optional host_id are required')
                if not required_text(value['message']):
                    raise ValueError('A substantive message is required')
                request_id = native['input']['request_id']
            elif operation == 'resolve_notification':
                if (set(value) != {'request_id', 'outcome', 'evidence'}
                        or not required_text(value['request_id'])
                        or value['outcome'] not in ('delivered', 'not_delivered')
                        or not required_text(value['evidence'])):
                    raise ValueError('Resolution requires request_id, delivered/not_delivered and external evidence')
                request_id = value['request_id']
            else:
                raise ValueError('Use deliver or resolve_notification')
        except (ValueError, PoiseError, TypeError) as exc:
            raise ProxyFailure('invalid_input', str(exc),
                               'Исправьте исходный пакет по договору handoff-proxy до повторного вызова.') from exc
        return cls(operation, deepcopy(value), request_id)


def assess_handoff(handoff, intent):
    native = handoff['result']
    task, request = intent['handoff']['input']['task_id'], intent['handoff']['input']['request_id']
    replay = f'Восстановите исходный handoff request_id={request} через его владельца; сохраните полученные квитанции.'
    if handoff['process']['exit_code'] != 0:
        return ProxyFailure('handoff_process_failed', 'Native процесс завершился с ошибкой; полный ответ сохранён.',
                            replay, status='unknown', phase='handoff')
    if handoff['process'].get('capture_complete') is False:
        return ProxyFailure('handoff_unconfirmed', 'Полный native вывод ещё не подтверждён.', replay,
                            status='unknown', phase='handoff')
    if not isinstance(native, dict):
        return ProxyFailure('handoff_unconfirmed', 'Полный пригодный native ответ недоступен.', replay,
                            status='unknown', phase='handoff')
    release = native.get('release')
    if (native.get('task') != task or native.get('request_id') != request
            or isinstance(release, dict) and release.get('task') != task):
        return ProxyFailure('handoff_receipt_mismatch', 'Квитанция относится к другому Task или request.',
                            f'Выполните show для Task {task} через исходного владельца handoff.',
                            status='unknown', phase='handoff')
    if native.get('status') == 'handoff_blocked':
        blocked = native.get('blocked')
        if (not isinstance(blocked, dict) or not isinstance(blocked.get('reason'), str)
                or not required_text(blocked.get('continuation'))):
            return ProxyFailure('handoff_unconfirmed', 'Native blocked имеет непригодную форму; полный ответ сохранён.',
                                replay, status='unknown', phase='handoff')
        return ProxyFailure('handoff_blocked', blocked.get('reason') or 'Native handoff остановлен.',
                            blocked['continuation'], phase='handoff')
    verification = native.get('verification')
    if not isinstance(verification, dict) or verification.get('status') != 'verified':
        return ProxyFailure('handoff_not_verified', 'Результат не прошёл native verification.',
                            f'Выполните согласованный rework Task {task} через владельца результата.', phase='handoff')
    before = release.get('before') if isinstance(release, dict) else None
    after = release.get('after') if isinstance(release, dict) else None
    independent_retained = (isinstance(before, dict) and isinstance(after, dict)
                            and required_text(after.get('worktree_task_id'))
                            and after['worktree_task_id'] != task
                            and before.get('worktree_task_id') == after['worktree_task_id'])
    if (not isinstance(release, dict) or release.get('status') != 'ownership_released'
            or not isinstance(after, dict) or not {'task_id', 'worktree_task_id'}.issubset(after)
            or after['task_id'] is not None
            or after['worktree_task_id'] is not None and not independent_retained):
        return ProxyFailure('handoff_not_released', 'Освобождение владения не подтверждено.',
                            f'Установите фактическое владение и завершите release Task {task} через его владельца.',
                            phase='handoff')
    try:
        normal = normal_progression(native.get('progression'))
    except (KeyError, TypeError):
        normal = False
    if (native.get('status') != 'handoff_completed' or native.get('blocked') is not None or not normal
            or not required_text(verification.get('verification_commit'))
            or not required_text(verification.get('verified_tree'))):
        return ProxyFailure('handoff_unconfirmed', 'Полный ответ не подтверждает завершённую передачу.',
                            replay, status='unknown', phase='handoff')
    return None


def message_context(project, intent, handoff):
    native = handoff['result']
    verification = native['verification']
    return {'project': project, 'task': native['task'], 'request_id': native['request_id'],
            'response_path': handoff['response_path'], 'commit': verification['verification_commit'],
            'tree': verification['verified_tree'], 'progression': deepcopy(native['progression'])}


@dataclass
class ProxyState:
    caller: str
    project: str
    request_id: str
    intent: dict
    handoff: dict | None = None
    notification: dict | None = None
    send_started: bool = False
    resolution_inputs: list = field(default_factory=list)
    status: str = 'pending'
    phase: str = 'handoff'
    error: dict | None = None
    schema: str = 'poise-agent-handoff-proxy-state-1'

    def require_identity(self, caller, request):
        if self.caller != caller:
            raise ProxyFailure('caller_conflict', 'Эта передача принадлежит другому native caller.',
                               'Продолжите исходным native caller; не подменяйте session identity.')
        if request.operation == 'deliver' and self.intent != request.value:
            raise ProxyFailure('intent_conflict', 'Тот же request_id содержит другой результат, сообщение или получателя.',
                               'Сохраните исходный intent; для нового результата используйте новый request_id.')

    def fail(self, failure):
        self.status, self.phase, self.error = failure.status, failure.phase, failure.error()

    def mark_unknown(self, reason):
        self.notification = {**(self.notification or {}), 'status': 'unknown'}
        self.fail(ProxyFailure('notification_unconfirmed', reason,
                              f'Проверьте доставку у назначенного получателя; затем resolve_notification '
                              f'request_id={self.request_id} с outcome и внешним evidence.',
                              status='unknown', phase='notification'))

    def begin_notification(self):
        self.notification = {**(self.notification or {}), 'status': 'pending',
                             'recipient': deepcopy(self.intent['recipient']), 'response': None}
        self.send_started = True
        self.mark_unknown('Отправка начата; подтверждение ещё не сохранено.')

    def confirm_notification(self, response):
        self.notification = {**self.notification, 'status': 'confirmed', 'response': deepcopy(response)}
        self.send_started = False
        self.status, self.phase, self.error = 'delivered', 'complete', None

    def resolve(self, value):
        if value in self.resolution_inputs:
            return False
        if self.status == 'delivered' or not self.notification or self.notification['status'] != 'unknown':
            raise ProxyFailure('invalid_input', 'Разрешение применимо только к неизвестному уведомлению.',
                               'Прочитайте сохранённый исход; не отменяйте подтверждённую доставку.')
        self.resolution_inputs.append(deepcopy(value))
        self.notification['resolution'] = deepcopy(value)
        self.send_started = False
        if value['outcome'] == 'delivered':
            self.confirm_notification(None)
        else:
            self.notification['status'] = 'pending'
            self.status, self.phase, self.error = 'pending', 'notification', None
        return True

    def to_dict(self):
        return asdict(self)

    def project_result(self, path, *, replayed=False):
        return deepcopy({'schema': 'poise-agent-handoff-proxy-result-1', 'status': self.status,
                         'phase': self.phase, 'request_id': self.request_id,
                         'task': self.intent['handoff']['input']['task_id'], 'handoff': self.handoff,
                         'notification': self.notification, 'error': self.error,
                         'replayed': replayed, 'response_path': path})

    def prompt(self):
        context = message_context(self.project, self.intent, self.handoff)
        return self.intent['message'] + '\n\nКонтекст передачи:\n' + json.dumps(context, ensure_ascii=False, sort_keys=True)
