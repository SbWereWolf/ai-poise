"""Pure shape validation shared by domain and interface contracts."""
from .errors import PoiseError


def configuration_fields(value, keys, where, error_type=PoiseError):
    """Select supported configuration options, without weakening packet schemas."""
    if not isinstance(value, dict):
        raise error_type(f'{where}: ожидается объект')
    missing = set(keys) - value.keys()
    if missing:
        raise error_type(f'{where}: отсутствуют обязательные поля {sorted(missing)}')
    return {key: value[key] for key in keys}


def validate_exact_keys(
    value: object,
    keys: set[str],
    where: str,
    error_type: type[PoiseError],
) -> None:
    if not isinstance(value, dict):
        raise error_type(f'{where}: ожидается объект')
    missing = keys - value.keys()
    extra = value.keys() - keys
    if missing or extra:
        raise error_type(
            f'{where}: требуется точный набор полей; '
            f'отсутствуют {sorted(missing)}; неизвестные поля {sorted(extra)}'
        )
