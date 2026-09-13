"""Pure shape validation shared by domain and interface contracts."""
from .errors import PoiseError


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
