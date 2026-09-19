"""Translate a Task-family admission refusal at the runtime boundary."""
from functools import wraps
from ..modules.tasks.duplicates import DuplicateStartBlocked


def duplicate_admission(method):
    @wraps(method)
    def invoke(runtime, *args, **kwargs):
        try:
            return method(runtime, *args, **kwargs)
        except DuplicateStartBlocked as exc:
            result = {'status': 'duplicate_start_blocked', 'session': runtime.session,
                      'duplicate_gate': exc.decision, 'result_template': None,
                      'next_work': 'Согласовать исполнителя ближайших задач; отставшую работу не начинать'}
            if exc.decision['reason'] == 'family_ahead' and exc.decision['minimum'] == 0:
                result.update(
                    next_work='Получить указанный коммит и проверить требования через reuse; '
                              'при локальной неудаче — task/restart и доработка в своей ветке.',
                    recovery={'action': 'import_then_verify',
                              'on_verification_failure': 'restart'})
            return result
    return invoke
