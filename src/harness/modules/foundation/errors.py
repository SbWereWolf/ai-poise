class HarnessError(Exception):
    """Ожидаемая ошибка контракта, среды или процесса Harness."""


class DomainError(HarnessError):
    """Отклонённая операция предметной модели; не обязательно баг Harness."""


class VersionConflict(HarnessError):
    """Сохранение не может перезаписать более новую версию."""
