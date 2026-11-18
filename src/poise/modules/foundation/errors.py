class PoiseError(Exception):
    """Ожидаемая ошибка контракта, среды или процесса Poise."""


class DomainError(PoiseError):
    """Отклонённая операция предметной модели; не обязательно баг Poise."""


class TaskActionIntentConflict(PoiseError):
    """A Task action request ID already belongs to a different intent."""


class VersionConflict(PoiseError):
    """Сохранение не может перезаписать более новую версию."""


class LockUnavailable(PoiseError):
    """The configured external lock acquisition wait was exhausted."""


class SQLiteBoundaryUnavailable(PoiseError):
    """The configured SQLITE_BUSY wait at BEGIN/COMMIT was exhausted."""
