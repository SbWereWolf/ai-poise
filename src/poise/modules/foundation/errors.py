class PoiseError(Exception):
    """Ожидаемая ошибка контракта, среды или процесса Poise."""


class DomainError(PoiseError):
    """Отклонённая операция предметной модели; не обязательно баг Poise."""


class VersionConflict(PoiseError):
    """Сохранение не может перезаписать более новую версию."""
