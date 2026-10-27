"""Public values and failures, without filesystem or Task dependencies."""
import math


class Problem(Exception):
    def __init__(self, code, details, action, status='rejected', result=None):
        super().__init__(code)
        self.error = {'code': code, 'details': details, 'action': action}
        self.status, self.result = status, result


def invalid(path):
    raise Problem('invalid_request', {'path': path}, 'correct:request')


def exact_fields(value, fields, path):
    if not isinstance(value, dict):
        invalid(path)
    missing = sorted(fields - value.keys())
    if missing:
        invalid(path.rstrip('/') + '/' + missing[0] if len(missing) == 1 else path or '/')
    if value.keys() - fields:
        invalid(path or '/')
    return value


def text(value, path):
    if not isinstance(value, str) or not value or '\0' in value:
        invalid(path)
    return value


def string_list(value, path, *, unique=True):
    if not isinstance(value, list):
        invalid(path)
    for item in value:
        text(item, path)
    if unique and len(set(value)) != len(value):
        invalid(path)
    return value


def positive_count(value):
    return type(value) is int and value > 0


def positive_duration(value):
    if type(value) not in (int, float):
        return False
    try:
        return value > 0 and math.isfinite(value)
    except OverflowError:
        return False


def configuration_fields(value, fields, label, error_type=ValueError):
    if not isinstance(value, dict) or not fields <= value.keys():
        raise error_type(f'{label}: required known fields missing')
    return {key: value[key] for key in fields}


def validate_exact_keys(value, fields, label, error_type=ValueError):
    if not isinstance(value, dict) or set(value) != fields:
        raise error_type(f'{label}: exact fields required')


def argv(value, path):
    if not isinstance(value, list) or not value:
        invalid(path)
    for item in value:
        text(item, path)
    return value


def environment(value, path):
    if not isinstance(value, dict):
        invalid(path)
    for key, item in value.items():
        text(key, path)
        if '=' in key or not isinstance(item, str) or '\0' in item:
            invalid(path)
    return value
