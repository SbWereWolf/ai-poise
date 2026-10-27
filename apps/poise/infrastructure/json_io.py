"""Shared existing UTF-8 JSON decoder, independent of either CLI."""
import json
import math


def unique_pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def finite_float(text):
    value = float(text)
    if not math.isfinite(value):
        raise ValueError("Nonfinite JSON number")
    return value


def nonfinite_constant(text):
    raise ValueError("Nonfinite JSON constant")


def loads(raw: bytes):
    value = json.loads(
        raw.decode("utf-8"), object_pairs_hook=unique_pairs,
        parse_float=finite_float, parse_constant=nonfinite_constant,
    )
    # Escaped lone surrogates also cannot represent UTF-8 text on this boundary.
    json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
    return value
