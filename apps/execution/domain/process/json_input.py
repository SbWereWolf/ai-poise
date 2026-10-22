"""Pure strict JSON parsing shared by public providers."""
import json

from .errors import PoiseError


def strict_json(text):
    def pairs(values):
        result={}
        for key, value in values:
            if key in result:
                raise PoiseError(f"Повтор поля JSON: {key}")
            result[key]=value
        return result
    def invalid_constant(value):
        raise PoiseError(f"Недопустимое JSON-число: {value}")
    try:
        return json.loads(text, object_pairs_hook=pairs, parse_constant=invalid_constant)
    except (ValueError, TypeError, UnicodeError) as exc:
        raise PoiseError(f"Неверный JSON: {exc}") from exc
