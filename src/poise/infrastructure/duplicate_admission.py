"""Translate a Task-family admission refusal at the runtime boundary."""
from functools import wraps
from ..modules.tasks.duplicates import DuplicateStartBlocked


def duplicate_admission(method):
    @wraps(method)
    def invoke(runtime, *args, **kwargs):
        try:
            return method(runtime, *args, **kwargs)
        except DuplicateStartBlocked as exc:
            return {'status': 'duplicate_start_blocked', 'session': runtime.session,
                    'duplicate_gate': exc.decision, 'result_template': None,
                    'next_work': 'Согласовать исполнителя ближайших задач; отставшую работу не начинать'}
    return invoke
